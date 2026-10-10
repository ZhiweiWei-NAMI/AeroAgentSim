// Screen-space presentation helpers; no graph facts or renderer coordinates change.
export const overlapArea=(a,b)=>Math.max(0,Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x))*Math.max(0,Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y));
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));

export function tooltipPlacement(point,size,frame,obstacles=[]){
 const gap=18,w=Math.min(size.w,frame.w-16),h=Math.min(size.h,frame.h-16);
 const candidates=[{x:point.x+gap,y:point.y+gap},{x:point.x-w-gap,y:point.y+gap},
  {x:point.x+gap,y:point.y-h-gap},{x:point.x-w-gap,y:point.y-h-gap},
  {x:8,y:8},{x:frame.w-w-8,y:8},{x:8,y:frame.h-h-8},{x:frame.w-w-8,y:frame.h-h-8}]
  .map(p=>({...p,x:clamp(p.x,8,frame.w-w-8),y:clamp(p.y,8,frame.h-h-8),w,h}));
 const score=p=>obstacles.reduce((sum,b)=>sum+overlapArea(p,b)*(b.weight??1),0)*1000+Math.hypot(p.x-point.x,p.y-point.y);
 return candidates.sort((a,b)=>score(a)-score(b))[0];
}

export function wrapLabel(text,maxWidth,measure){
 const lines=[];let line='';
 for(const char of text){if(line&&measure(line+char)>maxWidth){lines.push(line);line=char;}else line+=char;}
 if(line)lines.push(line);
 return lines;
}

export function clearPlacement(placements,frame,occupied,nodeBoxes){
 return placements.find(b=>b.x>=frame.x&&b.y>=frame.y&&b.x+b.w<=frame.x+frame.w&&b.y+b.h<=frame.y+frame.h&&
  ![...occupied,...nodeBoxes].some(o=>overlapArea({x:b.x-3,y:b.y-2,w:b.w+6,h:b.h+4},o)>0));
}
