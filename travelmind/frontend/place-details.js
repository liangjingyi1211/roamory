"use strict";
// Only selected-place details are fetched. No persistent cache, scraping or iframe.
window.TravelMindDetails = (() => {
  const surfaceDocument = window.TravelMindSurface?.document || document;
  const node=(tag,text,cls)=>{const n=surfaceDocument.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  let sequence=0, selectedKey=null, currentResult=null, currentSelection=null;
  const inflight=new Map();
  const root=()=>surfaceDocument.getElementById("place-detail");
  const safeLink=(text,url)=>{try{const u=new URL(url);if(u.protocol!=="https:")return null;const a=node("a",text);a.href=u.href;a.target="_blank";a.rel="noopener noreferrer";return a;}catch{return null;}};
  function hoursForDate(hours,date){
    const weekday=new Date(date+"T12:00:00").getDay(),previous=(weekday+6)%7,result=[];
    const periods=hours?.periods||[];
    const minutes=p=>(p.hour||0)*60+(p.minute||0);
    const display=p=>String(p.hour||0).padStart(2,"0")+":"+String(p.minute||0).padStart(2,"0");
    for(const period of periods){const a=period.open,b=period.close;if(!a)continue;
      if(!b&&periods.length===1&&a.day===0&&minutes(a)===0)return ["24 小时营业（官方数据）"];
      if(!b)continue;
      if(a.day===weekday)result.push(display(a)+"–"+display(b)+(b.day!==a.day||minutes(b)<=minutes(a)?"（次日）":""));
      else if(a.day===previous&&b.day===weekday&&minutes(b)>0)result.push("00:00–"+display(b)+"（前日延续）");
    }
    if(result.length)return result;
    const names=[["星期日","Sunday"],["星期一","Monday"],["星期二","Tuesday"],["星期三","Wednesday"],["星期四","Thursday"],["星期五","Friday"],["星期六","Saturday"]][weekday];
    const explicit=(hours?.weekdayDescriptions||[]).find(t=>names.some(n=>t.startsWith(n)));
    return explicit&&/(休息|关闭|Closed)/i.test(explicit)?["当日休息（官方数据）"]:[];
  }
  function draw(local,day,official,error,retry){
    const box=root();box.replaceChildren();
    if(error){box.append(node("div",error,"notice"));if(retry){const b=node("button","重试详情","detail-retry");b.onclick=retry;box.append(b);}}
    const data=official||{};
    if(data.photos?.length){const photo=data.photos[0];try{
      const image=node("img");image.className="place-photo";image.alt=data.displayName||local.name;image.src=photo.getURI({maxWidth:800,maxHeight:400});image.loading="lazy";
      image.onerror=()=>image.remove();box.append(image);
      const attrs=node("div",undefined,"photo-attribution");for(const author of photo.authorAttributions||[]){const a=safeLink(author.displayName,author.uri);attrs.append(a||node("span",author.displayName));}box.append(attrs);
    }catch{}}
    box.append(node("h2",data.displayName||local.name));
    const field=(label,value)=>{if(value!==null&&value!==undefined&&value!=="")box.append(node("p",`${label}：${value}`,"place-field"));};
    field("类型",data.primaryTypeDisplayName||local.category);
    if(data.rating!==undefined&&data.rating!==null)field("评分",`${data.rating} / 5${data.userRatingCount!==undefined?` · ${data.userRatingCount} 条评价`:""}`);
    else field("评分",local.rating);
    field("地址",data.formattedAddress||local.address);field("价格等级",data.priceLevel||local.price_level);field("描述",local.description);
    if(local.cuisine?.length)field("菜系",local.cuisine.join("、"));
    const hours=data.regularOpeningHours?hoursForDate(data.regularOpeningHours,day.date):(local.opening_today||[]);
    field("旅行当天",day.date);field("营业时间",hours.join(" / ")||"暂无数据");
    const source=safeLink("在 Google Maps 查看来源",data.googleMapsURI||local.source_url);if(source)box.append(source);
    for(const attr of data.attributions||[]){const a=safeLink(attr.provider,attr.providerURI);box.append(a||node("small",attr.provider||""));}
    if(official){const branding=node("div",undefined,"google-attribution");const logo=node("img");logo.src="https://maps.gstatic.com/mapfiles/api-3/images/powered-by-google-on-white3.png";logo.alt="Google Maps";branding.append(logo);box.append(branding);}
    if(!local.place_id)box.append(node("p","无可用地图详情 · 仅显示已保存字段，不自动匹配同名地点","subtle"));
  }
  function clear(){sequence++;selectedKey=null;currentResult=null;currentSelection=null;surfaceDocument.getElementById("place-kind").textContent="";root().replaceChildren(node("div","选择行程中的地点查看详情","place-empty"));}
  async function select(local,day,kind,loadSDK,force=false){
    const key=day.day_id+":"+local.id+":"+(local.place_id||"");
    if(!force&&selectedKey===key&&currentResult)return;
    const token=++sequence;selectedKey=key;currentSelection={local,day,kind,loadSDK};currentResult=null;
    surfaceDocument.getElementById("place-kind").textContent=kind;
    if(!local.place_id){draw(local,day,null,null);currentResult={local:true};return;}
    draw(local,day,null,"正在按 Place ID 加载 Google 官方详情…");
    try{
      if(!await loadSDK())throw Error("地图 SDK 不可用，请检查浏览器密钥与 Maps JavaScript API。");
      if(token!==sequence)return;
      let request=inflight.get(local.place_id);
      if(!request){request=(async()=>{
        const {Place}=await google.maps.importLibrary("places");const place=new Place({id:local.place_id});
        await place.fetchFields({fields:["displayName","formattedAddress","primaryTypeDisplayName","rating","userRatingCount","googleMapsURI","priceLevel","regularOpeningHours","photos","attributions"]});return place;
      })();inflight.set(local.place_id,request);request.finally(()=>inflight.delete(local.place_id)).catch(()=>{});}
      let timeout;const result=await Promise.race([request,new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error("地点详情请求超时")),12000);})]).finally(()=>clearTimeout(timeout));
      if(token!==sequence)return;currentResult=result;draw(local,day,result,null);
    }catch(error){if(token!==sequence)return;
      // Do not print URLs or SDK messages that may contain an API key.
      const code=String(error.code||error.status||error.name||"ERROR").replace(/[^A-Za-z0-9_]/g,"").slice(0,70);
      draw(local,day,null,`官方详情暂不可用（${code}）。已保留已有资料；请检查 Places API (New)、浏览器密钥 API 限制、配额与结算。`,()=>select(local,day,kind,loadSDK,true));
    }
  }
  return {select,clear,hoursForDate};
})();
