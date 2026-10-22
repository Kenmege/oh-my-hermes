/* MIT; Copyright (c) 2026 Minho Hwang (revfactory).
 * Modified for OMH from revfactory/showreel@64521220a2f8e91cb9894400c48c15b7f45205d6.
 * Selected absolute-time techniques, restructured/parameterized; not upstream runtime parity.
 * Full permission and warranty notice: toolkit/LICENSE.upstream. */
(function(root){
  'use strict';
  const {lerp,prog,ease}=root.REEL;
  root.FX={
    project(x,y,z,{az=Math.PI/4,el=.61548,scale=30,cx=320,cy=240}={}) {
      const u=x*Math.cos(az)-y*Math.sin(az),v=x*Math.sin(az)+y*Math.cos(az);
      return [cx+u*scale,cy+v*scale*Math.sin(el)-z*scale*Math.cos(el),v];
    },
    light(normal,t,{ambient=.3,strength=.65}={}) {
      const lamp=[Math.cos(t*.7)*.65,Math.sin(t*.7)*.65,.76];
      return ambient+strength*Math.max(0,normal.reduce((s,n,i)=>s+n*lamp[i],0));
    },
    match(from,to,t,start,duration){
      const p=prog(t,start,duration,ease.inOutCubic);
      return Object.fromEntries(['x','y','w','h'].map(k=>{
        if(!Number.isFinite(from[k])||!Number.isFinite(to[k]))throw Error('invalid geometry');
        return [k,lerp(from[k],to[k],p)];
      }));
    }
  };
})(globalThis);
