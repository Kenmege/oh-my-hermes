/* MIT; Copyright (c) 2026 Minho Hwang (revfactory).
 * Modified for OMH from revfactory/showreel@64521220a2f8e91cb9894400c48c15b7f45205d6.
 * Selected absolute-time techniques, restructured/parameterized; not upstream runtime parity.
 * Full permission and warranty notice: toolkit/LICENSE.upstream. */
(function(root){
  'use strict';
  const R=root.REEL;
  root.TECHNIQUES.particleGeometry=(t,{seed=1,count=160,cx=320,cy=180,radius=110,start=0,drag=1.5,gravity=25}={})=>{
    if(!Number.isInteger(count)||count<1||count>4000||drag<=0)throw Error('invalid particles');
    const u=Math.max(0,t-start),k=(1-Math.exp(-drag*u))/drag;
    return Array.from({length:count},(_,i)=>{
      const angle=R.rand(i,seed)*2*Math.PI,r=radius*Math.sqrt(R.rand(i,seed+1));
      const vx=(R.rand(i,seed+2)-.5)*140,vy=(R.rand(i,seed+3)-.5)*140;
      return {x:cx+Math.cos(angle)*r+vx*k,y:cy+Math.sin(angle)*r+gravity/drag*u+(vy-gravity/drag)*k,
        size:1+3*R.rand(i,seed+4),alpha:R.clamp(1-u/4)};
    });
  };
})(globalThis);
