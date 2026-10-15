import {connections} from './model.mjs';
const original={ad:{x:25,y:92},dataset:{x:25,y:548},website:{x:25,y:92},form:{x:25,y:274},pixel:{x:25,y:422},crm:{x:25,y:274},filter:{x:42,y:392},server:{x:25,y:548},retain:{x:179,y:467}};
const compact={...original,ad:{x:25,y:80},website:{x:25,y:80},form:{x:25,y:224},crm:{x:25,y:224},pixel:{x:25,y:364},dataset:{x:25,y:364},server:{x:25,y:364}};
export function flowLayout(loop,guide=false){
 const useCompact=guide&&!loop.filterEnabled;
 const positions={...(useCompact?compact:original),...loop.positions};
 const persistedBottom=Math.max(0,...Object.values(loop.positions||{}).map(point=>point.y+120));
 return {positions,laneHeight:Math.max(useCompact?500:665,persistedBottom)};
}
export function flowConnections(loop,guide=false){
 const {positions}=flowLayout(loop,guide);
 return connections(loop).map(edge=>{
  if(!guide||loop.filterEnabled)return edge;
  if(edge.source==='website'&&edge.target==='pixel')return {...edge,from:'browser-bottom',to:'top',data:{browserBranchLeft:270+Math.min(positions.website.x,positions.form.x,positions.pixel.x)-22}};
  if(edge.source==='pixel')return {...edge,from:'left'};
  if(edge.source==='server')return {...edge,from:'bottom',to:'bottom'};
  return edge;
 });
}
