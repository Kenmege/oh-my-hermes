/* MIT; Copyright (c) 2026 Minho Hwang (revfactory).
 * Modified for OMH from revfactory/showreel@64521220a2f8e91cb9894400c48c15b7f45205d6.
 * Selected absolute-time techniques, restructured/parameterized; not upstream runtime parity.
 * Full permission and warranty notice: toolkit/LICENSE.upstream. */
(function (root) {
  'use strict';
  const clamp = (x, a=0, b=1) => Math.min(b, Math.max(a, x));
  const lerp = (a,b,p) => a+(b-a)*p;
  const ease = {linear:p=>p, outCubic:p=>1-(1-p)**3,
    inOutCubic:p=>p<.5?4*p**3:1-(-2*p+2)**3/2,
    outBack:p=>1+2.70158*(p-1)**3+1.70158*(p-1)**2};
  const prog = (t,start,duration,e=ease.outCubic) => {
    if (![t,start,duration].every(Number.isFinite) || duration<=0) throw Error('invalid timing');
    return e(clamp((t-start)/duration));
  };
  const rand = (i,seed=0) => { const x=Math.sin(i*12.9898+seed*78.233+.1)*43758.5453; return x-Math.floor(x); };
  function spring(t,start=0,frequency=2.2,damping=.42) {
    if (!(frequency>0 && damping>0 && damping<1)) throw Error('invalid spring');
    const dt=t-start; if(dt<=0) return 0;
    const w=2*Math.PI*frequency, wd=w*Math.sqrt(1-damping*damping);
    return 1-Math.exp(-damping*w*dt)*(Math.cos(wd*dt)+damping*w/wd*Math.sin(wd*dt));
  }
  function kf(t,frames) {
    if(!frames.length || frames.some((v,i)=>!Number.isFinite(v[0]) || !Number.isFinite(v[1]) || (i && v[0]<=frames[i-1][0]))) throw Error('invalid keyframes');
    if(t<=frames[0][0]) return frames[0][1];
    for(let i=1;i<frames.length;i++) if(t<=frames[i][0]) return lerp(frames[i-1][1],frames[i][1],prog(t,frames[i-1][0],frames[i][0]-frames[i-1][0],frames[i][2]||ease.inOutCubic));
    return frames.at(-1)[1];
  }
  root.REEL={clamp,lerp,ease,prog,rand,spring,kf};
  root.TECHNIQUES={};
})(globalThis);
