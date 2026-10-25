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
  const r=await fetch(path,options); if(!r.ok){const e=await r.json().catch(()=>({}));throw Error(e.error||'Anslutningen misslyckades ('+r.status+')');} return r.json();
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
      info.textContent=date(p.timestamp)+' · ± '+p.accuracy+' m'+(fetchStatus==='fetching'?' · Söker ny position':fetchStatus==='pending'?' · Väntar på hämtning':fetchStatus==='error'?' · Hämtning misslyckades':fetchStatus==='no_report'?' · Ingen ny rapport':fetchStatus==='interrupted'?' · Ej färdighämtad':'');
      if(Date.now()-Date.parse(p.timestamp)>3600000)info.className='old';
      const label=document.createElement('div');label.textContent=d.name+' · '+date(p.timestamp);
      const marker=L.marker([p.latitude,p.longitude],{icon:L.divIcon({className:'pin',html:String(i+1),iconSize:[30,30]})}).bindPopup(label).addTo(pins);
      L.circle([p.latitude,p.longitude],{radius:Math.max(0,p.accuracy||0),weight:1,color:'#2e694b',fillOpacity:.08}).addTo(pins);
      card.onclick=()=>{map.setView([p.latitude,p.longitude],15);marker.openPopup();};
    }else{info.textContent=fetchStatus==='fetching'?'Söker position…':fetchStatus==='pending'?'Väntar på hämtning':fetchStatus==='error'?'Hämtning misslyckades':fetchStatus==='interrupted'?'Ej färdighämtad':'Ingen position rapporterad';card.disabled=true;}
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
    wasRunning=s.running;render(await api('/api/locations'));await refreshForwarding();
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
$('fit').onclick=fit;poll();setInterval(poll,2000);
