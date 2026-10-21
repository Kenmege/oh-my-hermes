/* MIT; Copyright (c) 2026 Minho Hwang (revfactory).
 * Modified for OMH from revfactory/showreel@64521220a2f8e91cb9894400c48c15b7f45205d6
 * assets/engine/scenes.js and selected techniques: new parameterized teaching gallery, not a film template.
 * Full permission/warranty: toolkit/LICENSE.upstream. */
(function(root){
  'use strict';
  const C=root.PROJECT,R=root.REEL,T=root.TECHNIQUES;
  const stage=document.getElementById('stage'),cv=document.getElementById('geometry'),g=cv.getContext('2d');
  stage.style.width=C.width+'px';stage.style.height=C.height+'px';cv.width=C.width;cv.height=C.height;
  document.documentElement.lang=C.language;document.documentElement.dir=C.direction;
  const box=document.getElementById('type');
  box.style.fontFamily=(C.fonts||[]).map(f=>JSON.stringify(f.family)).join(',');
  const runs=(C.texts||[]).map(run=>{
    const mask=document.createElement('div');mask.className='run';
    const el=document.createElement(run.literal?'bdi':'div');
    el.lang=run.lang||C.language;el.dir=run.literal?'ltr':run.dir||C.direction;
    el.textContent=run.text;mask.append(el);box.append(mask);return el;
  });
  root.__ready=false;root.__error=null;
  root.__meta={width:C.width,height:C.height,duration:C.duration,fps:C.fps,seed:C.seed,
    scenes:['kinetic-type','depth-light','particles','match-transform'],fonts:C.fonts,
    limitations:['Teaching gallery, not a ready-made film','Native-language review not observed','No autoplay/audio']};
  root.__seek=async function(t){
    if(!Number.isFinite(t)||t<0||t>C.duration)throw Error('seek out of bounds');
    const width=C.width,height=C.height,section=C.duration/4;
    const idx=Math.min(3,Math.floor(t/section)),local=t-idx*section;
    const palette=C.palette||['#101820','#e7ba64','#e9eee8'];
    g.clearRect(0,0,width,height);g.fillStyle=idx===3?palette[2]:palette[0];g.fillRect(0,0,width,height);
    box.style.display=idx===0?'flex':'none';
    if(idx===0) runs.forEach((el,index)=>{
      const p=T.kineticPose(local,{start:.08,duration:Math.min(.8,section/2),index,stagger:.12,rise:height*.3});
      el.style.opacity=p.opacity;el.style.transform=`translateY(${p.y}px) scale(${p.scale})`;
    });
    g.save();
    if(idx===1){
      for(const b of T.depthGeometry(local,{seed:C.seed,count:36,scale:width/20,cx:width*.5,cy:height*.7})){
        for(const face of b.faces){
          const light=face.light;g.fillStyle=`rgb(${Math.round(64*light)},${Math.round(158*light)},${Math.round(186*light)})`;
          g.beginPath();face.points.forEach(([x,y],i)=>i?g.lineTo(x,y):g.moveTo(x,y));g.closePath();g.fill();
        }
      }
    }
    if(idx===2){
      g.fillStyle=palette[1];
      for(const p of T.particleGeometry(local,{seed:C.seed,count:450,cx:width*.5,cy:height*.45,radius:height*.27,gravity:height*.07})){
        g.globalAlpha=p.alpha;g.beginPath();g.arc(p.x,p.y,p.size*width/640,0,2*Math.PI);g.fill();
      }
    }
    if(idx===3){
      const from={x:width*.12,y:height*.3,w:width*.18,h:height*.18};
      const to={x:width*.1,y:height*.1,w:width*.8,h:height*.8};
      const p=T.matchGeometry(local,{from,to,start:0,duration:Math.min(1.8,section)});
      g.fillStyle=palette[0];g.fillRect(p.x,p.y,p.w,p.h);
      g.strokeStyle=palette[1];g.lineWidth=width/100;g.beginPath();g.arc(p.x+p.w*.5,p.y+p.h*.5,Math.min(p.w,p.h)*.22,0,Math.PI*2);g.stroke();
    }
    g.restore();
    // Synchronous absolute-time DOM/canvas writes; the host capture waits for compositor output.
    stage.getBoundingClientRect();
  };
  async function ready(){
    if(!C.fonts?.length)throw Error('HOLD: explicit fonts required');
    for(const font of C.fonts){
      const face=new FontFace(font.family,`url("${font.file}")`);
      await face.load();document.fonts.add(face);
      if(face.status!=='loaded')throw Error('font failed: '+font.file);
    }
    await document.fonts.ready;
    await Promise.all([...document.images].map(async img=>{await img.decode();if(!img.naturalWidth)throw Error('image failed: '+img.src);}));
    await root.__seek(0);root.__ready=true;
  }
  ready().catch(error=>{root.__error=String(error);console.error(error);});
})(globalThis);
