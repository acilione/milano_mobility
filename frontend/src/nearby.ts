import {createAddressAutocomplete} from './autocomplete';
import {getAddresses,setAddresses,newAddress,watchAddresses, type Address} from './addresses';
import {createRouteView} from './route-view';

const categories=[['cafe','Cafés'],['supermarket','Supermarkets'],['cinema','Cinemas'],['pharmacy','Pharmacies'],['restaurant','Restaurants'],['park','Parks'],['post_office','Post offices'],['bank','Banks / ATMs'],['healthcare','Healthcare'],['gym','Gyms']];
const esc=(value:unknown):string=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]!);
interface Place {id:string;name:string;point:[number,number];address:string;seconds:number;opening_hours:string;osm_url:string}
interface Group {mapped_count:number;sampled:boolean;reachable_count:number;checked_count:number;unknown_count:number;nearest_seconds:number|null;places:Place[]}
interface AddressResult {name:string;point:[number,number];status:string;reason?:string;categories:Record<string,Group>;retrieved_at:string;osm_timestamp?:string}
interface Result {addresses:AddressResult[];categories:{id:string;label:string}[];radius:number;minutes:number;candidate_limit:number}
const identity=(a:{name:string;point:[number,number]|null}):string=>JSON.stringify([a.name,a.point]);

