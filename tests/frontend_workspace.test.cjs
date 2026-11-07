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
}
function setup() {
  const nodes=new Map(), sent=[], selections=[], scrollRequests=[];
  const document={body:{scrollHeight:800},documentElement:{getBoundingClientRect:()=>({height:800})},createElement:tag=>new Element(tag),
    getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Element('div'));return nodes.get(id);},
    querySelectorAll:()=>[],querySelector:()=>new Element('div')};
  const parent={postMessage:value=>sent.push(value)};
  const context=vm.createContext({document,window:{parent,innerHeight:800,scrollBy:options=>scrollRequests.push(options),addEventListener(){},matchMedia:()=>({matches:true})},
    ResizeObserver:class {observe(){}},crypto:{randomUUID:()=> 'event-1'},
    TravelMindDetails:{select:(...args)=>selections.push(args),clear(){}},sessionStorage:{setItem(){}},
    URL,URLSearchParams,setTimeout,console});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../travelmind/frontend/app.js'),'utf8'),context);
  vm.runInContext(`
    const place={id:'p1',name:'Museum',address:'Harbour Road',latitude:null,longitude:null};
    const day={day_id:'d1',day_index:1,date:'2026-11-07',title:'Harbour day',places:{p1:place},
      activities:[place],timeline_warnings:[],notes:[],timeline:[{id:'t1',reference_id:'p1',item_type:'activity',title:'Museum',start_time:'09:00',end_time:'10:00'}]};
    plan={trip_id:'trip1',version:3,total_days:1,days:[day]};selectedDay='d1';
    const originalUpdateMarkers=updateMarkers;updateMarkers=()=>{};renderDay();
  `,context);
  return {context,nodes,sent,selections,scrollRequests};
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
