"use strict";
// Static Streamlit component protocol; no npm bundle, no backend credential.
const post = (type, payload = {}) => window.parent.postMessage({isStreamlitMessage: true, type, ...payload}, "*");
const $ = id => document.getElementById(id);
const el = (tag, text, cls) => {const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n;};
const mealLabels = {breakfast:"早餐",lunch:"午餐",dinner:"晚餐"};
const labels = {activity:"景点",meal:"餐饮",hotel:"当晚住宿",custom:"自由安排"};
let plan, selectedDay, map, info, mapPromise, activeId, pending, lastAck, renderedSignature;
const markers = new Map();
let configuredKey = "", mapMessage = "";
function resize(){post("streamlit:setFrameHeight", {height:Math.max(720, document.body.scrollHeight + 24)});}
new ResizeObserver(resize).observe(document.body);
function feedback(message, error=false){const n=$("feedback");n.replaceChildren(); if(message)n.append(el("div",message,"notice "+(error?"error":"success")));resize();}
function button(text, fn, cls){const b=el("button",text,cls);b.type="button";b.onclick=fn;return b;}
function current(){return plan.days.find(d=>d.day_id===selectedDay)||plan.days[0];}
function submit(day,slot,action,args={}){
  if(pending)return;
  const event={event_id:crypto.randomUUID(),trip_id:plan.trip_id,day_id:day.day_id,slot,action,arguments:args};
  pending=event.event_id;feedback("正在保存到本机…");document.querySelectorAll("button,input").forEach(n=>n.disabled=true);
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
function details(place,day){
  const box=el("details"),summary=el("summary","地点详情");box.append(summary);
  const field=(label,value)=>{if(value!==null&&value!==undefined&&value!=="")box.append(el("div",`${label}：${value}`,"detail-line"));};
  field("名称",place.name);field("地址",place.address);field("描述",place.description);
  if(place.cuisine?.length)field("菜系",place.cuisine.join("、"));
  field("价格等级",place.price_level);field("评分",place.rating);
  if(place.ticket_price!==null&&place.ticket_price!==undefined)field("参考门票",`${place.ticket_price} ${place.currency||"币种未知"}`);
  if(place.price_per_night!==null&&place.price_per_night!==undefined)field("参考每晚价格",`${place.price_per_night} ${place.currency||"币种未知"}`);
  if(place.estimated_duration_minutes!==null&&place.estimated_duration_minutes!==undefined)field("建议游览时长",`${place.estimated_duration_minutes} 分钟`);
  if(place.opening_hours!==undefined){field("营业时间",`${day.date} · ${new Date(day.date+"T12:00:00").toLocaleDateString("zh-CN",{weekday:"long"})}`);field("当天时段",place.opening_today?.join(" / ")||"暂无数据");}
  if(place.source_url){try{const url=new URL(place.source_url);if(url.protocol==="https:"){const a=el("a","在 Google Maps 查看来源");a.href=url.href;a.target="_blank";a.rel="noopener noreferrer";box.append(a);}}catch{}}
  box.ontoggle=resize;return box;
}
function renderDays(){
  $("days").replaceChildren();
  for(const day of plan.days){const b=button("",()=>selectDay(day.day_id),"day"+(day.day_id===selectedDay?" active":""));b.setAttribute("aria-pressed",day.day_id===selectedDay?"true":"false");
    b.append(el("strong",`Day ${day.day_index}`),el("small",day.date),el("span",day.title||"探索与发现","theme"));
    const meals=[day.breakfast,day.lunch,day.dinner].filter(Boolean).length;b.append(el("small",`${day.activities.length} 个景点 · ${meals} 餐${day.hotel?" · 已选住宿":""}`));$("days").append(b);
  }
}
function renderDay(){
  const day=current();$("date-label").textContent=`DAY ${day.day_index} / ${plan.total_days} · ${day.date}`;
  $("day-title").textContent=day.title||`${plan.destination} · 这一天`;
  $("warnings").replaceChildren(...day.timeline_warnings.map(t=>el("div",t,"notice")));
  $("notes").replaceChildren(...day.notes.map(t=>el("div",t,"notice")));
  const root=$("timeline");root.replaceChildren();
  for(const item of day.timeline){
    const place=day.places[item.reference_id],slot=item.item_type==="meal"?item.meal_type:item.item_type==="activity"?"activities":"hotel";
    const row=el("article",undefined,`event ${item.item_type}`);row.dataset.ref=item.reference_id||item.id;
    const when=el("div",item.start_time?.slice(0,5)||"待安排","when");if(item.end_time)when.append(el("span",item.end_time.slice(0,5)));
    const rail=el("div",undefined,"rail");rail.append(el("i",undefined,"dot"));
    const body=el("div",undefined,"event-body");body.append(el("div",item.meal_type?mealLabels[item.meal_type]:labels[item.item_type],"kind"));
    const title=button(item.title,()=>focusPlace(item.reference_id),"title"+(!place?" empty":""));body.append(title);
    if(place?.address)body.append(el("p",place.address,"address"));
    if(place&&(!Number.isFinite(place.latitude)||!Number.isFinite(place.longitude)))body.append(el("p","暂无地图位置","subtle"));
    if(item.notes)body.append(el("p",item.notes,"subtle"));
    if(place)body.append(details(place,day));
    const actions=el("div",undefined,"actions");
    if(item.item_type!=="custom")actions.append(button(place?"更换地点":"添加地点",()=>editor(body,day,slot,place,item)));
    actions.append(button("编辑时间",()=>editor(body,day,slot,place,item,"time")));
    if(place)actions.append(button("删除",()=>submit(day,slot,"delete",{item_id:slot==="activities"?place.id:null}),"danger"));
    if(item.item_type==="activity"&&place){const index=day.activities.findIndex(a=>a.id===place.id);
      if(index>0)actions.append(button("↑",()=>submit(day,slot,"move",{item_id:place.id,direction:-1})));
      if(index<day.activities.length-1)actions.append(button("↓",()=>submit(day,slot,"move",{item_id:place.id,direction:1})));
    }
    body.append(actions);row.append(when,rail,body);root.append(row);
  }
  const additions=$("additions");additions.replaceChildren();
  additions.append(button("＋ 添加景点",()=>editor(additions,day,"activities",null,null),"add-button"));
  if(!day.hotel)additions.append(button("＋ 添加当晚住宿",()=>editor(additions,day,"hotel",null,null),"add-button"));
  resize();updateMarkers();
}
function selectDay(id){if(pending)return;selectedDay=id;activeId=null;try{sessionStorage.setItem("day:"+plan.trip_id,id);}catch{}renderDays();renderDay();feedback("");}
function focusPlace(id){
  document.querySelectorAll(".event").forEach(n=>n.classList.toggle("selected",n.dataset.ref===id));activeId=id;
  for(const [key,data] of markers)data.content.classList.toggle("active",key===id);
  const selected=markers.get(id);if(!selected)return;
  const content=el("div");content.append(el("strong",selected.place.name),el("p",labels[selected.type]),el("p",selected.place.address||""));
  info.setContent(content);info.open({map,anchor:selected.marker});map.panTo(selected.marker.position);
  document.querySelectorAll(".event").forEach(n=>{if(n.dataset.ref===id)n.scrollIntoView({block:"nearest",behavior:"smooth"});});
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
    const script=document.createElement("script");const params=new URLSearchParams({key:configuredKey,callback:"initTravelMindMap",v:"weekly",libraries:"marker",loading:"async",language:"zh-CN"});
    script.src="https://maps.googleapis.com/maps/api/js?"+params;script.async=true;script.onerror=()=>fail("Google Maps 加载失败，请检查网络连接。");document.head.append(script);
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
    const marker=new google.maps.marker.AdvancedMarkerElement({map,position,title:place.name,content});
    marker.addListener("click",()=>focusPlace(place.id));markers.set(place.id,{marker,place,type:item.item_type,content});bounds.extend(position);
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
  const signature=JSON.stringify([args.plan,args.browser_key,args.map_message,args.acknowledgment]);
  if(signature===renderedSignature)return;
  if(pending&&args.acknowledgment?.event_id!==pending)return;
  renderedSignature=signature;
  const acknowledgment=args.acknowledgment;
  if(acknowledgment&&acknowledgment.event_id===pending){
    pending=null;document.querySelectorAll("button,input").forEach(n=>n.disabled=false);
    if(!acknowledgment.success){feedback(acknowledgment.message,true);lastAck=acknowledgment.event_id;return;}
  }
  const differentTrip=plan?.trip_id!==args.plan.trip_id;plan=args.plan;configuredKey=args.browser_key||"";mapMessage=args.map_message||"";
  if(differentTrip||!plan.days.some(d=>d.day_id===selectedDay)){
    try{selectedDay=sessionStorage.getItem("day:"+plan.trip_id);}catch{}if(!plan.days.some(d=>d.day_id===selectedDay))selectedDay=plan.days[0].day_id;
  }
  renderDays();renderDay();
  if(acknowledgment?.success&&acknowledgment.event_id!==lastAck){feedback("已保存 · 时间线与地图已同步");lastAck=acknowledgment.event_id;}
});
post("streamlit:componentReady",{apiVersion:1});resize();