export function createNearby(root:HTMLElement):{destroy():void} {
  let addresses=getAddresses(),request:AbortController|undefined,version=0,data:Result|undefined;
  let editors:{destroy():void}[]=[],routes:{destroy():void}[]=[];
  root.innerHTML=`<div class="comparison-title"><h2>Compare nearby places</h2><p>Add up to three addresses and tick the ones to compare. Choose the types of places that matter to you, then compare how many you can reach on foot and the closest walking time.</p></div>
    <form id="nearby-form" class="nearby-controls"><fieldset><legend>1. Addresses to compare</legend><p class="field-help">This address list is shared with Journey comparison. Addresses are saved in this browser when storage is available. Untick an address to exclude it without deleting it.</p><div id="nearby-addresses" class="nearby-addresses"></div><button type="button" id="nearby-add" class="secondary-button">Add address</button></fieldset>
    <fieldset><legend>2. Places and walking limits</legend><div class="nearby-categories">${categories.map(([id,label])=>`<label><input type="checkbox" name="category" value="${id}" ${['cafe','supermarket','cinema','pharmacy','park','post_office'].includes(id!)?'checked':''}>${label}</label>`).join('')}</div>
    <div class="nearby-limits"><label>Maximum walk<select id="nearby-minutes">${[5,10,15,20].map(n=>`<option value="${n}" ${n===15?'selected':''}>${n} minutes</option>`).join('')}</select></label><label>Search radius<select id="nearby-radius">${[500,1000,1500].map(n=>`<option value="${n}" ${n===1000?'selected':''}>${n} metres</option>`).join('')}</select></label></div>
    <p class="field-help">The radius limits where we look for mapped places; the walking limit checks actual street routes. For each category we check up to the 20 nearest mapped places. Counts describe this shortlist, not every business in the area.</p></fieldset>
    <button class="commute-submit" id="nearby-compare" type="submit">Compare nearby places</button><p id="nearby-status" role="status" aria-live="polite"></p></form>
    <div class="nearby-data-note"><details><summary>Coverage and data sources</summary><p>Places come from OpenStreetMap via Overpass and may be missing or out of date. Search results are cached for the day. Walking times use pedestrian street routes; mapped building centres may differ from entrances. Opening hours are shown as recorded, not verified live. No accessibility or step-free guarantee is made.</p><p>Selected coordinates are sent to Overpass and the pedestrian routing provider. <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap attribution</a></p></details></div>
    <section id="nearby-results" aria-label="Nearby place comparison"><p class="comparison-empty">Select at least one address and category, then compare nearby places.</p></section>`;
  const get=<T extends HTMLElement>(selector:string):T=>root.querySelector<T>(selector)!;
  const status=get('#nearby-status'),button=get<HTMLButtonElement>('#nearby-compare');
  const selectedCategories=():string[]=>[...root.querySelectorAll<HTMLInputElement>('[name=category]:checked')].map(el=>el.value);
  const disposeRoutes=():void=>{routes.forEach(r=>r.destroy());routes=[];};
  const invalidate=(retain=false):void=>{
    version++;request?.abort();button.disabled=false;disposeRoutes();
    if(!retain)data=undefined;
    get('#nearby-results').innerHTML='<p class="comparison-empty">Selection changed. Compare nearby places to update the results.</p>';
    status.textContent='';
  };
  const persist=():void=>setAddresses(addresses,'nearby');
  const renderAddresses=():void=>{
    editors.forEach(e=>e.destroy());editors=[];
    get('#nearby-addresses').innerHTML=addresses.map((a,i)=>`<div class="location-editor nearby-address" data-location="${i}"><label class="address-include"><input data-enabled="${i}" type="checkbox" ${a.enabled?'checked':''}> Include Address ${i+1}</label><label for="nearby-address-${i}">Street and house number</label><div class="address-search"><input id="nearby-address-${i}" data-address="${i}" type="search" maxlength="200" value="${esc(a.name)}" placeholder="Street, house number, Milan" autocomplete="off" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="nearby-options-${i}" aria-describedby="nearby-feedback-${i}"><button class="secondary-button" type="button" data-search="${i}">Search</button></div><div id="nearby-options-${i}" class="address-results" role="listbox" aria-label="Milan address suggestions" hidden></div><p id="nearby-feedback-${i}" class="address-feedback" role="status"></p><p class="selected-location">${a.point?'Location selected.':'Type at least 3 characters, then select a suggestion.'}</p><button class="map-location-button" type="button" data-delete="${i}" ${addresses.length===1?'disabled':''}>Remove address</button></div>`).join('');
    get<HTMLButtonElement>('#nearby-add').disabled=addresses.length>=3;
    root.querySelectorAll<HTMLElement>('.nearby-address').forEach((el,i)=>editors.push(createAddressAutocomplete(el,place=>{
      addresses[i]!.name=place.name;addresses[i]!.point=place.point;invalidate();persist();renderAddresses();get<HTMLInputElement>(`#nearby-address-${i}`).focus();
    })));
  };
  const renderResults=():void=>{
    if(!data)return;
    const selected=addresses.filter(a=>a.enabled),cats=selectedCategories();
    const rows=selected.map(a=>data!.addresses.find(r=>identity(r)===identity(a)));
    disposeRoutes();
    if(!selected.length||!cats.length){get('#nearby-results').innerHTML='<p class="comparison-empty">Tick at least one address and one category to display a comparison.</p>';return;}
    if(rows.some(a=>!a)||cats.some(c=>!data!.categories.some(d=>d.id===c))){get('#nearby-results').innerHTML='<p class="comparison-empty">This selection has not been checked. Compare nearby places to load it.</p>';return;}
    const addressesData=rows as AddressResult[];
    const cell=(a:AddressResult,c:string):string=>{
      if(a.status!=='ready')return '<span class="unavailable">Data unavailable</span>';
      const g=a.categories[c]!;
      return `<strong>${g.reachable_count} within ${data!.minutes} min</strong><small>${g.nearest_seconds===null?'No verified walking time':`Closest checked: ${Math.ceil(g.nearest_seconds/60)} min`}</small><small>${g.checked_count} checked / ${g.mapped_count} mapped${g.sampled?' · shortlist limited':''}${g.unknown_count?` · ${g.unknown_count} routes unknown`:''}</small>`;
    };
    get('#nearby-results').innerHTML=`<h2 id="nearby-results-title" tabindex="-1">Nearby place comparison</h2><p class="results-guide">${data.minutes}-minute walk · ${data.radius} m search radius. Counts include only checked places reachable by street routes. Expand a category below to see names and walking directions.</p>
      <div class="comparison-table-wrap"><table class="comparison-table nearby-table"><caption>Walking access by category</caption><thead><tr><th scope="col">Category</th>${addressesData.map(a=>`<th scope="col">${esc(a.name)}</th>`).join('')}</tr></thead><tbody>${cats.map(c=>`<tr><th scope="row">${esc(categories.find(([id])=>id===c)![1])}</th>${addressesData.map(a=>`<td>${cell(a,c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
      <div class="nearby-place-groups">${cats.map(c=>`<details><summary>${esc(categories.find(([id])=>id===c)![1])} — places and routes</summary><div class="nearby-place-columns">${addressesData.map((a,i)=>`<article><h3>${esc(a.name)}</h3>${a.status!=='ready'?`<p>${esc(a.reason)}</p>`:a.categories[c]!.places.length?a.categories[c]!.places.map(p=>`<div class="nearby-place"><strong>${esc(p.name)}</strong><span>${Math.ceil(p.seconds/60)} min walk</span>${p.address?`<p>${esc(p.address)}</p>`:''}${p.opening_hours?`<p>Recorded hours: ${esc(p.opening_hours)}</p>`:'<p>Opening hours not recorded.</p>'}<a href="${esc(p.osm_url)}" target="_blank" rel="noopener">Place details</a><button class="secondary-button" type="button" data-route-address="${i}" data-route-category="${c}" data-route-place="${esc(p.id)}">Show walking route</button><div class="nearby-route-window" hidden></div></div>`).join(''):'<p>No checked places have a verified route within the walking limit. This does not establish that none exist nearby.</p>'}</article>`).join('')}</div></details>`).join('')}</div>
      <p class="field-help">${addressesData.filter(a=>a.status==='ready').map(a=>`${esc(a.name)}: retrieved ${esc(new Date(a.retrieved_at).toLocaleString())}`).join(' · ')}</p>`;
    get('#nearby-results').querySelectorAll<HTMLButtonElement>('[data-route-place]').forEach(el=>el.addEventListener('click',()=>{
      const window=el.nextElementSibling as HTMLElement;
      if(window.childElementCount){window.hidden=!window.hidden;el.textContent=window.hidden?'Show walking route':'Hide walking route';return;}
      const address=addressesData[Number(el.dataset.routeAddress)]!,place=address.categories[el.dataset.routeCategory!]!.places.find(p=>p.id===el.dataset.routePlace)!;
      window.hidden=false;el.textContent='Hide walking route';routes.push(createRouteView(window,[{mode:'walk',coordinates:[address.point,place.point]}],undefined,true));
    }));
  };
  renderAddresses();
  const unwatch=watchAddresses('nearby',()=>{addresses=getAddresses();invalidate(true);renderAddresses();renderResults();});
  root.addEventListener('input',event=>{
    const el=event.target as HTMLInputElement;if(el.dataset.address===undefined)return;
    const a=addresses[Number(el.dataset.address)]!;a.name=el.value;a.point=null;el.closest('.location-editor')!.querySelector('.selected-location')!.textContent='Select a suggestion to confirm this address.';invalidate();persist();
  });
  root.addEventListener('change',event=>{
    const el=event.target as HTMLInputElement;
    if(el.dataset.enabled!==undefined){addresses[Number(el.dataset.enabled)]!.enabled=el.checked;invalidate(true);persist();renderResults();}
    else if(el.name==='category'){invalidate(true);renderResults();}
    else if(el.id==='nearby-radius'||el.id==='nearby-minutes')invalidate();
  });
  root.addEventListener('click',event=>{
    const el=(event.target as HTMLElement).closest<HTMLButtonElement>('[data-delete]');if(!el||addresses.length<=1)return;
    addresses.splice(Number(el.dataset.delete),1);invalidate(true);persist();renderAddresses();renderResults();
  });
  get('#nearby-add').addEventListener('click',()=>{if(addresses.length>=3)return;addresses.push(newAddress());invalidate(true);persist();renderAddresses();get<HTMLInputElement>(`#nearby-address-${addresses.length-1}`).focus();});
  get<HTMLFormElement>('#nearby-form').addEventListener('submit',async event=>{
    event.preventDefault();const selected=addresses.filter(a=>a.enabled),cats=selectedCategories();
    if(!selected.length){status.textContent='Tick at least one address.';return;}
    const missing=selected.find(a=>!a.point);if(missing){status.textContent='Select a street suggestion for every included address.';get<HTMLInputElement>(`#nearby-address-${addresses.indexOf(missing)}`).focus();return;}
    if(!cats.length){status.textContent='Select at least one category.';return;}
    invalidate();request=new AbortController();const token=version;button.disabled=true;status.textContent='Finding mapped places and checking walking routes. This can take up to a minute per address.';
    try{
      const response=await fetch('/api/nearby',{method:'POST',headers:{'Content-Type':'application/json','X-Mobility-Action':'explore'},signal:request.signal,body:JSON.stringify({addresses:selected,categories:cats,radius:Number(get<HTMLSelectElement>('#nearby-radius').value),minutes:Number(get<HTMLSelectElement>('#nearby-minutes').value)})});
      const result=await response.json();if(!response.ok)throw new Error(result.error??'Comparison unavailable.');if(token!==version)return;
      data=result;renderResults();status.textContent=data!.addresses.every(a=>a.status==='ready')?'Nearby comparison complete.':'Some addresses could not be checked. Their data is marked unavailable; retry to refresh.';
      if(!root.hidden){get('#nearby-results-title').focus({preventScroll:true});get('#nearby-results').scrollIntoView({behavior:'smooth',block:'start'});}
    }catch(error){if(token===version)status.textContent=error instanceof Error?error.message:'Comparison unavailable.';}finally{if(token===version)button.disabled=false;}
  });
  return {destroy():void{version++;request?.abort();unwatch();editors.forEach(e=>e.destroy());disposeRoutes();}};
}
