/* MIT; Copyright (c) 2026 Minho Hwang (revfactory).
 * Modified for OMH from revfactory/showreel@64521220a2f8e91cb9894400c48c15b7f45205d6.
 * Selected absolute-time techniques, restructured/parameterized; not upstream runtime parity.
 * Full permission and warranty notice: toolkit/LICENSE.upstream. */
(function(root){
  'use strict';
  const R=root.REEL;
  root.TECHNIQUES.kineticPose=(t,{start=0,duration=.7,stagger=.08,index=0,rise=80}={})=>{
    const local=t-start-index*stagger;
    const p=R.prog(local,0,duration,R.ease.outBack);
    const anticipation=local<0?-8*Math.sin(Math.PI*R.clamp((local+.15)/.15)):0;
    return {y:(1-p)*rise+anticipation,opacity:R.prog(local,0,duration*.6),mask:R.clamp(p),scale:.96+.04*R.spring(local,0,2.2,.65)};
  };
})(globalThis);
