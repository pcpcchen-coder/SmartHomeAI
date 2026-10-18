import React,{useEffect,useRef,useState} from 'react';
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {Lightbulb,Speaker,Camera,Power,Droplets,WashingMachine,MapPin,Monitor,Router,Activity,PanelTop,Radio} from 'lucide-react';

export function DeviceIcon({kind,...props}){const Icon={sensor:Activity,cover:PanelTop,gateway:Router,remote:Radio,light:Lightbulb,speaker:Speaker,camera:Camera,switch:Power,dehumidifier:Droplets,laundry:WashingMachine,computer:Monitor,network:Router}[kind]||MapPin;return <Icon {...props}/>}

export default function Space({layout,devices,selectedId,onSelect,onPlace,placementMode,view,roomId,drawPoints=[],showModel=true}){
 const host=useRef(),runtime=useRef(),labels=useRef({}),data=useRef({devices,onPlace,placementMode}),[error,setError]=useState(''),[loaded,setLoaded]=useState(false);
 data.current={devices,onPlace,placementMode};
 const visible=devices.filter(d=>d.placement&&(!roomId||d.placement.room_id===roomId));
 useEffect(()=>{
  if(view!=='3d'||!host.current||!layout)return;
  setLoaded(false);setError('');
  const el=host.current,scene=new THREE.Scene();scene.background=new THREE.Color('#eef0ec');scene.fog=new THREE.Fog('#eef0ec',30,65);
  let renderer;try{renderer=new THREE.WebGLRenderer({antialias:true,alpha:false});}catch{setError('此瀏覽器無法啟用 3D，請切換平面圖操作。');return;}
  renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.05;renderer.localClippingEnabled=true;
  el.prepend(renderer.domElement);
  const camera=new THREE.PerspectiveCamera(35,1,.1,100);camera.position.set(13,16,17);
  const orbit=new OrbitControls(camera,renderer.domElement);orbit.target.set(0,.2,0);orbit.enableDamping=true;orbit.dampingFactor=.08;orbit.minDistance=5;orbit.maxDistance=35;orbit.maxPolarAngle=Math.PI/2.05;orbit.minPolarAngle=.12;
  scene.add(new THREE.HemisphereLight('#fff9e9','#b1b5ad',1.7));const sun=new THREE.DirectionalLight('#fff5e3',2.2);sun.position.set(-8,16,8);sun.castShadow=true;sun.shadow.mapSize.set(2048,2048);sun.shadow.camera.left=-12;sun.shadow.camera.right=12;sun.shadow.camera.top=12;sun.shadow.camera.bottom=-12;sun.shadow.normalBias=.03;scene.add(sun);
  const ground=new THREE.Mesh(new THREE.PlaneGeometry(100,100),new THREE.MeshStandardMaterial({color:'#eef0ec',roughness:1}));ground.rotation.x=-Math.PI/2;ground.position.y=-.22;ground.receiveShadow=true;scene.add(ground);
  const grid=new THREE.GridHelper(32,32,'#cad0c8','#e1e4de');grid.position.y=-.20;scene.add(grid);
  const building=new THREE.Group();scene.add(building);
  const floor=new THREE.Mesh(new THREE.BoxGeometry(layout.width_m,.12,layout.depth_m),new THREE.MeshStandardMaterial({color:'#d7c7ae',roughness:.92}));floor.position.y=-.12;floor.receiveShadow=true;building.add(floor);
  const makeRoomGeometry=poly=>{const shape=new THREE.Shape();poly.forEach((p,i)=>{const x=(p[0]-.5)*layout.width_m,y=(.5-p[1])*layout.depth_m;i?shape.lineTo(x,y):shape.moveTo(x,y)});shape.closePath();const geo=new THREE.ShapeGeometry(shape);geo.rotateX(-Math.PI/2);return geo;};
  for(const room of layout.rooms){
   const slab=new THREE.Mesh(makeRoomGeometry(room.polygon),new THREE.MeshStandardMaterial({color:room.color,roughness:1,transparent:true,opacity:showModel?.12:.65,side:THREE.DoubleSide}));slab.position.y=.005;building.add(slab);
  }
  let disposed=false;const clip=new THREE.Plane(new THREE.Vector3(0,-1,0),2.15);let model;
  if(showModel&&layout.model_url){
   new GLTFLoader().load(layout.model_url,gltf=>{
    if(disposed){gltf.scene.traverse(x=>{x.geometry?.dispose();});return;}
    model=gltf.scene;model.scale.setScalar(layout.model_scale||1);model.rotation.y=THREE.MathUtils.degToRad(layout.model_rotation||0);model.position.set(layout.model_offset_x||0,-.15,layout.model_offset_z||0);
    model.traverse(obj=>{if(obj.isMesh){obj.castShadow=true;obj.receiveShadow=true;const mats=Array.isArray(obj.material)?obj.material:[obj.material];obj.material=mats.map(m=>{const n=new THREE.MeshStandardMaterial({color:m.color||'#eee6da',map:m.map,transparent:m.transparent,opacity:m.opacity,roughness:.88,metalness:0,side:THREE.DoubleSide,clippingPlanes:[clip]});return n;});if(obj.material.length===1)obj.material=obj.material[0];}});building.add(model);setLoaded(true);
   },undefined,()=>{setError('原始模型載入失敗，可切換配置模型或平面圖。');});
  }else{
   const edges=new Set();for(const room of layout.rooms)room.polygon.forEach((p,i)=>{const q=room.polygon[(i+1)%room.polygon.length];const key=[p.join(','),q.join(',')].sort().join('|');if(edges.has(key))return;edges.add(key);const x1=(p[0]-.5)*layout.width_m,z1=(p[1]-.5)*layout.depth_m,x2=(q[0]-.5)*layout.width_m,z2=(q[1]-.5)*layout.depth_m,length=Math.hypot(x2-x1,z2-z1);const wall=new THREE.Mesh(new THREE.BoxGeometry(length,.85,.10),new THREE.MeshStandardMaterial({color:'#f7f4ec',roughness:.8}));wall.position.set((x1+x2)/2,.425,(z1+z2)/2);wall.rotation.y=-Math.atan2(z2-z1,x2-x1);wall.castShadow=true;wall.receiveShadow=true;building.add(wall);});setLoaded(true);
  }
  const ray=new THREE.Raycaster(),pointer=new THREE.Vector2(),plane=new THREE.Plane(new THREE.Vector3(0,1,0),0);let down;
  const onDown=e=>{down=[e.clientX,e.clientY]};const onUp=e=>{if(!data.current.placementMode||!down||Math.hypot(e.clientX-down[0],e.clientY-down[1])>6)return;const r=renderer.domElement.getBoundingClientRect();pointer.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);ray.setFromCamera(pointer,camera);const v=new THREE.Vector3();if(ray.ray.intersectPlane(plane,v)){const x=v.x/layout.width_m+.5,y=v.z/layout.depth_m+.5;if(x>=0&&x<=1&&y>=0&&y<=1)data.current.onPlace(x,y);}};
  renderer.domElement.addEventListener('pointerdown',onDown);renderer.domElement.addEventListener('pointerup',onUp);
  const size=()=>{const w=el.clientWidth,h=el.clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();};const resize=new ResizeObserver(size);resize.observe(el);size();
  runtime.current={camera,orbit};let frame;
  const animate=()=>{frame=requestAnimationFrame(animate);orbit.update();renderer.render(scene,camera);const w=el.clientWidth,h=el.clientHeight;for(const d of data.current.devices){if(!d.placement)continue;const label=labels.current[d.id];if(!label)continue;const p=d.placement;const v=new THREE.Vector3((p.x-.5)*layout.width_m,p.z||1,(p.y-.5)*layout.depth_m).project(camera);label.style.left=`${(v.x*.5+.5)*w}px`;label.style.top=`${(-v.y*.5+.5)*h}px`;label.style.visibility=v.z>1?'hidden':'visible';}for(const room of layout.rooms){const label=labels.current['room-'+room.id];if(!label)continue;const center=room.polygon.reduce((a,p)=>[a[0]+p[0]/room.polygon.length,a[1]+p[1]/room.polygon.length],[0,0]);const v=new THREE.Vector3((center[0]-.5)*layout.width_m,.05,(center[1]-.5)*layout.depth_m).project(camera);label.style.left=`${(v.x*.5+.5)*w}px`;label.style.top=`${(-v.y*.5+.5)*h}px`;}};animate();
  return()=>{disposed=true;cancelAnimationFrame(frame);resize.disconnect();orbit.dispose();renderer.domElement.removeEventListener('pointerdown',onDown);renderer.domElement.removeEventListener('pointerup',onUp);scene.traverse(obj=>{obj.geometry?.dispose();const mats=Array.isArray(obj.material)?obj.material:[obj.material];mats.forEach(m=>m?.dispose());});renderer.dispose();renderer.domElement.remove();runtime.current=null;};
 },[view,layout?.version,layout?.model_url,showModel]);

 const marker=(d,style={})=><button key={d.id} ref={e=>{labels.current[d.id]=e}} style={style} onClick={e=>{e.stopPropagation();onSelect(d.id)}} aria-label={`選取 ${d.name}`} className={`map-device ${selectedId===d.id?'selected':''} ${d.placement?.confirmed?'':'tentative'} ${d.state?.fresh&&d.state?.available?'online':''}`}><DeviceIcon kind={d.kind} size={16}/><span>{d.name.split('／')[0]}</span></button>;
 return <div className={`space-frame ${placementMode?'placing':''}`}>
  {view==='3d'?<div className="three-host" ref={host}>
   {layout?.rooms.map(r=><span className={`room-label ${roomId===r.id?'focus':''}`} key={r.id} ref={e=>labels.current['room-'+r.id]=e}>{r.name}</span>)}
   {visible.map(d=>marker(d))}
   {!loaded&&!error&&<div className="model-loading"><span className="spinner"/>載入武陵居 3D 模型</div>}
   {error&&<div className="model-warning">{error}</div>}
  </div>:<div className="plan-host" onClick={e=>{if(!placementMode)return;const r=e.currentTarget.getBoundingClientRect();onPlace((e.clientX-r.left)/r.width,(e.clientY-r.top)/r.height)}}>
   <img className="floorplan-image" src={layout.image} alt={layout.source_label||'空間平面圖'} draggable="false"/>
   <svg className="floorplan-overlay" viewBox="0 0 1000 1000" preserveAspectRatio="none">{layout.rooms.map(r=><polygon key={r.id} points={r.polygon.map(p=>p.map(x=>x*1000).join(',')).join(' ')} fill={roomId===r.id?r.color:'transparent'} fillOpacity=".4" stroke={roomId===r.id?'#78906b':'transparent'} strokeWidth="2"/>)}{drawPoints.length>0&&<polyline points={drawPoints.map(p=>p.map(x=>x*1000).join(',')).join(' ')} fill="#9eaf82" fillOpacity=".25" stroke="#738458" strokeWidth="3"/>}</svg>
   {visible.map(d=>marker(d,{left:`${d.placement.x*100}%`,top:`${d.placement.y*100}%`}))}
  </div>}
  {view==='3d'&&<div className="map-navigation"><button aria-label="放大模型" onClick={()=>{const r=runtime.current;if(r)r.camera.position.lerp(r.orbit.target,.15)}}>+</button><button aria-label="縮小模型" onClick={()=>{const r=runtime.current;if(r)r.camera.position.sub(r.orbit.target).multiplyScalar(1.15).add(r.orbit.target)}}>−</button><button aria-label="重設視角" onClick={()=>{const r=runtime.current;if(r){r.camera.position.set(13,16,17);r.orbit.target.set(0,.2,0)}}}>↺</button></div>}
  <div className="map-caption"><span className="tiny-dot"/> {view==='3d'?(showModel?'原始 SketchUp 模型':'依平面圖建立的配置模型'):layout.source_label}<span>{placementMode?'點擊空間以設定位置':view==='3d'?'拖曳旋轉 · 滾輪縮放':'點選設備進行控制'}</span></div>
 </div>;
}
