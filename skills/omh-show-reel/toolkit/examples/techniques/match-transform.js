/* MIT; Copyright (c) 2026 Minho Hwang (revfactory).
 * Modified for OMH from revfactory/showreel@64521220a2f8e91cb9894400c48c15b7f45205d6.
 * Selected absolute-time techniques, restructured/parameterized; not upstream runtime parity.
 * Full permission and warranty notice: toolkit/LICENSE.upstream. */
(function(root){
  'use strict';
  // Pass actual measured DOM bounds or authored geometry; carries the same focal object.
  root.TECHNIQUES.matchGeometry=(t,{from,to,start=0,duration=1}={})=>root.FX.match(from,to,t,start,duration);
})(globalThis);
