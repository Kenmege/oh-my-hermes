/* MIT; Copyright (c) 2026 Minho Hwang (revfactory).
 * Modified for OMH from revfactory/showreel@64521220a2f8e91cb9894400c48c15b7f45205d6.
 * Selected absolute-time techniques, restructured/parameterized; not upstream runtime parity.
 * Full permission and warranty notice: toolkit/LICENSE.upstream. */
(function(root){
  'use strict';
  const R=root.REEL,F=root.FX;
  root.TECHNIQUES.depthGeometry=(t,{seed=1,count=25,scale=30,cx=320,cy=240}={})=>{
    if(!Number.isInteger(count)||count<1||count>256)throw Error('invalid box count');
    const side=Math.ceil(Math.sqrt(count)),az=Math.PI/4+.22*Math.sin(t*.4);
    return Array.from({length:count},(_,i)=>{
      const x=i%side-side/2,y=Math.floor(i/side)-side/2;
      const h=(.4+R.rand(i,seed)*3)*R.prog(t,i*.015,.65,R.ease.outBack);
      const P=(a,b,z)=>F.project(x+a,y+b,z,{az,scale,cx,cy});
      const normals=[[0,1,0],[1,0,0],[0,0,1]];
      return {depth:P(.4,.4,0)[2],faces:[
        [P(0,.8,0),P(.8,.8,0),P(.8,.8,h),P(0,.8,h)],
        [P(.8,0,0),P(.8,.8,0),P(.8,.8,h),P(.8,0,h)],
        [P(0,0,h),P(.8,0,h),P(.8,.8,h),P(0,.8,h)]
      ].map((points,j)=>({points,light:F.light(normals[j],t)}))};
    }).sort((a,b)=>a.depth-b.depth);
  };
})(globalThis);
