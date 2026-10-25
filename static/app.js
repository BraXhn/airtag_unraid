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
  if(data.updated_at===lastUpdate)return;
  lastUpdate=data.updated_at; pins.clearLayers();$('devices').replaceChildren();
  $('count').textContent=data.devices.length;
  $('updated').textContent=data.updated_at?'Hämtat '+date(data.updated_at):'Ingen hämtning ännu';
  data.devices.forEach((d,i)=>{
    const card=document.createElement('button');card.className='device';
    const title=document.createElement('strong');title.textContent=d.name;card.append(title);
    const info=document.createElement('small');card.append(info);
    const p=d.location;
    if(p && Number.isFinite(p.latitude) && Number.isFinite(p.longitude)){
      info.textContent=date(p.timestamp)+' · ± '+p.accuracy+' m';
      if(Date.now()-Date.parse(p.timestamp)>3600000)info.className='old';
      const label=document.createElement('div');label.textContent=d.name+' · '+date(p.timestamp);
      const marker=L.marker([p.latitude,p.longitude],{icon:L.divIcon({className:'pin',html:String(i+1),iconSize:[30,30]})}).bindPopup(label).addTo(pins);
      L.circle([p.latitude,p.longitude],{radius:Math.max(0,p.accuracy||0),weight:1,color:'#2e694b',fillOpacity:.08}).addTo(pins);
      card.onclick=()=>{map.setView([p.latitude,p.longitude],15);marker.openPopup();};
    }else{info.textContent='Ingen position rapporterad';card.disabled=true;}
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
    wasRunning=s.running;render(await api('/api/locations'));
  }catch(e){$('error').textContent=e.message;}finally{busy=false;}
}
document.querySelectorAll('[data-action]').forEach(b=>b.onclick=async()=>{try{$('error').textContent='';await api('/api/start',{action:b.dataset.action});await poll();$('value').focus();}catch(e){$('error').textContent=e.message;}});
$('answer').onsubmit=async e=>{e.preventDefault();const value=$('value').value;$('value').value='';try{await api('/api/input',{value});$('error').textContent='';}catch(e){$('error').textContent=e.message;}};
$('stop').onclick=async()=>{try{await api('/api/stop',{});}catch(e){$('error').textContent=e.message;}};
$('fit').onclick=fit;poll();setInterval(poll,2000);
