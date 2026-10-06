/** Native modal with shared planner styling, focus restoration and Escape support. */
export function createDialog(root:HTMLElement,title:string,onClose?:()=>void):{
  element:HTMLDialogElement;body:HTMLElement;open(title?:string):void;close():void;destroy():void;
} {
  const element=document.createElement('dialog');
  element.className='nearby-dialog planner-dialog';
  const heading=document.createElement('div');heading.className='nearby-dialog-heading';
  const label=document.createElement('h2');label.id=`dialog-${crypto.randomUUID()}`;label.textContent=title;
  element.setAttribute('aria-labelledby',label.id);
  const button=document.createElement('button');button.type='button';button.className='secondary-button';button.textContent='Close';
  const body=document.createElement('div');body.className='planner-dialog-body';
  heading.append(label,button);element.append(heading,body);root.append(element);
  const close=():void=>element.close();
  button.addEventListener('click',close);
  const closed=():void=>onClose?.();element.addEventListener('close',closed);
  return {element,body,open(nextTitle):void{if(nextTitle)label.textContent=nextTitle;if(!element.open)element.showModal();},close,
    destroy():void{element.removeEventListener('close',closed);close();onClose?.();element.remove();}};
}
