// Run with: node --test tests/frontend_workspace.test.cjs
// Exercise the actual workspace code with a small DOM adapter; no Maps network calls.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.dataset={}; this.attributes={}; this.classList={toggle(){}}; }
  append(...children) { for(const child of children){child.parent=this;this.children.push(child);} }
  replaceChildren(...children) { this.children=[];this.append(...children); }
  setAttribute(key,value) { this.attributes[key]=value; }
  querySelector() { return null; }
  focus() { this.focused=true; }
  getBoundingClientRect() { return {top:400,bottom:500,height:100}; }
  setCustomValidity(message) { this.validationMessage=message; }
  reportValidity() { return !this.validationMessage; }
}
function setup() {
  const nodes=new Map(), sent=[], selections=[], scrollRequests=[], listeners={};
  const document={body:{scrollHeight:800},documentElement:{getBoundingClientRect:()=>({height:800})},createElement:tag=>new Element(tag),
    getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Element('div'));return nodes.get(id);},
    querySelectorAll:()=>[],querySelector:()=>new Element('div')};
  const parent={postMessage:value=>sent.push(value)};
  const context=vm.createContext({document,window:{parent,innerHeight:800,scrollBy:options=>scrollRequests.push(options),addEventListener:(name,fn)=>{listeners[name]=fn;},matchMedia:()=>({matches:true})},
    ResizeObserver:class {observe(){}},crypto:{randomUUID:()=> 'event-1'},
    RoamoryDetails:{select:(...args)=>selections.push(args),clear(){}},sessionStorage:{setItem(){}},
    URL,URLSearchParams,setTimeout,console});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../roamory/frontend/app.js'),'utf8'),context);
  vm.runInContext(`
    const place={id:'p1',name:'Museum',address:'Harbour Road',latitude:null,longitude:null};
    const day={day_id:'d1',day_index:1,date:'2026-11-07',title:'Harbour day',places:{p1:place},
      activities:[place],timeline_warnings:[],notes:[],timeline:[{id:'t1',reference_id:'p1',item_type:'activity',title:'Museum',start_time:'09:00',end_time:'10:00'}]};
    plan={trip_id:'trip1',version:3,total_days:1,days:[day]};selectedDay='d1';
    const originalUpdateMarkers=updateMarkers;updateMarkers=()=>{};renderDay();
  `,context);
  return {context,nodes,sent,selections,scrollRequests,listeners,parent};
}
function click(control) {
  let stopped=false;
  control.onclick({stopPropagation(){stopped=true;}});
  return stopped;
}
test('timeline has no duplicate details and selecting a card updates M2 without coordinates',()=>{
  const {context,nodes,selections}=setup();
  const body=nodes.get('timeline').children[0].children[2];
  assert.equal(body.children.some(n=>n.tagName==='details'),false);
  body.onclick({target:{closest:()=>null}});
  assert.equal(selections.length,1);
  assert.equal(selections[0][0].id,'p1');
  assert.equal(vm.runInContext('activeId',context),'p1');
  assert.equal(vm.runInContext('selectedPlaceRef.dayId',context),'d1');
});
test('replace, time and delete controls are independent of selection and retain event contract',()=>{
  const {nodes,sent,selections}=setup();
  const body=nodes.get('timeline').children[0].children[2];
  const actions=body.children.find(n=>n.className==='actions');
  assert.equal(click(actions.children[0]),true);
  assert.equal(body.children.at(-1).className,'edit-form');
  assert.equal(click(actions.children[1]),true);
  assert.equal(body.children.at(-1).children[0].className,'form-row');
  assert.equal(click(actions.children[2]),true);
  assert.equal(selections.length,0);
  const event=sent.find(n=>n.type==='streamlit:setComponentValue').value;
  assert.equal(event.action,'delete');assert.equal(event.trip_version,3);
  assert.equal(event.day_id,'d1');assert.equal(event.arguments.item_id,'p1');
});
test('M1 skips unknown coordinates, marker selection updates M2 and switching day clears both',async()=>{
  const {context,nodes,sent,selections,scrollRequests}=setup();
  context.google={maps:{LatLngBounds:class {extend(){} getCenter(){return {}; }},
    marker:{AdvancedMarkerElement:class {constructor(options){Object.assign(this,options);}addEventListener(name,fn){this.click=fn;}}},
    event:{addListenerOnce(){}}}};
  vm.runInContext(`loadMap=()=>Promise.resolve(true);info={close(){},setContent(){},open(){}};
    map={panTo(){},setCenter(){},setZoom(){},fitBounds(){}};`,context);
  await vm.runInContext('originalUpdateMarkers()',context);
  assert.equal(vm.runInContext('markers.size',context),0);
  vm.runInContext('place.latitude=35;place.longitude=139;',context);
  await vm.runInContext('originalUpdateMarkers()',context);
  assert.equal(vm.runInContext('markers.size',context),1);
  vm.runInContext("markers.get('p1').marker.click()",context);
  assert.equal(selections.length,1);
  assert.equal(vm.runInContext('activeId',context),'p1');
  vm.runInContext("plan.days.push({...day,day_id:'d2',day_index:2,places:{},timeline:[],activities:[]});selectDay('d2');",context);
  await vm.runInContext('originalUpdateMarkers()',context);
  assert.equal(vm.runInContext('markers.size',context),0);
  assert.equal(vm.runInContext('selectedPlaceRef',context),null);
  assert.equal(scrollRequests.at(-1).behavior,'instant');
  assert.equal(sent.some(message=>message.type==='streamlit:setComponentValue'),false);
});
test('journal button edits existing text and sends a separate revision-checked event',()=>{
  const {context,nodes,sent,selections}=setup();
  vm.runInContext("journalEntries=[{day_id:'d1',item_type:'activity',item_id:'p1',content:'Existing memory',revision:2}]",context);
  const body=nodes.get('timeline').children[0].children[2];
  const record=body.children.find(n=>n.className==='actions').children.at(-1);
  assert.equal(record.textContent,'记录');
  assert.equal(click(record),true);
  assert.equal(selections.length,0);
  const form=body.children.at(-1),textarea=form.children[1].children[0];
  assert.equal(textarea.value,'Existing memory');
  textarea.value='New memory';form.onsubmit({preventDefault(){}});
  const event=sent.find(n=>n.type==='streamlit:setComponentValue').value;
  assert.equal(event.kind,'journal');assert.equal(event.item_type,'activity');
  assert.equal(event.item_id,'p1');assert.equal(event.revision,2);
  assert.equal(event.content,'New memory');
  assert.equal(event.action,undefined);
});
test('journal-only acknowledgment preserves markers and selected card',()=>{
  const {context,nodes,listeners,parent,selections}=setup();
  vm.runInContext("pending='journal-1';activeId='p1';globalThis.refreshes=0;updateMarkers=()=>refreshes++;markers.set('p1',{sentinel:true})",context);
  const plan=JSON.parse(vm.runInContext('JSON.stringify(plan)',context));
  listeners.message({source:parent,data:{type:'streamlit:render',args:{plan,
    acknowledgment:{event_id:'journal-1',kind:'journal',success:true},
    journal_entries:[{day_id:'d1',item_id:'p1',item_type:'activity',content:'A memory',revision:1}]}}});
  assert.equal(vm.runInContext('refreshes',context),0);
  assert.equal(vm.runInContext("markers.get('p1').sentinel",context),true);
  assert.equal(selections.length,0);
  assert.ok(nodes.get('timeline').children[0].className.includes('selected'));
  const actions=nodes.get('timeline').children[0].children[2].children.find(n=>n.className==='actions');
  assert.equal(actions.children.at(-1).textContent,'编辑记录');
});
test('journal supports photo selection, preview removal and an image-only save',async()=>{
  const {context,nodes,sent}=setup();
  context.FileReader=class {readAsDataURL(file){this.result=file.data_url;this.onload();}};
  vm.runInContext("journalEntries=[{day_id:'d1',item_type:'activity',item_id:'p1',content:'',revision:1,images:[{id:'old-photo',name:'old.png',thumbnail_url:'data:image/png;base64,old'}]}]",context);
  const body=nodes.get('timeline').children[0].children[2];
  click(body.children.find(n=>n.className==='actions').children.at(-1));
  const form=body.children.at(-1),files=form.children[3].children[0],gallery=form.children[4];
  assert.equal(gallery.children.length,1);
  click(gallery.children[0].children[1]);
  assert.equal(gallery.children.length,0);
  files.files=[{name:'new.png',type:'image/png',size:20,data_url:'data:image/png;base64,new'}];
  await files.onchange();
  assert.equal(gallery.children.length,1);
  form.onsubmit({preventDefault(){}});
  const event=sent.find(n=>n.type==='streamlit:setComponentValue').value;
  assert.equal(event.content,'');
  assert.equal(event.keep_image_ids.length,0);
  assert.equal(event.new_images[0].name,'new.png');
  assert.equal(event.revision,1);
});
