export interface Address { id: string; name: string; point: [number, number] | null; enabled: boolean }
const key = 'milano-addresses';
const event = 'milano-addresses-changed';
let current: Address[] | null = null;
export const newAddress = (): Address => ({id:crypto.randomUUID(),name:'',point:null,enabled:true});
export function getAddresses(): Address[] {
  if (!current) {
    try {
      let raw = JSON.parse(localStorage.getItem(key) ?? 'null');
      if(!raw){
        const saved=JSON.parse(localStorage.getItem('milano-commute-settings')??localStorage.getItem('milano-apartments')??'null');
        const previous=saved?.addresses??saved?.apartments;
        if(Array.isArray(previous))raw=previous.map(a=>({id:a.id??crypto.randomUUID(),name:a.name,point:a.point,enabled:a.enabled!==false}));
      }
      if (Array.isArray(raw) && raw.length > 0 && raw.length <= 3 && raw.every(a=>
        typeof a.id==='string' && typeof a.name==='string' && a.name.length<=200 && typeof a.enabled==='boolean' &&
        (a.point===null || (Array.isArray(a.point) && a.point.length===2 && a.point.every(Number.isFinite) && a.point[0]>=8.3 && a.point[0]<=10.2 && a.point[1]>=44.8 && a.point[1]<=46.2)))) current=raw;
    } catch { /* Browser persistence is optional. */ }
    current ??= [newAddress()];
  }
  return structuredClone(current);
}
export function setAddresses(addresses: Address[], source: string): void {
  current = structuredClone(addresses);
  try { localStorage.setItem(key, JSON.stringify(current)); } catch { /* Session still works. */ }
  window.dispatchEvent(new CustomEvent(event,{detail:{source}}));
}
export function watchAddresses(source:string, update:()=>void): ()=>void {
  const listener=(e:Event):void=>{if((e as CustomEvent).detail.source!==source)update();};
  window.addEventListener(event,listener);return ()=>window.removeEventListener(event,listener);
}
