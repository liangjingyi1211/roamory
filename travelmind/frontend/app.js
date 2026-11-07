"use strict";
const surface = window.TravelMindSurface;
const surfaceDocument = surface?.document || document;
const surfaceView = surface?.view || window;
// Static Streamlit component protocol; no npm bundle, no backend credential.
const post = (type, payload = {}) => window.parent.postMessage({isStreamlitMessage: true, type, ...payload}, "*");
const $ = id => surfaceDocument.getElementById(id);
const el = (tag, text, cls) => {const n=surfaceDocument.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n;};
const mealLabels = {breakfast:"早餐",lunch:"午餐",dinner:"晚餐"};
const labels = {activity:"景点",meal:"餐饮",hotel:"当晚住宿",custom:"自由安排"};
let plan, selectedDay, selectedPlaceRef=null, map, info, mapPromise, activeId, pending, lastAck, renderedSignature, pendingPrompt="";
const markers = new Map();
let configuredKey = "", mapMessage = "";
let journalEntries = [];
function resize(){
  surface?.refreshInset();
  post("streamlit:setFrameHeight", {height:surface?.mounted?0:Math.ceil(surfaceDocument.documentElement.scrollHeight || surfaceDocument.body.scrollHeight)});
}
new ResizeObserver(resize).observe(surface?.root || surfaceDocument.documentElement);
surfaceView.addEventListener("resize",resize);
function feedback(message, error=false){const n=$("feedback");n.replaceChildren(); if(message)n.append(el("div",message,"notice "+(error?"error":"success")));resize();}
function button(text, fn, cls){const b=el("button",text,cls);b.type="button";b.onclick=e=>{e.stopPropagation();fn(e);};return b;}
function current(){return plan.days.find(d=>d.day_id===selectedDay)||plan.days[0];}
// Scroll only this surfaceDocument. scrollIntoView can also move the Streamlit parent,
// placing the iframe behind its toolbar even though the map is sticky inside it.
function revealInPage(node, alignStart=false){
  const inset=surface?.getInset?.() ?? (surfaceDocument.querySelector(".geo-column").getBoundingClientRect().height+16);
  const rect=node.getBoundingClientRect();
  const delta=alignStart||rect.top<inset?rect.top-inset:Math.max(0,rect.bottom-surfaceView.innerHeight+16);
  surfaceView.scrollBy({top:delta,behavior:alignStart||window.matchMedia("(prefers-reduced-motion: reduce)").matches?"instant":"smooth"});
}
function submit(day,slot,action,args={}){
  if(pending)return;
  const event={event_id:crypto.randomUUID(),trip_id:plan.trip_id,trip_version:plan.version,day_id:day.day_id,slot,action,arguments:args};
  pending=event.event_id;feedback("正在保存到本机…");surfaceDocument.querySelectorAll("button,input").forEach(n=>n.disabled=true);
  post("streamlit:setComponentValue",{value:event,dataType:"json"});
}
function input(label,value,type="text"){
  const box=el("div"),name=el("label",label),control=el("input");control.type=type;control.value=value||"";
  name.append(control);box.append(name);return {box,control};
}
function editor(container, day, slot, place, item, mode="name"){
  container.querySelector(".edit-form")?.remove();
  const form=el("form",undefined,"edit-form");
  let name,start,end;
  if(mode==="name"){
    const field=input("名称 · 手动输入不搜索或自动补全",place?.name);name=field.control;name.required=true;form.append(field.box);
  }else{
    const row=el("div",undefined,"form-row");const a=input("开始时间（可留空）",item.start_time?.slice(0,5),"time");const b=input("结束时间（可留空）",item.end_time?.slice(0,5),"time");
    start=a.control;end=b.control;row.append(a.box,b.box);form.append(row);
  }
  const row=el("div",undefined,"form-buttons"),save=el("button","保存","primary");save.type="submit";
  row.append(save,button("取消",()=>{form.remove();resize();}));form.append(row);
  form.onsubmit=e=>{e.preventDefault();
    if(mode==="name")submit(day,slot,"save",{name:name.value,item_id:slot==="activities"?place?.id:null});
    else submit(day,"timeline","time",{item_id:item.id,start_time:start.value||null,end_time:end.value||null});
  };
  container.append(form);resize();(name||start).focus();
}
function journalEditor(container, day, place, itemType){
  if(pending)return;
  container.querySelector(".journal-form")?.remove();
  const kind=itemType==="meal"?"restaurant":itemType;
  const existing=journalEntries.find(entry=>entry.day_id===day.day_id&&entry.item_type===kind&&entry.item_id===place.id);
  const form=el("form",undefined,"edit-form journal-form"),heading=el("h3",`记录 · ${place.name}`);
  const label=el("label","写下此刻的感想、见闻或小惊喜"),text=el("textarea");
  text.value=existing?.content||"";text.rows=5;text.maxLength=10000;
  label.append(text);form.append(heading,label,el("p","仅保存为个人日记，不参与行程规划。","subtle"));
  const kept=new Set((existing?.images||[]).map(image=>image.id)),uploads=[];
  const photoLabel=el("label","添加图片 · 最多 6 张，每张不超过 5MB"),files=el("input");
  files.type="file";files.multiple=true;files.accept="image/jpeg,image/png,image/webp";photoLabel.append(files);
  const gallery=el("div",undefined,"journal-photo-grid"),status=el("div",undefined,"journal-upload-status");status.setAttribute("role","status");
  form.append(photoLabel,gallery,status);
  let reading=false;
  const renderPhotos=()=>{
    gallery.replaceChildren();
    const tile=(name,url,remove)=>{
      const box=el("div",undefined,"journal-photo"),img=el("img");img.src=url;img.alt=name||"旅行照片";
      box.append(img,button("移除",()=>{remove();renderPhotos();},"remove-photo"));gallery.append(box);
    };
    for(const image of existing?.images||[])if(kept.has(image.id))tile(image.name,image.thumbnail_url,()=>kept.delete(image.id));
    uploads.forEach((image,index)=>tile(image.name,image.data_url,()=>uploads.splice(index,1)));
  };
  const readFile=file=>new Promise((resolve,reject)=>{
    const reader=new FileReader();reader.onload=()=>resolve({name:file.name,data_url:reader.result});reader.onerror=()=>reject(Error("图片读取失败，请重新选择"));reader.readAsDataURL(file);
  });
  files.onchange=async()=>{
    const selected=[...files.files];if(!selected.length||reading)return;
    status.textContent="";text.setCustomValidity("");
    if(kept.size+uploads.length+selected.length>6){status.textContent="每条记录最多保留 6 张图片";files.value="";return;}
    if(selected.some(file=>file.size>5*1024*1024||!["image/jpeg","image/png","image/webp"].includes(file.type))){status.textContent="请选择不超过 5MB 的 JPG、PNG 或 WebP 图片";files.value="";return;}
    reading=true;save.disabled=true;files.disabled=true;status.textContent="正在读取图片…";
    try{uploads.push(...await Promise.all(selected.map(readFile)));renderPhotos();status.textContent="图片已准备好，保存记录后生效。";}
    catch(error){status.textContent=error.message;}
    finally{reading=false;save.disabled=false;files.disabled=false;files.value="";resize();}
  };
  renderPhotos();
  const controls=el("div",undefined,"form-buttons"),save=el("button","保存记录","primary");save.type="submit";
  controls.append(save,button("取消",()=>{form.remove();container.querySelector(".record")?.focus();resize();}));form.append(controls);
  form.onsubmit=event=>{
    event.preventDefault();if(pending||reading)return;
    if(!text.value.trim()&&!kept.size&&!uploads.length){text.setCustomValidity("请写下感想或添加图片");text.reportValidity();return;}
    pending=crypto.randomUUID();feedback("正在保存旅行记录…");
    surfaceDocument.querySelectorAll("button,input,textarea").forEach(node=>node.disabled=true);
    post("streamlit:setComponentValue",{dataType:"json",value:{event_id:pending,kind:"journal",trip_id:plan.trip_id,
      day_id:day.day_id,item_type:kind,item_id:place.id,content:text.value,revision:existing?.revision??null,
      keep_image_ids:[...kept],new_images:uploads}});
  };
  text.oninput=()=>text.setCustomValidity("");
  container.append(form);resize();text.focus({preventScroll:true});revealInPage(form);
}
function renderDays(){
  $("days").replaceChildren();
  for(const day of [...plan.days].sort((a,b)=>a.date.localeCompare(b.date))){const b=button("",()=>selectDay(day.day_id),"day"+(day.day_id===selectedDay?" active":""));b.setAttribute("aria-pressed",day.day_id===selectedDay?"true":"false");
    b.append(el("strong",`Day ${day.day_index}`),el("small",day.date),el("span",day.title||"探索与发现","theme"));
    const meals=[day.breakfast,day.lunch,day.dinner].filter(Boolean).length;b.append(el("small",`${day.activities.length} 个景点 · ${meals} 餐${day.hotel?" · 已选住宿":""}`));$("days").append(b);
  }
}
function renderDay(refreshMap=true){
  const day=current();
  $("warnings").replaceChildren(...day.timeline_warnings.map(t=>el("div",t,"notice")));
  $("notes").replaceChildren(...day.notes.map(t=>el("div",t,"notice")));
  const root=$("timeline");root.replaceChildren();
  for(const item of day.timeline){
    const place=day.places[item.reference_id],slot=item.item_type==="meal"?item.meal_type:item.item_type==="activity"?"activities":"hotel";
    const row=el("article",undefined,`event ${item.item_type}${item.reference_id&&item.reference_id===activeId?" selected":""}`);row.dataset.ref=item.reference_id||item.id;
    const when=el("div",item.start_time?.slice(0,5)||"待安排","when");if(item.end_time)when.append(el("span",item.end_time.slice(0,5)));
    const rail=el("div",undefined,"rail");rail.append(el("i",undefined,"dot"));
    const body=el("div",undefined,"event-body");body.append(el("div",item.meal_type?mealLabels[item.meal_type]:labels[item.item_type],"kind"));
    body.onclick=e=>{if(!e.target.closest("button,a,input,textarea,form,details"))focusPlace(item.reference_id);};
    const title=button(item.title,()=>focusPlace(item.reference_id),"title"+(!place?" empty":""));
    title.setAttribute("aria-pressed",item.reference_id===activeId?"true":"false");body.append(title);
    if(place?.address)body.append(el("p",place.address,"address"));
    if(place&&(!Number.isFinite(place.latitude)||!Number.isFinite(place.longitude)))body.append(el("p","暂无地图位置","subtle"));
    if(item.notes)body.append(el("p",item.notes,"subtle"));
    const actions=el("div",undefined,"actions");
    if(item.item_type!=="custom")actions.append(button(place?"更换地点":"添加地点",()=>editor(body,day,slot,place,item)));
    actions.append(button("编辑时间",()=>editor(body,day,slot,place,item,"time")));
    if(place)actions.append(button("删除",()=>submit(day,slot,"delete",{item_id:slot==="activities"?place.id:null}),"danger"));
    if(item.item_type==="activity"&&place){const index=day.activities.findIndex(a=>a.id===place.id);
      if(index>0)actions.append(button("上移",()=>submit(day,slot,"move",{item_id:place.id,direction:-1})));
      if(index<day.activities.length-1)actions.append(button("下移",()=>submit(day,slot,"move",{item_id:place.id,direction:1})));
    }
    if(place&&["activity","meal","hotel"].includes(item.item_type)){
      const kind=item.item_type==="meal"?"restaurant":item.item_type;
      const recorded=journalEntries.some(entry=>entry.day_id===day.day_id&&entry.item_type===kind&&entry.item_id===place.id);
      actions.append(button(recorded?"编辑记录":"记录",()=>journalEditor(body,day,place,item.item_type),"record"));
    }
    body.append(actions);row.append(when,rail,body);root.append(row);
  }
  const additions=$("additions");additions.replaceChildren();
  additions.append(button("＋ 添加景点",()=>editor(additions,day,"activities",null,null),"add-button"));
  if(!day.hotel)additions.append(button("＋ 添加当晚住宿",()=>editor(additions,day,"hotel",null,null),"add-button"));
  resize();if(refreshMap)updateMarkers();
}
function selectDay(id){if(pending)return;selectedDay=id;activeId=null;selectedPlaceRef=null;TravelMindDetails.clear();try{sessionStorage.setItem("day:"+plan.trip_id,id);}catch{}renderDays();renderDay();revealInPage($("journey-content"),true);feedback("");}
function focusPlace(id){
  const day=current(),place=day.places[id],item=day.timeline.find(t=>t.reference_id===id);
  if(!place||!item)return;
  selectedPlaceRef={dayId:day.day_id,itemType:item.item_type,id:place.id,place_id:place.place_id||null,
    fingerprint:JSON.stringify([place.name,place.place_id,place.latitude,place.longitude])};
  TravelMindDetails.select(place,day,labels[item.item_type],loadMap);
  surfaceDocument.querySelectorAll(".event").forEach(n=>n.classList.toggle("selected",n.dataset.ref===id));activeId=id;
  surfaceDocument.querySelectorAll(".event .title").forEach(n=>n.setAttribute("aria-pressed",n.closest(".event").dataset.ref===id?"true":"false"));
  for(const [key,data] of markers)data.content.classList.toggle("active",key===id);
  const selected=markers.get(id);if(!selected)return;
  const content=el("div");content.append(el("strong",selected.place.name),el("p",labels[selected.type]),el("p",selected.place.address||""));
  info.setContent(content);info.open({map,anchor:selected.marker});map.panTo(selected.marker.position);
  surfaceDocument.querySelectorAll(".event").forEach(n=>{if(n.dataset.ref===id)revealInPage(n);});
}
function loadMap(){
  if(mapPromise)return mapPromise;
  if(!configuredKey){$("map-status").textContent=mapMessage;return Promise.resolve(false);}
  $("map-status").textContent="正在加载 Google Maps…";
  mapPromise=new Promise(resolve=>{
    let settled=false;const fail=message=>{if(settled)return;settled=true;$("map-status").textContent=message;resolve(false);};
    window.gm_authFailure=()=>{settled=false;fail("Google Maps 授权失败，请检查 Maps JavaScript API、结算及浏览器密钥的 HTTP Referer 限制。");};
    window.initTravelMindMap=()=>{if(settled)return;settled=true;
      map=new google.maps.Map($("map"),{center:{lat:0,lng:0},zoom:2,mapId:"DEMO_MAP_ID",mapTypeControl:false,streetViewControl:false,fullscreenControl:true,gestureHandling:"cooperative"});
      info=new google.maps.InfoWindow();$("map-status").textContent="";resolve(true);
    };
    const script=surfaceDocument.createElement("script");const params=new URLSearchParams({key:configuredKey,callback:"initTravelMindMap",v:"weekly",libraries:"marker",loading:"async",language:"zh-CN"});
    script.src="https://maps.googleapis.com/maps/api/js?"+params;script.async=true;script.onerror=()=>fail("Google Maps 加载失败，请检查网络连接。");surfaceDocument.head.append(script);
    setTimeout(()=>fail("地图加载超时，请检查网络及浏览器 API 配置。"),15000);
  });return mapPromise;
}
async function updateMarkers(){
  const requested=selectedDay;if(!await loadMap())return;if(requested!==selectedDay)return;
  info.close();for(const data of markers.values())data.marker.map=null;markers.clear();
  const day=current(),bounds=new google.maps.LatLngBounds();let count=0;
  for(const item of day.timeline){const place=day.places[item.reference_id];
    if(!place||!Number.isFinite(place.latitude)||!Number.isFinite(place.longitude))continue;
    if(place.latitude < -90||place.latitude>90||place.longitude < -180||place.longitude>180)continue;
    const position={lat:place.latitude,lng:place.longitude},content=el("div",String(++count),"marker-label "+item.item_type);
    const marker=new google.maps.marker.AdvancedMarkerElement({map,position,title:place.name,content,gmpClickable:true});
    marker.addEventListener("gmp-click",()=>focusPlace(place.id));markers.set(place.id,{marker,place,type:item.item_type,content});bounds.extend(position);
  }
  $("map-status").textContent=count?"":"当天暂无可展示的地点坐标。";
  if(count===1){map.setCenter(bounds.getCenter());map.setZoom(14);}else if(count>1){map.fitBounds(bounds,44);google.maps.event.addListenerOnce(map,"idle",()=>{if(map.getZoom()>16)map.setZoom(16);});}
  if(activeId&&markers.has(activeId))focusPlace(activeId);
}
window.addEventListener("message",event=>{
  if(event.source!==window.parent||event.data.type!=="streamlit:render")return;
  const args=event.data.args;if(!args?.plan)return;
  // Streamlit can resend unchanged props on layout/resize notifications. Do not
  // destroy an in-progress editor (or recreate markers) for those notifications.
  const signature=JSON.stringify([args.plan,args.browser_key,args.map_message,args.acknowledgment,args.conversation,args.journal_entries]);
  if(signature===renderedSignature)return;
  if(pending&&args.acknowledgment?.event_id!==pending)return;
  renderedSignature=signature;
  const acknowledgment=args.acknowledgment;
  const journalOnly=acknowledgment?.success&&acknowledgment.kind==="journal"&&plan&&JSON.stringify(plan)===JSON.stringify(args.plan);
  if(acknowledgment&&acknowledgment.event_id===pending){
    pending=null;surfaceDocument.querySelectorAll("button,input,textarea").forEach(n=>n.disabled=false);
    if(!acknowledgment.success){feedback(acknowledgment.message,true);lastAck=acknowledgment.event_id;return;}
  }
  const differentTrip=plan?.trip_id!==args.plan.trip_id;plan=args.plan;configuredKey=args.browser_key||"";mapMessage=args.map_message||"";
  journalEntries=args.journal_entries||[];
  if(differentTrip){selectedPlaceRef=null;activeId=null;TravelMindDetails.clear();}
  if(selectedPlaceRef){const place=current().places[selectedPlaceRef.id];
    if(!place||selectedPlaceRef.dayId!==selectedDay||selectedPlaceRef.fingerprint!==JSON.stringify([place.name,place.place_id,place.latitude,place.longitude])){
      selectedPlaceRef=null;activeId=null;TravelMindDetails.clear();
    }
  }
  if(differentTrip||!plan.days.some(d=>d.day_id===selectedDay)){
    try{selectedDay=sessionStorage.getItem("day:"+plan.trip_id);}catch{}if(!plan.days.some(d=>d.day_id===selectedDay))selectedDay=plan.days[0].day_id;
  }
  renderDays();renderDay(!journalOnly);
  renderChat(args.conversation||[]);
  if(acknowledgment?.success&&acknowledgment.event_id!==lastAck){feedback(acknowledgment.message||"已保存 · 时间线与地图已同步",!!acknowledgment.assistant_status&&acknowledgment.assistant_status!=="success");lastAck=acknowledgment.event_id;}
});
function sendChat(message){
  if(pending||!message.trim())return;
  pendingPrompt=message;pending=crypto.randomUUID();$("chat-status").textContent="正在解析修改范围并处理，请稍候…";
  $("chat-retry").hidden=true;surfaceDocument.querySelectorAll("button,input,textarea").forEach(n=>n.disabled=true);
  post("streamlit:setComponentValue",{dataType:"json",value:{event_id:pending,kind:"assistant",trip_id:plan.trip_id,
    trip_version:plan.version,message,selected_day_id:selectedDay,selected_place_ref:selectedPlaceRef}});
}
function renderChat(messages){
  $("chat-context").textContent=`${plan.destination} · ${plan.total_days} 天`;
  $("chat-messages").replaceChildren(...messages.map(m=>{const n=el("div",undefined,"chat-message "+m.role);n.append(el("small",m.role==="user"?"你":"TravelMind"),el("p",m.text));return n;}));
  $("chat-count").textContent=messages.length?`(${messages.length})`:"";
  const last=messages[messages.length-1];$("chat-status").textContent=last?.role==="assistant"?last.text:"";
  $("chat-retry").hidden=!last||last.status!=="failed";
  if(last?.status==="success")$("chat-input").value="";
}
$("chat-form").onsubmit=e=>{e.preventDefault();sendChat($("chat-input").value);};
$("chat-retry").onclick=()=>sendChat(pendingPrompt||$("chat-input").value);
post("streamlit:componentReady",{apiVersion:1});resize();
