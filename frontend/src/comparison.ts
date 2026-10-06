import { Map as MapLibreMap, Marker, NavigationControl } from 'maplibre-gl';
import { MAP_STYLE } from './map';
import { createAddressAutocomplete } from './autocomplete';
import {getAddresses,setAddresses,watchAddresses,newAddress} from './addresses';
import {createRouteView,type MapLeg} from './route-view';
import {createDialog} from './dialog';

type Point = [number, number];
interface Location { id?: string; enabled?: boolean; name: string; point: Point | null; rent?: number | null }
interface Settings { version: 1; destination: Location; addresses: Location[]; week: string;
  days: number[]; arrival: string; departure: string; walk: number }
interface Leg extends MapLeg { mode: string; route?: string; from: string; to: string; departure: number; arrival: number }
interface Journey { departure: number; arrival: number; seconds: number; walking_seconds: number; transfers: number; legs: Leg[] }
interface Day { date: string; status: string; reason?: string; outbound: Journey | null;
  return: Journey | null; return_later: Journey | null }
interface AddressResult { name: string; rent: number | null; days: Day[]; weekly_seconds: number | null }
interface Comparison { pipeline_run_id?:string; addresses: AddressResult[]; coverage: {first: string; last: string}; snapshot_date: string }
const escape = (value: unknown): string => String(value ?? '').replace(/[&<>"']/g,
  c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'})[c]!);
const duration = (seconds: number): string => {
  const minutes = Math.ceil(seconds / 60);
  return minutes >= 60 ? `${Math.floor(minutes/60)} h ${minutes%60} min` : `${minutes} min`;
};
const clock = (seconds: number): string => {
  const day = Math.floor(seconds/86400), value = ((seconds%86400)+86400)%86400;
  return `${String(Math.floor(value/3600)).padStart(2,'0')}:${String(Math.floor(value%3600/60)).padStart(2,'0')}${day < 0 ? ' (previous day)' : day > 0 ? ' (next day)' : ''}`;
};
const localDate = (): string => new Intl.DateTimeFormat('en-CA', {
  timeZone:'Europe/Rome',year:'numeric',month:'2-digit',day:'2-digit',
}).format(new Date());
const monday = (value: string): string => {
  const day = new Date(`${value}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() - (day.getUTCDay()+6)%7);
  return day.toISOString().slice(0,10);
};
const dateLabel = (value: string): string => new Date(`${value}T12:00:00`).toLocaleDateString('en-GB', {weekday:'short',day:'numeric',month:'short'});
function validateStored(value: unknown): Settings | null {
  if (!value || typeof value !== 'object') return null;
  const s = value as Settings;
  s.addresses ??= (value as {apartments?:Location[]}).apartments!;
  const location = (v: Location): boolean => !!v && typeof v.name === 'string' && v.name.length <= 200 &&
    (v.point === null || (Array.isArray(v.point) && v.point.length === 2 && v.point.every(Number.isFinite) &&
      v.point[0]>=8.3 && v.point[0]<=10.2 && v.point[1]>=44.8 && v.point[1]<=46.2)) &&
    (v.rent == null || (Number.isFinite(v.rent) && v.rent>=0 && v.rent<=100000));
  if (s.version !== 1 || !location(s.destination) || !Array.isArray(s.addresses) ||
    s.addresses.length < 1 || s.addresses.length > 3 || !s.addresses.every(location) ||
    !/^\d{4}-\d{2}-\d{2}$/.test(s.week) || !Number.isFinite(Date.parse(s.week)) ||
    !Array.isArray(s.days) || !s.days.length || !s.days.every(d=>Number.isInteger(d)&&d>=0&&d<=6) ||
    ![s.arrival,s.departure].every(t=>/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(t)) || ![5,10,15].includes(s.walk)) return null;
  return s;
}

export function createComparison(root: HTMLElement, serviceDays: {service_date:string}[]): {destroy():void; updateCoverage(days:{service_date:string}[]):void} {
  let settings: Settings = {version:1,destination:{name:'',point:null},addresses:getAddresses(),week:monday(localDate()),days:[0,2,4],arrival:'09:00',departure:'18:00',walk:10};
  let imported = false, storageMessage = '';
  try {
    const hash = new URLSearchParams(location.hash.slice(1)).get('comparison');
    const raw = hash ? new TextDecoder().decode(Uint8Array.from(atob(hash.replace(/-/g,'+').replace(/_/g,'/')), c=>c.charCodeAt(0))) : localStorage.getItem('milano-commute-settings') ?? localStorage.getItem('milano-apartments');
    if (raw && raw.length <= 16000) {
      const parsed = validateStored(JSON.parse(raw));
      if (parsed) { settings=parsed; imported=!!hash; }
      else storageMessage='Saved comparison settings were invalid. Enter new locations.';
    }
  } catch { storageMessage='Saved comparison settings could not be loaded.'; }
  if(imported){settings.addresses=settings.addresses.map(a=>({...a,id:a.id??crypto.randomUUID(),enabled:a.enabled!==false}));setAddresses(settings.addresses as ReturnType<typeof getAddresses>,'comparison');}
  else settings.addresses=getAddresses();
  const publishAddresses=():void=>setAddresses(settings.addresses.map(a=>({id:a.id??crypto.randomUUID(),name:a.name,point:a.point,enabled:a.enabled!==false})),'comparison');
  const included=():Location[]=>settings.addresses.filter(a=>a.enabled!==false);
  let routeViews:{destroy():void}[]=[],journeyMaps:Leg[][]=[];
  const clearRouteViews=():void=>{routeViews.forEach(r=>r.destroy());routeViews=[];journeyMaps=[];};
  let controller: AbortController | null = null;
  const addressEditors: {destroy():void}[] = [];
  let version=0, mapTarget=-1, step=0;
  let mapEditing=false;
  let results: Comparison | null = null;
  const markers: Marker[] = [];
  const dates=serviceDays.map(d=>d.service_date).sort();
  const lastDate=dates.at(-1) ?? '';
  root.innerHTML=`<div class="planner-heading"><div><h2>Journey comparison</h2><p>Compare travel time, walking and transfers to one destination.</p></div><div class="planner-actions"><button type="button" class="secondary-button planner-settings-toggle" aria-expanded="false" aria-controls="comparison-form">Journey settings</button><button type="button" class="secondary-button" id="comparison-locations">Locations map</button><button type="button" class="secondary-button" id="comparison-info">Data & method</button></div></div>
    <div class="comparison-layout"><form id="comparison-form" class="comparison-form" novalidate>
      <div class="comparison-steps" aria-label="Comparison steps">
        <button type="button" data-step="0" aria-current="step"><span>1</span>Destination</button>
        <button type="button" data-step="1"><span>2</span>Addresses</button>
        <button type="button" data-step="2"><span>3</span>Travel days</button>
      </div>
      <p id="comparison-status" role="status" aria-live="polite"></p>
      <fieldset data-form-step="0"><legend>1. Set your work or study destination</legend>
        <p class="step-help">Select a Milan address suggestion or place a pin on the locations map.</p>
        <div id="destination-fields"></div>
      </fieldset>
      <fieldset data-form-step="1" hidden><legend>2. Add addresses to compare</legend>
        <p class="step-help">Tick the addresses to compare. Open an address to edit its location.</p>
        <div id="address-fields"></div><button id="add-address" type="button" class="secondary-button">Add another address</button>
        <p class="field-help">Up to three addresses, shared with Nearby places.</p>
      </fieldset>
      <fieldset data-form-step="2" hidden><legend>3. Choose your travel days and times</legend>
        <div id="comparison-selection-summary" class="selection-summary"></div>
        <div class="comparison-fields">
        <label>Week starting Monday<input id="comparison-week" type="date" required value="${settings.week}"></label>
        <label>Walking limit per leg<select id="comparison-walk">${[5,10,15].map(v=>`<option value="${v}" ${v===settings.walk?'selected':''}>${v} minutes</option>`).join('')}</select></label>
        <label>Arrive by<input id="comparison-arrival" type="time" required value="${settings.arrival}"></label>
        <label>Leave at<input id="comparison-departure" type="time" required value="${settings.departure}"></label></div>
        <p class="field-label" id="office-days-label">Days you travel to this destination</p>
        <div class="office-days" role="group" aria-labelledby="office-days-label">${['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map((name,i)=>`<label><input type="checkbox" value="${i}" ${settings.days.includes(i)?'checked':''}>${name}</label>`).join('')}</div>
        <p class="field-help">Milan time. Walking limits apply to each leg; journeys are limited to 90 minutes.</p>

      </fieldset>
      <div class="step-actions"><button id="comparison-back" type="button" class="secondary-button" hidden>Back</button>
        <button id="comparison-next" type="button" class="commute-submit">Continue to addresses</button>
        <button id="compare-addresses" class="commute-submit" type="submit" hidden>Calculate comparison</button></div>
      <details class="save-options"><summary>Save or share these settings</summary>
        <p>Save addresses and travel preferences in this browser, or copy a link that includes them. Recipients need access to this application. Recalculate to get results from the current timetable.</p>
        <div class="comparison-actions"><button id="save-comparison" type="button" class="secondary-button">Save in this browser</button><button id="share-comparison" type="button" class="secondary-button">Copy settings link</button></div>
        <input id="comparison-share-link" aria-label="Comparison link" readonly hidden>
      </details>
    </form><div class="comparison-map-column"><div class="comparison-map-toolbar"><label for="comparison-map-target">Location</label><select id="comparison-map-target"></select>
      <div class="map-placement-actions"><button id="enable-map-pin" type="button" class="secondary-button">Place a pin instead</button><button id="cancel-map-pin" type="button" class="secondary-button" hidden>Cancel</button></div>
      <p id="map-selection-help" role="status">Selected addresses appear on this map. D = destination; 1–3 = addresses.</p></div>
      <div id="comparison-map" role="region" aria-label="Address and destination locations"></div>
      <div class="comparison-notes"><p id="comparison-coverage"></p><p>Scheduled journeys only. Check your transport operator for live disruptions.</p>
      <details><summary>Data, walking estimates and privacy</summary>
      <p>Walking to and from stops follows streets. Walks between transfer stops are estimates. Station entrances and accessibility are not verified.</p>
      <p>Address text is sent to Photon as you type. Walking coordinates are sent to the routing service. Your shared address list is saved in this browser automatically. Travel preferences are saved when you choose Save. A settings link contains the locations and schedule you choose to share.</p>
      <p><a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap data</a> · <a href="https://www.openstreetmap.org/fixthemap" target="_blank" rel="noopener">Report a map issue</a></p></details></div></div></div>
    <section id="comparison-results" aria-label="Address comparison results"><div class="comparison-empty"><h3>Your comparison results</h3><p>Set a destination, include addresses and choose travel days. Calculate to compare morning, return and weekly travel.</p></div></section>`;
  const get=<T extends HTMLElement>(selector:string):T=>root.querySelector<T>(selector)!;
  const locationDialog=createDialog(root,'Address locations',()=>setMapEditing(false));
  locationDialog.body.append(get('.comparison-map-column'));
  get('.comparison-layout').append(get('#comparison-results'));
  const journeyDialog=createDialog(root,'Daily journeys',()=>{clearRouteViews();journeyDialog.body.replaceChildren();});
  const infoDialog=createDialog(root,'Journey data and method');
  infoDialog.body.append(get('.comparison-notes'));
  infoDialog.body.insertAdjacentHTML('afterbegin','<p>Table values are ranges across the selected days. Weekly travel sums morning and return journeys and is withheld when any journey is unavailable. Search considers up to 12 nearby boarding stops, two transfers and a two-minute transfer allowance.</p>');
  get('#comparison-info').addEventListener('click',()=>infoDialog.open());
  get('.planner-settings-toggle').addEventListener('click',()=>{
    const expanded=root.classList.toggle('settings-open');get('.planner-settings-toggle').setAttribute('aria-expanded',String(expanded));
  });
  const revealField=(index:number):void=>{
    const input=get<HTMLInputElement>(`#address-${index}`),details=input.closest('details');
    if(details)details.open=true;input.focus();
  };
  root.addEventListener('toggle',event=>{
    const details=event.target as HTMLDetailsElement;
    if(details.matches('.comparison-address-editor')&&details.open)root.querySelectorAll<HTMLDetailsElement>('.comparison-address-editor').forEach(other=>{if(other!==details)other.open=false;});
  },true);
  const renderEmpty=():void=>{
    const addresses=included();
    get('#comparison-results').innerHTML=`<div class="comparison-results-header"><div><h2>Scheduled journeys</h2><p>Complete the settings and calculate to fill this comparison.</p></div></div><div class="comparison-table-wrap"><table class="comparison-table"><caption class="visually-hidden">Journey comparison awaiting calculation</caption><thead><tr><th scope="col">Measure</th>${(addresses.length?addresses:[{name:'Include an address'}]).map(a=>`<th scope="col">${escape(a.name||'Select an address')}</th>`).join('')}</tr></thead><tbody>${['Morning journey','Return journey','Morning walking','Return walking','Morning transfers','Return transfers','Total travel'].map(label=>`<tr><th scope="row">${label}</th>${(addresses.length?addresses:[null]).map(()=>'<td><span class="nearby-pending" aria-label="Not calculated">—</span></td>').join('')}</tr>`).join('')}</tbody></table></div><p class="results-guide">Ranges cover selected days. Daily journeys and route maps become available after calculation.</p>`;
  };
  renderEmpty();
  const status=get('#comparison-status');
  const button=get<HTMLButtonElement>('#compare-addresses');
  const showStep=(next:number, focus=true):void=>{
    step=next;if(focus){root.classList.add('settings-open');get('.planner-settings-toggle').setAttribute('aria-expanded','true');}
    root.querySelectorAll<HTMLElement>('[data-form-step]').forEach(el=>el.hidden=Number(el.dataset.formStep)!==step);
    root.querySelectorAll<HTMLElement>('[data-step]').forEach(el=>{
      if(Number(el.dataset.step)===step)el.setAttribute('aria-current','step');else el.removeAttribute('aria-current');
    });
    get('#comparison-back').hidden=step===0;
    get('#comparison-next').hidden=step===2;button.hidden=step!==2;
    get('#comparison-next').textContent=step===0?'Continue to addresses':'Continue to travel days';
    get('#comparison-selection-summary').textContent=`${settings.destination.point?settings.destination.name:'Destination not selected'} · ${included().length} included addresses`;
    if(focus)root.querySelector<HTMLElement>(`[data-form-step="${step}"] input`)?.focus();
  };
  get('#comparison-back').addEventListener('click',()=>showStep(Math.max(0,step-1)));
  get('#comparison-next').addEventListener('click',()=>{
    if(!validateLocations(step))return;
    if(!validateFields())return;
    status.textContent='';showStep(Math.min(2,step+1));
  });
  root.querySelectorAll<HTMLButtonElement>('[data-step]').forEach(el=>el.addEventListener('click',()=>showStep(Number(el.dataset.step))));
  get('#comparison-coverage').textContent = lastDate ? `Timetable coverage: ${dates[0]} to ${lastDate}.${lastDate<localDate()?' The timetable has expired. Update the data before planning current travel.':''}` : 'No timetable dates are available.';
  status.textContent=imported?'Shared comparison loaded. Calculate to obtain current results.':storageMessage;
  const map=new MapLibreMap({container:get('#comparison-map'),style:MAP_STYLE,center:[9.19,45.4642],zoom:11});
  map.addControl(new NavigationControl({showCompass:false}), 'top-right');
  const observer=new ResizeObserver(()=>map.resize()); observer.observe(get('#comparison-map'));
  const field=(place:Location,index:number):string=>`${index>=0?`<div class="comparison-address-card"><label class="address-include"><input data-enabled="${index}" type="checkbox" ${place.enabled!==false?'checked':''}> Include address ${index+1}</label><details class="comparison-address-editor"><summary title="${escape(place.name)}">${index+1}. ${escape(place.name||'Select address')}</summary>`:''}<div class="location-editor" data-location="${index}"><label for="address-${index}">${index<0?'Destination address':`Address ${index+1}`}</label>
    <div class="address-search"><input id="address-${index}" type="search" maxlength="200" value="${escape(place.name)}" placeholder="Street, house number, Milan" autocomplete="off" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="address-results-${index}" aria-describedby="address-feedback-${index}"><button type="button" data-search="${index}" class="secondary-button">Search</button></div>
    <p class="selected-location">${place.point?'Location selected. You can continue.':'Type at least 3 characters, then select a suggestion. Include a house number for a precise location.'}</p>
    <div id="address-results-${index}" class="address-results" role="listbox" aria-label="Milan address suggestions" hidden></div>
    <p id="address-feedback-${index}" class="address-feedback" role="status" aria-live="polite"></p>
    ${index>=0?`<button type="button" data-remove="${index}" class="secondary-button" ${settings.addresses.length===1?'disabled':''}>Remove address</button>`:''}
    <button type="button" data-pin="${index}" class="map-location-button">Choose on map</button></div>${index>=0?'</details></div>':''}`;
  const place=(index:number):Location=>index<0?settings.destination:settings.addresses[index]!;
  const syncMarkers=():void=>{
    markers.forEach(m=>m.remove()); markers.length=0;
    [settings.destination,...settings.addresses].forEach((p,i)=>{
      if (!p.point || p.enabled===false) return;
      const element=document.createElement('div'); element.className=`comparison-pin ${i===0?'destination-pin':''}`;
      element.textContent=i===0?'D':String(i); element.title=i===0?'Destination':`Address ${i}`;
      markers.push(new Marker({element}).setLngLat(p.point).addTo(map));
    });
  };
  const renderFields=():void=>{
    addressEditors.forEach(editor=>editor.destroy()); addressEditors.length=0;
    get('#destination-fields').innerHTML=field(settings.destination,-1);
    get('#address-fields').innerHTML=settings.addresses.map((p,i)=>field(p,i)).join('');
    get<HTMLButtonElement>('#add-address').disabled=settings.addresses.length>=3;
    get<HTMLSelectElement>('#comparison-map-target').innerHTML=`<option value="-1">Destination</option>${settings.addresses.map((_,i)=>`<option value="${i}">Address ${i+1}</option>`).join('')}`;
    if(mapTarget>=settings.addresses.length)mapTarget=-1;
    get<HTMLSelectElement>('#comparison-map-target').value=String(mapTarget);
    syncMarkers();showStep(step,false);if(!results)renderEmpty();
    root.querySelectorAll<HTMLElement>('.location-editor').forEach(editor=>{
      const index=Number(editor.dataset.location);
      addressEditors.push(createAddressAutocomplete(editor,result=>{
        choose(index,result.name,result.point);
        revealField(index);
        status.textContent=index<0?'Destination selected. Continue to addresses.':'Address location selected. Add another address or continue to travel days.';
      }));
    });
  };
  renderFields();
  const invalidate=():void=>{
    version++; controller?.abort(); journeyDialog.close();clearRouteViews(); results=null; button.disabled=false;
    renderEmpty();
    get<HTMLInputElement>('#comparison-share-link').hidden=true;
    status.textContent='';
  };
  const choose=(index:number, name:string, point:Point):void=>{
    invalidate(); const target=place(index); target.name=name;target.point=point;mapTarget=index;if(index>=0)publishAddresses();renderFields();
    setMapEditing(false);locationDialog.close();
    const points=[settings.destination,...settings.addresses].flatMap(p=>p.point?[p.point]:[]);
    if(points.length>1)map.fitBounds([
      [Math.min(...points.map(p=>p[0])),Math.min(...points.map(p=>p[1]))],
      [Math.max(...points.map(p=>p[0])),Math.max(...points.map(p=>p[1]))],
    ],{padding:55,maxZoom:14});
    else map.easeTo({center:point,zoom:Math.max(map.getZoom(),13)});
  };
  const setMapEditing=(active:boolean):void=>{
    if(active)openLocations();
    mapEditing=active;get('#cancel-map-pin').hidden=!active;
    get('#enable-map-pin').textContent=active?'Choose a point on the map':'Place a pin instead';
    get('#map-selection-help').textContent=active?`Click the map to set ${mapTarget<0?'your destination':`address ${mapTarget+1}`}.`:'Selected addresses appear on this map. D = destination; 1–3 = addresses.';
    map.getCanvas().style.cursor=active?'crosshair':'';
  };
  const openLocations=():void=>{
    locationDialog.open();map.resize();
    const points=[settings.destination,...settings.addresses].flatMap(p=>p.point?[p.point]:[]);
    if(points.length)map.fitBounds([[Math.min(...points.map(p=>p[0])),Math.min(...points.map(p=>p[1]))],[Math.max(...points.map(p=>p[0])),Math.max(...points.map(p=>p[1]))]],{padding:50,maxZoom:14,duration:0});
  };
  get('#comparison-locations').addEventListener('click',openLocations);
  get('#enable-map-pin').addEventListener('click',()=>setMapEditing(true));
  get('#cancel-map-pin').addEventListener('click',()=>setMapEditing(false));
  get<HTMLSelectElement>('#comparison-map-target').addEventListener('change',event=>{mapTarget=Number((event.target as HTMLSelectElement).value);if(mapEditing)setMapEditing(true);});
  map.on('click',event=>{
    if(!mapEditing)return;
    if(event.lngLat.lng<8.3||event.lngLat.lng>10.2||event.lngLat.lat<44.8||event.lngLat.lat>46.2){status.textContent='Select a location in the Milan service area.';return;}
    choose(mapTarget,`${mapTarget<0?'Destination':`Address ${mapTarget+1}`} (${event.lngLat.lat.toFixed(4)}, ${event.lngLat.lng.toFixed(4)})`,[event.lngLat.lng,event.lngLat.lat]);showStep(mapTarget<0?0:1);revealField(mapTarget);
  });
  const readSchedule=():void=>{
    settings.week=monday(get<HTMLInputElement>('#comparison-week').value);get<HTMLInputElement>('#comparison-week').value=settings.week;
    settings.arrival=get<HTMLInputElement>('#comparison-arrival').value;
    settings.departure=get<HTMLInputElement>('#comparison-departure').value;
    settings.walk=Number(get<HTMLSelectElement>('#comparison-walk').value);
    settings.days=[...root.querySelectorAll<HTMLInputElement>('.office-days input:checked')].map(el=>Number(el.value));
  };
  root.addEventListener('input',event=>{
    const target=event.target as HTMLInputElement;
    if(target.matches('[id^="address-"]')){
      const index=Number(target.id.slice(8));place(index).name=target.value;place(index).point=null;
      target.closest('.location-editor')!.querySelector('.selected-location')!.textContent='Select a suggestion to confirm this address.';
      syncMarkers();invalidate();if(index>=0)publishAddresses();
    }else if(target.matches('[data-rent]')){place(Number(target.dataset.rent)).rent=target.value===''?null:Number(target.value);invalidate();}
  });
  root.addEventListener('change',event=>{
    const target=event.target as HTMLElement;
    if((target as HTMLInputElement).dataset.enabled!==undefined){const el=target as HTMLInputElement;settings.addresses[Number(el.dataset.enabled)]!.enabled=el.checked;invalidate();publishAddresses();syncMarkers();showStep(step,false);return;}
    if(target.matches('#comparison-week,#comparison-arrival,#comparison-departure,#comparison-walk,.office-days input')){
      invalidate();if(get<HTMLInputElement>('#comparison-week').value)readSchedule();
    }
  });
  root.addEventListener('click',event=>{
    const target=(event.target as HTMLElement).closest<HTMLButtonElement>('button');if(!target)return;
    if(target.dataset.pin!==undefined){mapTarget=Number(target.dataset.pin);get<HTMLSelectElement>('#comparison-map-target').value=String(mapTarget);setMapEditing(true);status.textContent=`Click the map to set ${mapTarget<0?'the destination':`address ${mapTarget+1}`}.`;}
    if(target.dataset.remove!==undefined){invalidate();settings.addresses.splice(Number(target.dataset.remove),1);if(!settings.addresses.length)settings.addresses.push(newAddress());publishAddresses();renderFields();}
  });
  get('#add-address').addEventListener('click',()=>{if(settings.addresses.length<3){invalidate();settings.addresses.push(newAddress());publishAddresses();renderFields();revealField(settings.addresses.length-1);}});
  const validateLocations=(through:number):boolean=>{
    if(through>=1&&!included().length){showStep(1,false);status.textContent='Tick at least one address to include.';return false;}
    const missing=!settings.destination.point?-1:settings.addresses.findIndex(a=>a.enabled!==false&&!a.point);
    if(!settings.destination.point || (through>=1&&missing>=0)){
      showStep(!settings.destination.point?0:1,false);
      const index=!settings.destination.point?-1:missing;
      status.textContent=index<0?'Select a destination from the address suggestions, or choose it on the map.':`Select an address for Address ${index+1}, or remove that address if it is not needed.`;
      revealField(index);return false;
    }
    return true;
  };
  const validateFields=():boolean=>{
    const invalid=root.querySelector<HTMLInputElement>('input:invalid');
    if(!invalid)return true;
    showStep(Number(invalid.closest<HTMLElement>('[data-form-step]')!.dataset.formStep),false);
    invalid.reportValidity();return false;
  };
  const validate=():boolean=>{
    if(!validateLocations(1)||!validateFields())return false;
    readSchedule();
    if(!settings.days.length){showStep(2,false);status.textContent='Select at least one day you travel.';root.querySelector<HTMLInputElement>('.office-days input')!.focus();return false;}
    if(settings.departure<=settings.arrival){showStep(2,false);status.textContent='The time you leave work or study must be later than your arrival time on the same day.';get<HTMLInputElement>('#comparison-departure').focus();return false;}
    return true;
  };
  const renderResults=(data:Comparison):void=>{
    clearRouteViews();
    const range=(address:AddressResult,key:'seconds'|'walking_seconds'|'transfers',direction:'outbound'|'return'):string=>{
      const journeys=address.days.map(d=>d[direction]);if(!journeys.length||journeys.some(j=>!j))return `<span class="unavailable" title="Open daily journeys to see missing dates or routes">${journeys.some(Boolean)?'Incomplete':'Unavailable'}</span>`;
      const values=journeys.map(j=>j![key]),low=Math.min(...values),high=Math.max(...values);
      const format=(v:number):string=>key==='transfers'?String(v):duration(v);
      return low===high?format(low):`${format(low)} – ${format(high)}`;
    };
    const row=(title:string,fn:(a:AddressResult)=>string):string=>`<tr><th scope="row">${title}</th>${data.addresses.map(a=>`<td>${fn(a)}</td>`).join('')}</tr>`;
    get('#comparison-results').innerHTML=`<div class="comparison-results-header"><div><h2 id="comparison-results-title" tabindex="-1">Scheduled journeys</h2><p>${escape(settings.destination.name)} · Week of ${settings.week} · ${settings.days.length} office days · Timetable published ${data.snapshot_date}</p></div><button type="button" id="export-comparison" class="secondary-button">Export CSV</button></div>
      <div class="comparison-table-wrap"><table class="comparison-table"><caption class="visually-hidden">Scheduled address commute comparison</caption><thead><tr><th scope="col">Measure</th>${data.addresses.map((a,i)=>`<th scope="col" title="${escape(a.name)}">${escape(a.name)}</th>`).join('')}</tr></thead><tbody>
      ${row('Morning journey',a=>range(a,'seconds','outbound'))}${row('Return journey',a=>range(a,'seconds','return'))}
      ${row('Morning walking',a=>range(a,'walking_seconds','outbound'))}${row('Return walking',a=>range(a,'walking_seconds','return'))}
      ${row('Morning transfers',a=>range(a,'transfers','outbound'))}${row('Return transfers',a=>range(a,'transfers','return'))}
      ${row('Total travel',a=>a.weekly_seconds===null?'<span class="unavailable" title="Some dates or journeys are unavailable">Incomplete</span>':duration(a.weekly_seconds))}</tbody></table></div>
      <p class="results-guide">Ranges cover selected days. Select an address below for daily journeys and route maps.</p><div class="journey-detail-actions">${data.addresses.map((a,i)=>`<button type="button" class="secondary-button" data-address-journeys="${i}" title="${escape(a.name)}">${i+1}. ${escape(a.name)}<small>Daily journeys & routes</small></button>`).join('')}</div>`;
    get('#export-comparison').addEventListener('click',()=>{
      const rows=[['Address','Date','Morning seconds','Return seconds','Weekly seconds','Status'],
        ...data.addresses.flatMap(a=>a.days.map(d=>[a.name,d.date,d.outbound?.seconds??'',d.return?.seconds??'',a.weekly_seconds??'',d.status]))];
      const csv=rows.map(row=>row.map(value=>`"${String(value).replace(/^[=+@\-\t\r\n ]/,"'$&").replace(/"/g,'""')}"`).join(',')).join('\r\n');
      const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download='milan-address-comparison.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
  };
  root.addEventListener('click',event=>{
    const el=(event.target as HTMLElement).closest<HTMLButtonElement>('[data-address-journeys]');if(!el||!results)return;
    const a=results.addresses[Number(el.dataset.addressJourneys)]!;clearRouteViews();
    journeyDialog.body.innerHTML=`<div class="daily-journey-list">${a.days.map(d=>`<details><summary>${dateLabel(d.date)} — ${d.status==='ready'?(d.outbound&&d.return?`${duration(d.outbound.seconds+d.return.seconds)} total`:'No complete round trip'):'Timetable unavailable'}</summary>${d.status!=='ready'?`<p>${escape(d.reason)}</p>`:`<h3>Morning</h3>${journeyMarkup(d.outbound)}<h3>Return</h3>${journeyMarkup(d.return)}<h3>Return departure 10 minutes later</h3>${d.return_later?`<p>Arrive ${clock(d.return_later.arrival)} · ${duration(d.return_later.seconds)} travel${d.return?` · arrival ${duration(Math.max(0,d.return_later.arrival-d.return.arrival))} later`:''}.</p>`:'<p>No journey found within the 90-minute search window.</p>'}`}</details>`).join('')}</div>`;
    journeyDialog.open(a.name);
  });
  const journeyMarkup=(journey:Journey|null):string=>{
    if(!journey)return '<p>No journey found within 90 minutes using the selected walking limit.</p>';
    const index=journeyMaps.push(journey.legs)-1;
    return `<p>${clock(journey.departure)} – ${clock(journey.arrival)} · ${duration(journey.seconds)} · ${journey.transfers} transfers · ${duration(journey.walking_seconds)} walking</p><button type="button" class="secondary-button" data-route-view="${index}">Show route map</button><div class="journey-route-window" hidden></div><ol class="journey-legs">${journey.legs.map(l=>`<li><span>${clock(l.departure)} – ${clock(l.arrival)}</span><strong>${l.mode==='walk'?'Walk':`Line ${escape(l.route)}`}</strong><p>${escape(l.from)} → ${escape(l.to)}</p></li>`).join('')}</ol>`;
  };
  root.addEventListener('click',event=>{
    const el=(event.target as HTMLElement).closest<HTMLButtonElement>('[data-route-view]');if(!el)return;
    const window=el.nextElementSibling as HTMLElement;
    if(window.childElementCount){window.hidden=!window.hidden;el.textContent=window.hidden?'Show route map':'Hide route map';return;}
    window.hidden=false;el.textContent='Hide route map';routeViews.push(createRouteView(window,journeyMaps[Number(el.dataset.routeView)]!,results?.pipeline_run_id));
  });
  get<HTMLFormElement>('#comparison-form').addEventListener('submit',async event=>{
    event.preventDefault();if(!validate())return;invalidate();const token=version;controller=new AbortController();button.disabled=true;status.textContent='Calculating street access and scheduled journeys for the selected dates…';
    try{
      const response=await fetch('/api/comparison',{method:'POST',headers:{'Content-Type':'application/json','X-Mobility-Action':'compare'},body:JSON.stringify({...settings,addresses:included()}),signal:controller.signal});
      const data=await response.json() as Comparison & {error?:string};if(!response.ok)throw new Error(data.error??'Comparison unavailable.');if(version!==token)return;
      results=data;renderResults(data);root.classList.remove('settings-open');get('.planner-settings-toggle').setAttribute('aria-expanded','false');if(!root.hidden){get('#comparison-results-title').focus({preventScroll:true});}status.textContent=data.addresses.every(a=>a.weekly_seconds!==null)?'Comparison complete.':'Some dates or journeys are unavailable. See the daily results; incomplete weeks are not totalled.';
    }catch(error){if(version===token)status.textContent=error instanceof Error?error.message:'Comparison unavailable.';}finally{if(version===token)button.disabled=false;}
  });
  get('#save-comparison').addEventListener('click',()=>{if(!validate())return;try{localStorage.setItem('milano-commute-settings',JSON.stringify(settings));status.textContent='Comparison settings saved in this browser.';}catch{status.textContent='Browser storage is unavailable. Use the comparison link to retain these settings.';}});
  get('#share-comparison').addEventListener('click',async()=>{
    if(!validate())return;
    const encoded=btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(settings)))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
    const link=`${location.origin}${location.pathname}#comparison=${encoded}`;
    try{await navigator.clipboard.writeText(link);status.textContent='Comparison link copied. It includes your selected locations and schedule.';}
    catch{const input=get<HTMLInputElement>('#comparison-share-link');input.hidden=false;input.value=link;input.select();status.textContent='Copy the comparison link from the field below.';}
  });
  const unwatch=watchAddresses('comparison',()=>{settings.addresses=getAddresses();invalidate();renderFields();});
  return {
    updateCoverage(days):void {
      const available=days.map(d=>d.service_date).sort(),last=available.at(-1);
      get('#comparison-coverage').textContent=last?`Timetable coverage: ${available[0]} to ${last}.${last<localDate()?' The timetable has expired. Update the data before planning current travel.':''}`:'Timetable coverage is temporarily unavailable.';
      invalidate();status.textContent='Timetable coverage changed. Recalculate the comparison.';
    },
    destroy():void{unwatch();locationDialog.destroy();journeyDialog.destroy();infoDialog.destroy();clearRouteViews();version++;controller?.abort();addressEditors.forEach(editor=>editor.destroy());observer.disconnect();markers.forEach(m=>m.remove());map.remove();results=null;}
  };
}
