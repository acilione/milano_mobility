import {createAddressAutocomplete} from './autocomplete';
import {getAddresses,setAddresses,newAddress,watchAddresses} from './addresses';
import {createRouteView} from './route-view';
import categoryCatalog from '../../ingestion/milano_mobility/category_rules.json';

const categories=categoryCatalog.categories.map(c=>[c.id,c.label]);
const definition=(id:string):string=>categoryCatalog.categories.find(c=>c.id===id)!.description;
const esc=(value:unknown):string=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]!);
interface Place {id:string;name:string;point:[number,number];address:string;seconds:number;opening_hours:string;osm_url:string;classification?:Record<string,Record<string,string>>;source_tags?:Record<string,string>}
interface Group {mapped_count:number;sampled:boolean;reachable_count:number;checked_count:number;unknown_count:number;nearest_seconds:number|null;places:Place[]}
interface AddressResult {name:string;point:[number,number];status:string;reason?:string;categories:Record<string,Group>;retrieved_at:string;osm_timestamp?:string}
interface Result {addresses:AddressResult[];categories:{id:string;label:string}[];radius:number;minutes:number;candidate_limit:number}
const identity=(a:{name:string;point:[number,number]|null}):string=>JSON.stringify([a.name,a.point]);

export function createNearby(root:HTMLElement):{destroy():void} {
  let addresses=getAddresses(),request:AbortController|undefined,version=0,data:Result|undefined;
  let editor:{destroy():void}|undefined,route:{destroy():void}|undefined;
  root.innerHTML=`<div class="nearby-heading"><div><h2>Nearby places</h2><p>Compare up to three Milan addresses.</p></div><button type="button" class="secondary-button nearby-settings-toggle" aria-expanded="false" aria-controls="nearby-form">Addresses & filters</button></div>
    <div class="nearby-workspace"><form id="nearby-form" class="nearby-controls"><fieldset><legend>Addresses</legend><div id="nearby-addresses" class="nearby-addresses"></div><button type="button" id="nearby-add" class="map-location-button">+ Add address</button></fieldset>
    <fieldset><legend>Categories</legend><div class="nearby-categories">${categories.map(([id,label])=>`<label title="${esc(definition(id!))}"><input type="checkbox" name="category" value="${id}" ${['cafe','supermarket','cinema','bookshop','library','pharmacy','park','post_office'].includes(id!)?'checked':''}>${label}</label>`).join('')}</div></fieldset>
    <div class="nearby-limits"><label>Maximum walk<select id="nearby-minutes">${[5,10,15,20].map(n=>`<option value="${n}" ${n===15?'selected':''}>${n} min</option>`).join('')}</select></label><label>Search radius<select id="nearby-radius">${[500,1000,1500].map(n=>`<option value="${n}" ${n===1000?'selected':''}>${n} m</option>`).join('')}</select></label></div>
    <button class="commute-submit" id="nearby-compare" type="submit">Compare places</button><p id="nearby-status" role="status" aria-live="polite"></p></form>
    <section id="nearby-results" aria-label="Nearby place comparison"></section></div>
    <dialog id="nearby-dialog" class="nearby-dialog" aria-labelledby="nearby-dialog-title"><div class="nearby-dialog-heading"><h2 id="nearby-dialog-title"></h2><button type="button" class="secondary-button" data-close>Close</button></div><div id="nearby-dialog-body"></div></dialog>`;
  const get=<T extends HTMLElement>(selector:string):T=>root.querySelector<T>(selector)!;
  const status=get('#nearby-status'),button=get<HTMLButtonElement>('#nearby-compare'),dialog=get<HTMLDialogElement>('#nearby-dialog');
  const selectedCategories=():string[]=>[...root.querySelectorAll<HTMLInputElement>('[name=category]:checked')].map(el=>el.value);
  const closeDetails=():void=>{dialog.close();editor?.destroy();editor=undefined;route?.destroy();route=undefined;};
  dialog.addEventListener('close',()=>{editor?.destroy();editor=undefined;route?.destroy();route=undefined;});
  get('[data-close]').addEventListener('click',closeDetails);
  const openDialog=(title:string,body:string):void=>{
    closeDetails();get('#nearby-dialog-title').textContent=title;get('#nearby-dialog-body').innerHTML=body;dialog.showModal();
  };
  const persist=():void=>setAddresses(addresses,'nearby');
  const renderAddresses=():void=>{
    get('#nearby-addresses').innerHTML=addresses.map((a,i)=>`<div class="nearby-address-summary"><label title="Include address ${i+1}"><input data-enabled="${i}" type="checkbox" aria-label="Include address ${i+1}: ${esc(a.name||'Not selected')}" ${a.enabled?'checked':''}><span>${i+1}</span></label><button type="button" data-edit="${i}" title="${esc(a.name||'Select an address')}">${esc(a.name||'Select an address')}<small>${a.point?'Edit address':'Choose a Milan street'}</small></button></div>`).join('');
    get<HTMLButtonElement>('#nearby-add').disabled=addresses.length>=3;
  };
  const coverageNote=():string=>`<p class="nearby-method">Up to 20 nearest mapped places checked per category. Counts may be incomplete. <button type="button" class="map-location-button" data-coverage>Categories & data</button></p>`;
  const renderResults=():void=>{
    const selected=addresses.filter(a=>a.enabled),cats=selectedCategories();
    const rows=selected.map(a=>data?.addresses.find(r=>identity(r)===identity(a)));
    const complete=!!data&&rows.every(a=>a)&&cats.every(c=>data!.categories.some(d=>d.id===c));
    const ready=complete&&selected.length>0&&cats.length>0;
    const cell=(a:AddressResult|undefined,c:string,index:number,best:number|null):string=>{
      if(!ready)return '<span class="nearby-pending" aria-label="Not checked">—</span>';
      if(a!.status!=='ready')return `<button type="button" class="nearby-metric unavailable" data-details="${index}" data-category="${c}" aria-label="${esc(a!.name)}: data unavailable">Unavailable</button>`;
      const g=a!.categories[c]!,uncertain=g.sampled||g.unknown_count>0;
      return `<button type="button" class="nearby-metric ${best!==null&&g.nearest_seconds===best?'nearest-best':''}" data-details="${index}" data-category="${c}" aria-label="${esc(categories.find(([id])=>id===c)![1])}, ${esc(a!.name)}: ${g.reachable_count} checked places within ${data!.minutes} minutes; ${g.nearest_seconds===null?'nearest time unknown':`nearest ${Math.ceil(g.nearest_seconds/60)} minutes`}${uncertain?'; incomplete coverage':''}. View places and routes"><strong>${g.reachable_count}${uncertain?'<sup title="Limited shortlist or unknown routes">*</sup>':''}</strong><span>${g.nearest_seconds===null?'—':`${Math.ceil(g.nearest_seconds/60)}<small> min</small>`}</span></button>`;
    };
    get('#nearby-results').innerHTML=`<div class="nearby-result-heading"><h3 id="nearby-results-title" tabindex="-1">Walking access</h3><span>${ready?`${data!.minutes} min walk · ${data!.radius} m radius`:'Select addresses, then compare'}</span></div>
      <p class="nearby-legend">${ready?'Select a cell for places and routes. <span class="nearest-key">● Shortest verified walk</span>':'Tick addresses and categories to include them in this view.'}</p>
      ${!selected.length||!cats.length?'<p class="comparison-empty">Tick at least one address and one category to display a comparison.</p>':`<div class="comparison-table-wrap"><table class="comparison-table nearby-table"><caption class="visually-hidden">Walking access by category. Each cell shows reachable places and nearest verified walking time.</caption><thead><tr><th scope="col">Category</th>${selected.map((a,i)=>`<th scope="col"><span class="nearby-column-address" title="${esc(a.name)}">${addresses.indexOf(a)+1}. ${esc(a.name||'Address not selected')}</span><span class="nearby-column-key"><span>Places</span><span>Nearest</span></span></th>`).join('')}</tr></thead><tbody>${cats.map(c=>{
        const times=ready?rows.filter(a=>a?.status==='ready').map(a=>a!.categories[c]!).filter(g=>!g.unknown_count&&!g.sampled&&g.nearest_seconds!==null).map(g=>g.nearest_seconds!):[];
        // Only compare minima when every address has complete, verified coverage.
        const best=times.length===rows.length&&rows.length>1?Math.min(...times):null;
        return `<tr><th scope="row">${esc(categories.find(([id])=>id===c)![1])}</th>${rows.map((a,i)=>`<td>${cell(a,c,i,best)}</td>`).join('')}</tr>`;
      }).join('')}</tbody></table></div>`}
      ${ready?'<p class="nearby-table-note">Places = verified within the walking limit. Nearest may be outside it. * Limited shortlist or unknown routes. — No verified time.</p>':'<p class="nearby-table-note">Results appear here after comparison. New addresses or categories require a new lookup.</p>'}${coverageNote()}`;
  };
  const invalidate=(retain=false):void=>{
    version++;request?.abort();button.disabled=false;
    if(!retain)data=undefined;
    status.textContent='';renderResults();
  };
  const editAddress=(index:number):void=>{
    const a=addresses[index]!;
    openDialog(`Address ${index+1}`,`<div class="location-editor nearby-address"><label for="nearby-address-${index}">Street and house number</label><div class="address-search"><input id="nearby-address-${index}" data-address="${index}" type="search" maxlength="200" value="${esc(a.name)}" placeholder="Street, house number, Milan" autocomplete="off" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="nearby-options-${index}" aria-describedby="nearby-feedback-${index}"><button class="secondary-button" type="button" data-search>Search</button></div><div id="nearby-options-${index}" class="address-results" role="listbox" aria-label="Milan address suggestions" hidden></div><p id="nearby-feedback-${index}" class="address-feedback" role="status"></p><p class="selected-location">${a.point?'Location selected.':'Type at least 3 characters, then select a suggestion.'}</p><p class="field-help">Addresses are shared with Journey comparison and saved in this browser.</p><button class="map-location-button" type="button" data-delete="${index}" ${addresses.length===1?'disabled':''}>Remove address</button></div>`);
    editor=createAddressAutocomplete(get('.nearby-address'),place=>{
      a.name=place.name;a.point=place.point;invalidate();persist();renderAddresses();closeDetails();get<HTMLButtonElement>(`[data-edit="${index}"]`).focus();
    });
    get<HTMLInputElement>(`#nearby-address-${index}`).focus();
  };
  renderAddresses();renderResults();
  const unwatch=watchAddresses('nearby',()=>{closeDetails();addresses=getAddresses();invalidate(true);renderAddresses();});
  root.addEventListener('input',event=>{
    const el=event.target as HTMLInputElement;if(el.dataset.address===undefined)return;
    const a=addresses[Number(el.dataset.address)]!;a.name=el.value;a.point=null;el.closest('.location-editor')!.querySelector('.selected-location')!.textContent='Select a suggestion to confirm this address.';invalidate();persist();renderAddresses();
  });
  root.addEventListener('change',event=>{
    const el=event.target as HTMLInputElement;
    if(el.dataset.enabled!==undefined){addresses[Number(el.dataset.enabled)]!.enabled=el.checked;invalidate(true);persist();}
    else if(el.name==='category')invalidate(true);
    else if(el.id==='nearby-radius'||el.id==='nearby-minutes')invalidate();
  });
  root.addEventListener('click',event=>{
    const el=(event.target as HTMLElement).closest<HTMLButtonElement>('button');if(!el)return;
    if(el.dataset.edit!==undefined){editAddress(Number(el.dataset.edit));return;}
    if(el.dataset.delete!==undefined&&addresses.length>1){addresses.splice(Number(el.dataset.delete),1);closeDetails();invalidate(true);persist();renderAddresses();get('#nearby-add').focus();return;}
    if(el.hasAttribute('data-coverage')){openDialog('Category definitions and data',`<div class="nearby-data-note"><p>Places come from OpenStreetMap via Overpass and may be missing or out of date. Search results are cached for the day.</p><p>Each category requires explicit evidence of the service offered. Names, brands and broad facility types alone do not determine a category. Ambiguous, unsupported, explicitly inactive and private/no-access entries are excluded. Source tags can still be incorrect.</p><details class="category-definitions"><summary>Definitions for all categories</summary><dl>${categoryCatalog.categories.map(c=>`<dt>${esc(c.label)}</dt><dd>${esc(c.description)}</dd>`).join('')}</dl></details><p>The radius limits mapped places; walking times check street routes to up to 20 nearest mapped places per category. Counts describe this shortlist, not every business. Unavailable data is never counted as zero.</p><p>Green marks the shortest verified walk only when all compared addresses have complete route checks and no capped shortlists. Equal times are highlighted together. It does not rank overall neighbourhood quality.</p><p>Mapped centres may differ from entrances. Opening hours are recorded, not verified live. Routes may not be step-free. Selected coordinates are sent to Overpass and the pedestrian routing provider.</p><p><a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap attribution</a></p>${data?.addresses.filter(a=>a.status==='ready').map(a=>`<p>${esc(a.name)}: retrieved ${esc(new Date(a.retrieved_at).toLocaleString())}</p>`).join('')||''}</div>`);return;}
    if(el.dataset.details!==undefined&&data){
      const selected=addresses.filter(a=>a.enabled),a=data.addresses.find(r=>identity(r)===identity(selected[Number(el.dataset.details)]!))!,c=el.dataset.category!,g=a.categories?.[c];
      openDialog(`${categories.find(([id])=>id===c)![1]} · ${a.name}`,a.status!=='ready'?`<p class="unavailable">${esc(a.reason||'Data unavailable. Please retry the comparison.')}</p>`:`<p class="field-help">${esc(definition(c))}</p><p class="field-help">${g!.reachable_count} within ${data.minutes} min · ${g!.checked_count} checked / ${g!.mapped_count} mapped${g!.sampled?' · shortlist limited':''}${g!.unknown_count?` · ${g!.unknown_count} routes unknown`:''}</p><div class="nearby-detail-layout"><div class="nearby-place-list">${g!.places.length?g!.places.map((p,i)=>`<article class="nearby-place"><div><strong>${esc(p.name)}</strong><span>${Math.ceil(p.seconds/60)} min walk</span></div>${p.classification?.[c]?`<p class="place-classification">Mapped type: ${esc([...new Set(Object.values(p.classification[c]!))].join(' · ').replaceAll('_',' '))}</p>`:''}${p.source_tags?.access?`<p>Recorded access: ${esc(p.source_tags.access)}</p>`:''}${p.address?`<p>${esc(p.address)}</p>`:''}<p>${p.opening_hours?`Recorded hours: ${esc(p.opening_hours)}`:'Opening hours not recorded.'}</p><a href="${esc(p.osm_url)}" target="_blank" rel="noopener">Place details</a><button class="secondary-button" type="button" data-route-place="${i}">Show route</button></article>`).join(''):'<p>No checked places have a verified route within this walking limit. Nearby places may still exist.</p>'}</div><div class="nearby-route-window"><p>Select a place to view its walking route.</p></div></div>`);
      get('#nearby-dialog-body').querySelectorAll<HTMLButtonElement>('[data-route-place]').forEach(b=>b.addEventListener('click',()=>{
        route?.destroy();get('#nearby-dialog-body').querySelectorAll('[data-route-place]').forEach(other=>other.setAttribute('aria-pressed',String(other===b)));
        const p=g!.places[Number(b.dataset.routePlace)]!;route=createRouteView(get('.nearby-route-window'),[{mode:'walk',coordinates:[a.point,p.point]}],undefined,true);
      }));
    }
  });
  get('.nearby-settings-toggle').addEventListener('click',()=>{
    const expanded=root.classList.toggle('settings-open');get('.nearby-settings-toggle').setAttribute('aria-expanded',String(expanded));
  });
  get('#nearby-add').addEventListener('click',()=>{if(addresses.length>=3)return;addresses.push(newAddress());invalidate(true);persist();renderAddresses();editAddress(addresses.length-1);});
  get<HTMLFormElement>('#nearby-form').addEventListener('submit',async event=>{
    event.preventDefault();const selected=addresses.filter(a=>a.enabled),cats=selectedCategories();
    if(!selected.length){status.textContent='Tick at least one address.';return;}
    const missing=selected.find(a=>!a.point);if(missing){status.textContent='Select a suggestion for every included address.';editAddress(addresses.indexOf(missing));return;}
    if(!cats.length){status.textContent='Select at least one category.';return;}
    invalidate();request=new AbortController();const token=version;button.disabled=true;status.textContent='Checking places and walking routes. Allow up to a minute per address.';
    try{
      const response=await fetch('/api/nearby',{method:'POST',headers:{'Content-Type':'application/json','X-Mobility-Action':'explore'},signal:request.signal,body:JSON.stringify({addresses:selected,categories:cats,radius:Number(get<HTMLSelectElement>('#nearby-radius').value),minutes:Number(get<HTMLSelectElement>('#nearby-minutes').value)})});
      const result=await response.json();if(!response.ok)throw new Error(result.error??'Comparison unavailable.');if(token!==version)return;
      data=result;renderResults();status.textContent=data!.addresses.every(a=>a.status==='ready')?'Comparison updated.':'Some data is unavailable. Retry to refresh.';
      root.classList.remove('settings-open');get('.nearby-settings-toggle').setAttribute('aria-expanded','false');
      if(!root.hidden)get('#nearby-results-title').focus({preventScroll:true});
    }catch(error){if(token===version)status.textContent=error instanceof Error?error.message:'Comparison unavailable.';}finally{if(token===version)button.disabled=false;}
  });
  return {destroy():void{version++;request?.abort();unwatch();closeDetails();}};
}
