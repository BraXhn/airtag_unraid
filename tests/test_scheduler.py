import json
from pathlib import Path

import pytest

from scheduler import AutoRefresh


class FakeJob:
    def __init__(self):
        self.commands=[]
        self.value={'running':False,'run_id':0,'finished_at':None,'exit_code':None}
    def state(self): return dict(self.value)
    def start(self, command):
        if self.value['running']: return False
        self.commands.append(command)
        self.value.update(running=True,run_id=self.value['run_id']+1,finished_at=None,exit_code=None)
        return True
    def finish(self, code, now): self.value.update(running=False,finished_at=now,exit_code=code)


@pytest.fixture
def timer(tmp_path):
    job=FakeJob()
    now=[1000.0]
    scheduler=AutoRefresh(tmp_path,job,['python','mac_worker.py','--non-interactive'],clock=lambda:now[0])
    (tmp_path/'mac-session.enc').write_bytes(b'encrypted-placeholder')
    (tmp_path/'mac-accessories.enc').write_bytes(b'encrypted-placeholder')
    return scheduler,job,now,tmp_path


def test_disabled_by_default_and_validate_intervals(timer):
    scheduler,job,now,path=timer
    scheduler.tick()
    assert not scheduler.state()['enabled'] and not job.commands
    for value in ({'enabled':True,'interval_minutes':1},{'enabled':'yes','interval_minutes':15},
                  {'enabled':True,'interval_minutes':15.0}):
        with pytest.raises(ValueError): scheduler.save(value)
    scheduler.save({'enabled':True,'interval_minutes':30})
    restored=AutoRefresh(path,job,[],clock=lambda:now[0])
    assert restored.state()['interval_minutes']==30
    assert restored.state()['next_run']==2800


def test_no_overlap_and_interval_starts_after_completion(timer):
    scheduler,job,now,path=timer
    scheduler.save({'enabled':True,'interval_minutes':5})
    now[0]=1299;scheduler.tick();assert not job.commands
    now[0]=1300;scheduler.tick();assert len(job.commands)==1
    assert job.commands[0][-1]=='--non-interactive'
    now[0]=3000;scheduler.tick();assert len(job.commands)==1
    job.finish(0,now[0]);scheduler.tick()
    assert scheduler.state()['next_run']==3300
    now[0]=3300;scheduler.tick();assert len(job.commands)==2


def test_manual_job_delays_schedule_and_stop_defers(timer):
    scheduler,job,now,path=timer
    scheduler.save({'enabled':True,'interval_minutes':5})
    job.start(['manual'])
    now[0]=1500;scheduler.tick();assert len(job.commands)==1
    scheduler.defer()
    assert scheduler.state()['next_run']==1800
    job.finish(0,1500);scheduler.tick()
    assert scheduler.state()['next_run']==1800


def test_login_failure_pauses_until_manual_success(timer):
    scheduler,job,now,path=timer
    scheduler.save({'enabled':True,'interval_minutes':5})
    now[0]=1300;scheduler.tick()
    (path/'session-status.json').write_text(json.dumps({'login_required':True}))
    job.finish(10,1301);now[0]=1301;scheduler.tick()
    assert scheduler.state()['paused_reason']=='login_required'
    now[0]=100000;scheduler.tick();assert len(job.commands)==1
    job.start(['manual-relogin'])
    (path/'session-status.json').write_text(json.dumps({'login_required':False,'authenticated_at':now[0]}))
    job.finish(0,now[0]);scheduler.tick()
    assert scheduler.state()['paused_reason'] is None
    assert scheduler.state()['next_run']==now[0]+300


def test_missing_session_does_not_start_interactive_job(timer):
    scheduler,job,now,path=timer
    (path/'mac-session.enc').unlink()
    scheduler.save({'enabled':True,'interval_minutes':5})
    now[0]=1400;scheduler.tick()
    assert not job.commands and scheduler.state()['paused_reason']=='missing_session'


def test_network_backoff_and_apple_cooldown(timer):
    scheduler,job,now,path=timer
    scheduler.save({'enabled':True,'interval_minutes':5})
    now[0]=1300;scheduler.tick();job.finish(1,1301);now[0]=1301;scheduler.tick()
    assert scheduler.state()['next_run']==2201
    marker=path/'macadi/gsa-429';marker.parent.mkdir();marker.write_text('1300')
    now[0]=2201;scheduler.tick()
    assert scheduler.state()['paused_reason']=='apple_cooldown'
    assert scheduler.state()['next_run']==4000
    now[0]=4000;scheduler.tick()
    assert len(job.commands)==2 and scheduler.state()['paused_reason'] is None


def test_restart_of_interrupted_job_resumes_after_one_interval(timer):
    scheduler,job,now,path=timer
    scheduler.save({'enabled':True,'interval_minutes':5})
    now[0]=1300;scheduler.tick()
    assert scheduler.state()['next_run'] is None
    restarted=AutoRefresh(path,FakeJob(),['new'],clock=lambda:now[0])
    assert restarted.state()['next_run']==1600


def test_session_age_is_actual_and_expiry_unknown(timer):
    scheduler,job,now,path=timer
    (path/'session-status.json').write_text(json.dumps({'authenticated_at':700,'last_fetch_at':950,'login_required':False}))
    session=scheduler.state()['session']
    assert session['age_seconds']==300
    assert session['last_fetch_at']==950
    assert session['expected_expiry'] is None


def test_empty_device_selection_pauses_and_saving_new_choice_resumes(timer):
    scheduler,job,now,path=timer
    selection=path/'device-selection.json'
    selection.write_text(json.dumps({'all_devices':False,'identifiers':[]}))
    scheduler.save({'enabled':True,'interval_minutes':5})
    now[0]=1300;scheduler.tick()
    assert not job.commands and scheduler.state()['paused_reason']=='no_selection'
    selection.write_text(json.dumps({'all_devices':False,'identifiers':['one']}))
    scheduler.devices_changed()
    assert scheduler.state()['next_run']==1600 and scheduler.state()['paused_reason'] is None
    now[0]=1600;scheduler.tick();assert len(job.commands)==1
