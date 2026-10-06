import { Map as MapLibreMap, Marker, NavigationControl } from 'maplibre-gl';
import type { FeatureCollection, LineString } from 'geojson';
import { MAP_STYLE } from './map';

export interface MapLeg { mode:string; coordinates?:[number,number][]; route?:string; trip_id?:string }
export function createRouteView(root:HTMLElement, legs:MapLeg[], run?:string, walking=false):{destroy():void} {
  const controller=new AbortController();let disposed=false,map:MapLibreMap|undefined;
  let observer:ResizeObserver|undefined;
  const markers:Marker[]=[];
  root.innerHTML='<p role="status">Loading route map…</p><div class="inline-route-map" role="region" aria-label="Journey route map"></div><p class="route-legend">Solid green: transit route · blue: street walk · dashed: estimated connection</p><div class="route-directions"></div>';
  const status=root.querySelector('p')!, canvas=root.querySelector<HTMLElement>('.inline-route-map')!;
  const load=async():Promise<void>=>{
    try {
      const points=legs.flatMap(l=>l.coordinates??[]);
      if(points.length<2)throw new Error('Recalculate this journey to display its route map.');
      const response=await fetch(walking?'/api/walking-route':'/api/journey-map',{method:'POST',headers:{'Content-Type':'application/json','X-Mobility-Action':'explore'},signal:controller.signal,
        body:JSON.stringify(walking?{origin:points[0],destination:points.at(-1)}:{legs,pipeline_run_id:run})});
      const data=await response.json();if(!response.ok)throw new Error(data.error??'Route map unavailable.');if(disposed)return;
      let geo:FeatureCollection<LineString>;
      if(walking){
        geo={type:'FeatureCollection',features:[{type:'Feature',properties:{kind:'street_walk'},geometry:{type:'LineString',coordinates:data.coordinates}},
          ...data.access.map((coordinates:number[][])=>({type:'Feature',properties:{kind:'access'},geometry:{type:'LineString',coordinates}}))]};
        status.textContent=`${Math.ceil(data.seconds/60)} min walk · ${data.metres} m. Dashed access links are approximate. Entrances and accessibility are not verified.`;
        const list=document.createElement('ol');list.className='walking-directions';
        data.steps.forEach((s:{instruction:string;metres:number})=>{const item=document.createElement('li');item.textContent=`${s.instruction}${s.metres?` · ${s.metres} m`:''}`;list.append(item);});
        const details=document.createElement('details'),summary=document.createElement('summary');summary.textContent='Walking directions';details.append(summary,list);root.querySelector('.route-directions')!.append(details);
      }else{geo=data;status.textContent=data.notes.length?data.notes.join(' '):'Official transit paths and pedestrian street routes. Short access links are approximate.';}
      map=new MapLibreMap({container:canvas,style:MAP_STYLE,center:points[0],zoom:13});
      map.addControl(new NavigationControl({showCompass:false}),'top-right');
      observer=new ResizeObserver(()=>map?.resize());observer.observe(canvas);
      map.on('load',()=>{
        if(disposed)return;
        map!.addSource('journey',{type:'geojson',data:geo});
        map!.addLayer({id:'verified',type:'line',source:'journey',filter:['in',['get','kind'],['literal',['official_transit','street_walk']]],paint:{'line-color':['match',['get','kind'],'street_walk','#68b5ff','#48e3b5'],'line-width':5}});
        map!.addLayer({id:'estimated',type:'line',source:'journey',filter:['!', ['in',['get','kind'],['literal',['official_transit','street_walk']]]],paint:{'line-color':'#ffc45c','line-width':4,'line-dasharray':[2,2]}});
        const coords=geo.features.flatMap(f=>f.geometry.coordinates);
        map!.fitBounds([[Math.min(...coords.map(p=>p[0]!)),Math.min(...coords.map(p=>p[1]!))],[Math.max(...coords.map(p=>p[0]!)),Math.max(...coords.map(p=>p[1]!))]],{padding:40,maxZoom:16,duration:0});
        [points[0]!,points.at(-1)!].forEach((p,i)=>{const el=document.createElement('span');el.className='comparison-pin';el.textContent=i?'B':'A';markers.push(new Marker({element:el}).setLngLat(p).addTo(map!));});
      });
    }catch(error){if(!disposed){status.textContent=error instanceof Error?error.message:'Route map unavailable.';canvas.hidden=true;const retry=document.createElement('button');retry.type='button';retry.className='secondary-button';retry.textContent='Retry route map';retry.onclick=()=>{retry.remove();canvas.hidden=false;void load();};root.append(retry);}}
  };
  void load();
  return {destroy():void{disposed=true;controller.abort();observer?.disconnect();markers.forEach(m=>m.remove());map?.remove();}};
}
