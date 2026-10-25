"""Persistent, single-worker scheduling of non-interactive position fetches."""
import json
import threading
import time
from pathlib import Path
from device_selection import DeviceSelection

INTERVALS = (5, 15, 30, 60, 180, 360, 720, 1440)


class AutoRefresh:
    def __init__(self, data, job, command, clock=time.time):
        self.data = Path(data)
        self.path = self.data / 'auto-refresh.json'
        self.job, self.command, self.clock = job, command, clock
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.observed = job.state()['run_id']
        self.config = {'enabled': False, 'interval_minutes': 15, 'next_run': None,
                       'last_run': None, 'paused_reason': None}
        if self.path.exists():
            value = json.loads(self.path.read_text())
            if type(value.get('enabled')) is not bool or value.get('interval_minutes') not in INTERVALS:
                raise ValueError('Ogiltig sparad schemainställning')
            self.config.update(value)
        if self.config['enabled'] and self.config['next_run'] is None and not self.config['paused_reason']:
            # A container restart can interrupt a running job before its completion
            # was observed. Resume with one fresh interval, without a catch-up burst.
            self.config['next_run'] = self.clock() + self.config['interval_minutes'] * 60
            self._write()

    def _write(self):
        tmp = self.path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(self.config))
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def _session(self):
        path = self.data / 'session-status.json'
        if path.exists():
            return json.loads(path.read_text())
        return {}

    def state(self):
        with self.lock:
            session = self._session()
            authenticated = session.get('authenticated_at')
            return {**self.config, 'intervals': list(INTERVALS), 'session': {
                'authenticated_at': authenticated,
                'age_seconds': max(0, self.clock() - authenticated) if authenticated else None,
                'last_fetch_at': session.get('last_fetch_at'),
                'login_required': bool(session.get('login_required')),
                'expected_expiry': None}}

    def save(self, value):
        if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
            raise ValueError('Ange om automatisk sökning ska vara aktiverad')
        interval = value.get('interval_minutes')
        if type(interval) is not int or interval not in INTERVALS:
            raise ValueError('Välj ett av de tillgängliga sökintervallen')
        with self.lock:
            self.config.update(enabled=value['enabled'], interval_minutes=interval,
                               paused_reason=None, next_run=self.clock() + interval * 60 if value['enabled'] else None)
            self._write()

    def defer(self):
        """A manual Stop must not be immediately undone by an overdue timer."""
        with self.lock:
            self.config['next_run'] = self.clock() + self.config['interval_minutes'] * 60 if self.config['enabled'] else None
            self._write()

    def devices_changed(self):
        with self.lock:
            if self.config['paused_reason'] == 'no_selection':
                self.config['paused_reason'] = None
            self.config['next_run'] = self.clock() + self.config['interval_minutes'] * 60 if self.config['enabled'] else None
            self._write()

    def tick(self):
        with self.lock:
            state = self.job.state()
            if state['running']:
                return
            now = self.clock()
            session = self._session()
            if state['run_id'] != self.observed:
                self.observed = state['run_id']
                self.config['last_run'] = state['finished_at'] or now
                if state['exit_code'] == 10 or session.get('login_required'):
                    self.config.update(paused_reason='login_required', next_run=None)
                elif state['exit_code'] == 2:
                    self.config.update(paused_reason='missing_session', next_run=None)
                elif state['exit_code'] == 20:
                    self.config.update(paused_reason='no_selection', next_run=None)
                else:
                    self.config['paused_reason'] = None
                    delay = self.config['interval_minutes'] * 60
                    if state['exit_code'] != 0:
                        delay = max(delay, 15 * 60)
                    self.config['next_run'] = now + delay if self.config['enabled'] else None
                self._write()
            if not self.config['enabled']:
                return
            if session.get('login_required'):
                if self.config['paused_reason'] != 'login_required' or self.config['next_run'] is not None:
                    self.config.update(paused_reason='login_required', next_run=None)
                    self._write()
                return
            if not (self.data / 'mac-session.enc').exists() or not (self.data / 'mac-accessories.enc').exists():
                if self.config['paused_reason'] != 'missing_session':
                    self.config.update(paused_reason='missing_session', next_run=None)
                    self._write()
                return
            if self.config['paused_reason'] in ('login_required', 'missing_session'):
                # Resume only after a manual job has completed and cleared the reason.
                return
            selection = DeviceSelection(self.data).read()
            if not selection['all_devices'] and not selection['identifiers']:
                if self.config['paused_reason'] != 'no_selection':
                    self.config.update(paused_reason='no_selection', next_run=None)
                    self._write()
                return
            if self.config['paused_reason'] == 'no_selection':
                return
            marker = self.data / 'macadi/gsa-429'
            if marker.exists():
                try:
                    until = float(marker.read_text().strip()) + 45 * 60
                except (OSError, ValueError):
                    until = marker.stat().st_mtime + 45 * 60
                if until > now:
                    next_run = max(until, self.config['next_run'] or 0)
                    if self.config['paused_reason'] != 'apple_cooldown' or self.config['next_run'] != next_run:
                        self.config.update(paused_reason='apple_cooldown', next_run=next_run)
                        self._write()
                    return
            if self.config['paused_reason'] == 'apple_cooldown':
                self.config['paused_reason'] = None
                self._write()
            if self.config['next_run'] is None or now < self.config['next_run']:
                return
            if self.job.start(self.command):
                self.config['last_run'] = now
                self.config['next_run'] = None  # reschedule after this job finishes
                self._write()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except (OSError, ValueError):
                # A temporary local storage problem must not kill the timer thread.
                pass
            self.stop_event.wait(5)

    def start(self):
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, daemon=True)
                self.thread.start()
