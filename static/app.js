const $ = id => document.getElementById(id);
const map = L.map('map').setView([59.33,18.06],5);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',maxZoom:19
}).addTo(map);
const pins = L.featureGroup().addTo(map);
let lastUpdate=null, wasRunning=false, busy=false;
const date = value => new Date(value).toLocaleString('sv-SE');
async function api(path,body){
  const options={cache:'no-store'};
  if(body!==undefined) Object.assign(options,{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'AirTagMap'},body:JSON.stringify(body)});
  const r=await fetch(path,options); if(!r.ok){if(r.status===401)window.location.replace('/login');const e=await r.json().catch(()=>({}));throw Error(e.error||'Anslutningen misslyckades ('+r.status+')');} return r.json();
}
function fit(){if(pins.getLayers().length)map.fitBounds(pins.getBounds(),{padding:[40,40],maxZoom:15});}
function render(data){
  const renderKey=data.updated_at+':'+wasRunning;
  if(renderKey===lastUpdate)return;
  lastUpdate=renderKey; pins.clearLayers();$('devices').replaceChildren();
  $('count').textContent=data.devices.length;
  $('updated').textContent=data.fetching&&wasRunning?'Hämtar positioner · '+data.completed+'/'+data.total+' klara':data.completed<data.total?'Hämtningen avslutades · '+data.completed+'/'+data.total+' klara':data.updated_at?'Uppdaterat '+date(data.updated_at):'Ingen hämtning ännu';
  data.devices.forEach((d,i)=>{
    const card=document.createElement('button');card.className='device';
    const title=document.createElement('strong');title.textContent=d.name;card.append(title);
    const info=document.createElement('small');card.append(info);
    const p=d.location;
    const fetchStatus=!wasRunning&&['fetching','pending'].includes(d.fetch_status)?'interrupted':d.fetch_status;
    if(p && Number.isFinite(p.latitude) && Number.isFinite(p.longitude)){
      info.textContent=date(p.timestamp)+' · ± '+p.accuracy+' m'+(fetchStatus==='fetching'?' · Söker ny position':fetchStatus==='pending'?' · Väntar på hämtning':fetchStatus==='error'?' · Hämtning misslyckades':fetchStatus==='no_report'?' · Ingen ny rapport':fetchStatus==='interrupted'?' · Ej färdighämtad':fetchStatus==='not_selected'?' · Ej vald för hämtning':'');
      if(Date.now()-Date.parse(p.timestamp)>3600000)info.className='old';
      const label=document.createElement('div');label.textContent=d.name+' · '+date(p.timestamp);
      const marker=L.marker([p.latitude,p.longitude],{icon:L.divIcon({className:'pin',html:String(i+1),iconSize:[30,30]})}).bindPopup(label).addTo(pins);
      L.circle([p.latitude,p.longitude],{radius:Math.max(0,p.accuracy||0),weight:1,color:'#2e694b',fillOpacity:.08}).addTo(pins);
      card.onclick=()=>{map.setView([p.latitude,p.longitude],15);marker.openPopup();};
    }else{info.textContent=fetchStatus==='fetching'?'Söker position…':fetchStatus==='pending'?'Väntar på hämtning':fetchStatus==='error'?'Hämtning misslyckades':fetchStatus==='interrupted'?'Ej färdighämtad':fetchStatus==='not_selected'?'Ej vald för hämtning':'Ingen position rapporterad';card.disabled=true;}
    $('devices').append(card);
  });fit();
}
async function poll(){
  if(busy)return;busy=true;
  try{
    const s=await api('/api/state');
    $('output').textContent=s.output||'Starta med Anslut Apple-konto.';
    if(s.running||wasRunning)$('output').scrollTop=$('output').scrollHeight;
    $('status').textContent=s.running?'Åtgärd pågår':s.exit_code===0?'Åtgärd klar':s.exit_code!==null?'Åtgärden avslutades':s.has_keys?'Föremål anslutna':'Inte ansluten';
    document.querySelectorAll('[data-action]').forEach(b=>b.disabled=s.running);
    $('value').disabled=!s.running;$('answer').querySelector('button').disabled=!s.running;
    wasRunning=s.running;render(await api('/api/locations'));await refreshForwarding();await refreshSchedule();await refreshSelection();
  }catch(e){$('error').textContent=e.message;}finally{busy=false;}
}
document.querySelectorAll('[data-action]').forEach(b=>b.onclick=async()=>{try{$('error').textContent='';await api('/api/start',{action:b.dataset.action});await poll();$('value').focus();}catch(e){$('error').textContent=e.message;}});
$('answer').onsubmit=async e=>{e.preventDefault();const value=$('value').value;$('value').value='';try{await api('/api/input',{value});$('error').textContent='';}catch(e){$('error').textContent=e.message;}};
$('stop').onclick=async()=>{try{await api('/api/stop',{});}catch(e){$('error').textContent=e.message;}};
let forwardingLoaded=false, forwardingDirty=false, forwardingTags=[];
function renderForwarding(cfg){
  $('forward-enabled').checked=cfg.enabled;$('forward-host').value=cfg.host;
  $('forward-port').value=cfg.port;$('forward-codec').value=cfg.codec;
  forwardingTags=cfg.tags;$('forward-tags').replaceChildren();
  cfg.tags.forEach(tag=>{
    const label=document.createElement('label');label.textContent=tag.name+' · virtuellt ID';
    const input=document.createElement('input');input.value=tag.imei;input.inputMode='numeric';
    input.pattern='[0-9]{15}';input.minLength=15;input.maxLength=15;input.required=true;
    input.dataset.identifier=tag.identifier;label.append(input);$('forward-tags').append(label);
  });
}
async function refreshForwarding(){
  const data=await api('/api/forwarding'), s=data.status;
  // Preserve unsaved edits while polling delivery state.
  const changed=JSON.stringify(data.config.tags.map(t=>t.identifier))!==JSON.stringify(forwardingTags.map(t=>t.identifier));
  if(!forwardingDirty&&(!forwardingLoaded||changed)){renderForwarding(data.config);forwardingLoaded=true;}
  $('forward-status').textContent=(data.config.enabled?'Aktiverad':'Pausad')+' · '+s.pending+' i kö · '+s.sent+' kvitterade'+(s.last_sent?' · Senast '+date(s.last_sent*1000):'')+(s.error?' · '+s.error:'');
}
$('forwarding').addEventListener('input',()=>{forwardingDirty=true;});
$('forwarding').onsubmit=async e=>{
  e.preventDefault();$('forward-error').textContent='';$('forward-save').disabled=true;
  try{
    const tags=Array.from($('forward-tags').querySelectorAll('input')).map(input=>({identifier:input.dataset.identifier,imei:input.value}));
    await api('/api/forwarding',{enabled:$('forward-enabled').checked,host:$('forward-host').value.trim(),port:Number($('forward-port').value),codec:$('forward-codec').value,tags});
    forwardingDirty=false;forwardingLoaded=false;await refreshForwarding();
    $('forward-error').textContent='Inställningarna sparades.';
  }catch(e){$('forward-error').textContent=e.message;}finally{$('forward-save').disabled=false;}
};
let scheduleLoaded=false, scheduleDirty=false;
function duration(seconds){
  const minutes=Math.floor(seconds/60);
  if(minutes<60)return minutes+' min';
  const hours=Math.floor(minutes/60);
  return hours<24?hours+' tim '+minutes%60+' min':Math.floor(hours/24)+' dygn '+hours%24+' tim';
}
async function refreshSchedule(){
  const s=await api('/api/auto-refresh');
  if(!scheduleLoaded&&!scheduleDirty){$('auto-interval').value=String(s.enabled?s.interval_minutes:0);scheduleLoaded=true;}
  const reasons={login_required:'Pausad – ny Apple-inloggning behövs',missing_session:'Pausad – logga in och hämta föremålen manuellt först',no_selection:'Pausad – välj minst ett föremål att hämta',apple_cooldown:'Väntar efter Apples anropsbegränsning'};
  $('auto-status').textContent=!s.enabled?'Automatisk sökning är avstängd':(reasons[s.paused_reason]||'Aktiverad')+(s.next_run?' · Nästa sökning tidigast '+date(s.next_run*1000):wasRunning?' · Åtgärd pågår':'');
  const session=s.session;
  $('session-age').textContent=(session.authenticated_at?'Senaste inloggning: '+date(session.authenticated_at*1000)+' · Ålder '+duration(session.age_seconds):'Inloggningens ålder är okänd för äldre sparade sessioner.')+(session.last_fetch_at?' · Senaste genomförda positionssökning: '+date(session.last_fetch_at*1000):'')+(session.login_required?' · Ny inloggning krävs.':'')+' · Förväntat utgångsdatum: okänt.';
}
$('auto-interval').onchange=()=>{scheduleDirty=true;};
$('auto-refresh').onsubmit=async e=>{
  e.preventDefault();$('auto-save').disabled=true;$('auto-error').textContent='';
  try{
    const minutes=Number($('auto-interval').value);
    await api('/api/auto-refresh',{enabled:minutes!==0,interval_minutes:minutes||15});
    scheduleLoaded=false;scheduleDirty=false;await refreshSchedule();
    $('auto-error').textContent='Intervallet sparades.';
  }catch(e){$('auto-error').textContent=e.message;}finally{$('auto-save').disabled=false;}
};
let selectionLoaded=false, selectionDirty=false, selectionSignature=null;
function selectionSummary(){
  const inputs=Array.from($('selection-devices').querySelectorAll('input'));
  $('selection-status').textContent=inputs.filter(input=>input.checked).length+' av '+inputs.length+' föremål valda'+(selectionDirty?' · Osparade ändringar':'');
}
async function refreshSelection(){
  const data=await api('/api/device-selection');
  const signature=JSON.stringify(data);
  if(!selectionDirty&&(!selectionLoaded||signature!==selectionSignature)){
    $('select-all').checked=data.all_devices;$('selection-devices').replaceChildren();
    data.devices.forEach(device=>{
      const label=document.createElement('label'), input=document.createElement('input');
      input.type='checkbox';input.checked=device.selected;input.dataset.identifier=device.identifier;
      label.append(input,document.createTextNode(' '+device.name+(device.model?' · '+device.model:'')));
      $('selection-devices').append(label);
    });
    selectionLoaded=true;selectionSignature=signature;
    selectionSummary();
    if(!data.devices.length)$('selection-status').textContent=data.error||'Anslut Apple-kontot för att hämta föremålslistan först.';
  }
}
$('select-all').onchange=()=>{
  selectionDirty=true;
  $('selection-devices').querySelectorAll('input').forEach(input=>{input.checked=$('select-all').checked;});
  selectionSummary();
};
$('selection-devices').addEventListener('change',()=>{selectionDirty=true;$('select-all').checked=false;selectionSummary();});
$('device-selection').onsubmit=async e=>{
  e.preventDefault();$('selection-save').disabled=true;$('selection-error').textContent='';
  try{
    const identifiers=Array.from($('selection-devices').querySelectorAll('input')).filter(input=>input.checked).map(input=>input.dataset.identifier);
    await api('/api/device-selection',{all_devices:$('select-all').checked,identifiers});
    selectionDirty=false;selectionLoaded=false;await refreshSelection();await refreshSchedule();
    $('selection-error').textContent='Urvalet sparades och gäller nästa sökning.';
  }catch(e){$('selection-error').textContent=e.message;}finally{$('selection-save').disabled=false;}
};
$('logout').onclick=async()=>{
  try{await api('/api/logout',{});window.location.replace('/login');}
  catch(e){$('error').textContent=e.message;}
};
async function refreshAccounts(){
  const data=await api('/api/admin/users');$('accounts-list').replaceChildren();
  if(!data.users.length){$('accounts-list').textContent='Inga användarkonton skapade ännu.';return;}
  data.users.forEach(user=>{
    const card=document.createElement('section');card.className='account-card';
    const title=document.createElement('strong');title.textContent=user.username+' · '+(user.enabled?'Aktivt':'Pausat');card.append(title);
    const toggle=document.createElement('button');toggle.type='button';toggle.textContent=user.enabled?'Pausa konto':'Aktivera konto';
    toggle.onclick=async()=>{
      toggle.disabled=true;
      try{await api('/api/admin/users/'+user.id,{enabled:!user.enabled});await refreshAccounts();$('account-message').textContent='Kontostatusen sparades.';}
      catch(e){$('account-message').textContent=e.message;toggle.disabled=false;}
    };card.append(toggle);
    const reset=document.createElement('form'),label=document.createElement('label'),input=document.createElement('input'),button=document.createElement('button');
    label.textContent='Nytt lösenord för '+user.username;input.type='password';input.autocomplete='new-password';input.minLength=12;input.maxLength=256;input.required=true;label.append(input);
    button.textContent='Återställ lösenord';reset.append(label,button);
    reset.onsubmit=async event=>{
      event.preventDefault();button.disabled=true;const password=input.value;input.value='';
      try{await api('/api/admin/users/'+user.id,{password});$('account-message').textContent='Lösenordet återställdes. Tidigare inloggningar har avslutats.';}
      catch(e){$('account-message').textContent=e.message;}finally{button.disabled=false;}
    };card.append(reset);$('accounts-list').append(card);
  });
}
if($('create-account')){
  $('create-account').onsubmit=async event=>{
    event.preventDefault();$('create-account-button').disabled=true;$('account-message').textContent='';
    const password=$('new-password').value;$('new-password').value='';
    try{const data=await api('/api/admin/users',{username:$('new-username').value,password});$('new-username').value='';await refreshAccounts();$('account-message').textContent='Kontot '+data.user.username+' skapades.';}
    catch(e){$('account-message').textContent=e.message;}finally{$('create-account-button').disabled=false;}
  };
  $('account-admin').addEventListener('toggle',()=>{if($('account-admin').open)refreshAccounts().catch(e=>{$('account-message').textContent=e.message;});});
}
$('fit').onclick=fit;poll();setInterval(poll,2000);
