"use strict";
// Actual semantic/transcript consumer scripts. Deferred HTTP is a UI fixture,
// not a claim of live Gemma inference or PDF parsing.
const assert=require("node:assert/strict"), fs=require("node:fs"), path=require("node:path"), vm=require("node:vm");
const rootPath=path.join(__dirname,"../../src/academic_assistant/web");
const html=fs.readFileSync(path.join(rootPath,"index.html"),"utf8");
const tick=()=>new Promise(resolve=>setImmediate(resolve));
class Node {
  constructor(tag="div") {this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.listeners={};this.attributes={};this._text="";this.value="";this.hidden=false;this.disabled=false;this.checked=false;this.files=[];}
  get textContent(){return this._text+this.children.map(c=>c.textContent).join("");}
  set textContent(text){this.replaceChildren();this._text=String(text);}
  set innerHTML(_){throw Error("Unsafe HTML assignment");}
  append(...children){for(const child of children){child.remove();child.parent=this;this.children.push(child);}}
  appendChild(child){this.append(child);return child;}
  remove(){if(this.parent){this.parent.children=this.parent.children.filter(c=>c!==this);this.parent=null;}}
  replaceChildren(...children){for(const c of this.children)c.parent=null;this.children=[];this._text="";this.append(...children);}
  setAttribute(k,v){this.attributes[k]=String(v);}
  all(){return [this,...this.children.flatMap(c=>c.all())];}
  querySelector(s){if(s==="button[type='submit']")return this.children.find(c=>c.type==="submit");const f=s.match(/^\[data-field='([^']+)'\]$/)?.[1];assert.ok(f,s);return this.all().find(c=>c.dataset.field===f);}
  addEventListener(type,fn,capture=false){(this.listeners[type]??=[]).push({fn,capture:!!capture});}
  async emit(type,extra={}){const event={type,target:this,preventDefault(){},stopImmediatePropagation(){this.stopped=true;},...extra};for(const item of [...(this.listeners[type]||[])].sort((a,b)=>Number(b.capture)-Number(a.capture))){if(event.stopped)break;await item.fn(event);}}
  dispatchEvent(event){return this.emit(event.type);}
  requestSubmit(){this.pendingSubmit=this.emit("submit");}
  focus(){this.focused=true;} scrollIntoView(){} setCustomValidity(){} reportValidity(){return true;}
}
function setup(){
  const root=new Node("body"), elements=new Map();
  for(const m of html.matchAll(/<([a-z][a-z0-9]*)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){
    const n=new Node(m[1]);n.id=m[3];n.hidden=/\bhidden\b/.test(m[2]);
    for(const d of m[2].matchAll(/data-([a-z-]+)="([^"]+)"/g))n.dataset[d[1].replace(/-([a-z])/g,(_,v)=>v.toUpperCase())]=d[2];
    elements.set(n.id,n);root.append(n);
  }
  const credits=[new Node("input")];credits[0].dataset.creditMetric="credits.graduation.total";
  const examples=[...html.matchAll(/data-transcript-example="([^"]+)"/g)].map(m=>{const n=new Node("button");n.dataset.transcriptExample=m[1];return n;});
  const questionButtons=[...html.matchAll(/data-transcript-question="([^"]+)"/g)].map(m=>{const n=new Node("button");n.dataset.transcriptQuestion=m[1];return n;});
  const el=id=>elements.get(id)||root.all().find(n=>n.id===id)||null;
  assert.equal(el("new-conversation").dataset.assistantMode,"semantic");assert.equal(examples.length,3);
  const submit=new Node("button");submit.type="submit";el("question-form").append(submit);
  const calls=[], evidenceCalls=[], window=new Node("window");window.location={hostname:"demo.invalid"};
  window.AcademicEvidence={button:(ref,index)=>{evidenceCalls.push({ref,index});return new Node("button");}};
  const document={getElementById:el,createElement:tag=>new Node(tag),createTextNode:text=>{const n=new Node();n.textContent=text;return n;},querySelectorAll:s=>s==="[data-credit-metric]"?credits:s==="[data-transcript-example]"?examples:s==="[data-transcript-question]"?questionButtons:[]};
  const context={document,window,AbortController,Event,fetch:(url,options)=>new Promise((resolve,reject)=>calls.push({url,options,resolve,reject}))};
  for(const target of [context,window])for(const key of ["localStorage","sessionStorage","indexedDB"])Object.defineProperty(target,key,{get(){throw Error("Persistent storage accessed");}});
  vm.createContext(context);let legacy=0;el("question-form").addEventListener("submit",()=>{legacy++;});
  for(const script of ["transcript.js","semantic-ui.js"])vm.runInContext(fs.readFileSync(path.join(rootPath,script),"utf8"),context,{filename:script});
  return {el,calls,evidenceCalls,window,credits,examples,legacy:()=>legacy};
}
const course={course_name:"컴퓨터구조",course_code:"CDA0016",course_id:"cwnu.cs.2026.course.cda0016",source_id:"source",credits:3,category:"major_required",offering_label:"3학년 1학기"};
function reply(call,value,status=200){call.resolve({ok:status===200,status,json:async()=>JSON.parse(JSON.stringify(value))});}
function answer(updates={}){return {packet_id:"assistant-fixture",status:"supported",kind:"academic",answer:"컴퓨터구조는 3학점 전공필수 과목이에요.",plan_status:"generated",generation_status:"generated",context_question:"컴퓨터구조 학점",parts:[{status:"supported",title:"과목 조회",text:"컴퓨터구조는3학점",course_evidence:{status:"supported",courses:[course],evidence:[]}}],...updates};}
function ask(ui,question){ui.el("question").value=question;return ui.el("question-form").emit("submit");}
async function testSingleSemanticEndpointAndNaturalReply(){
  const ui=setup(),operation=ask(ui,"컴퓨터구조는 몇 학점이야?");
  assert.equal(ui.calls.length,1);assert.equal(ui.calls[0].url,"/v1/academic/assistant");assert.equal(ui.legacy(),0);
  assert.equal(ui.calls[0].options.cache,"no-store");assert.equal(ui.calls[0].options.credentials,"omit");
  reply(ui.calls[0],answer());await operation;
  assert.match(ui.el("answer-text").textContent,/3학점/);assert.match(ui.el("generation-note").textContent,/자연어 답변/);
  assert.match(ui.el("semantic-parts").textContent,/CDA0016/);
  const next=ask(ui,"그럼 몇 학기에 들어?");assert.equal(JSON.parse(ui.calls[1].options.body).previous_question,"컴퓨터구조 학점");
  reply(ui.calls[1],answer());await next;
}
async function testCancellationFailureAndReset(){
  const ui=setup(),old=ask(ui,"운체 학점");await ui.el("new-conversation").emit("click");
  assert.ok(ui.calls[0].options.signal.aborted);reply(ui.calls[0],answer());await old;assert.equal(ui.el("conversation-turns").children.length,0);
  const next=ask(ui,"안녕");assert.equal(JSON.parse(ui.calls[1].options.body).previous_question,null);
  reply(ui.calls[1],answer({status:"out_of_scope",kind:"greeting",parts:[],generation_status:"not_applicable",answer:"안녕하세요!"}));await next;
  assert.equal(ui.el("status-badge").textContent,"대화");assert.doesNotMatch(ui.el("generation-note").textContent,/자연어 답변/);
  const failure=ask(ui,"컴구");reply(ui.calls[2],{},503);await failure;
  assert.match(ui.el("generation-note").textContent,/근거가 없다는 뜻은 아닙니다/);assert.match(ui.el("status-badge").className,/status-error/);
}
function example(id){return {synthetic:true,example_id:id,title:"가상 성적표",extraction:{courses:[course]},transcript:{confirmed:true,courses:[{row_id:"row-1",course_name:"컴퓨터구조",course_code:"CDA0016",credits:3,grade:"A0",category:"major_required"}]},assessment:{answer:"부분 비교",raw_earned_credits:3,checks:[{check_id:"credits.graduation.total",label:"졸업학점",result:"not_met",required:130,earned:3,gap:127,missing_courses:[],evidence_packet:{status:"supported",evidence:[],applied_rules:[]}}],issues:[],verification_items:[]}};}
async function testActualOneClickConsumerAndTranscriptRevision(){
  const ui=setup();for(const button of ui.examples){const operation=button.emit("click");const call=ui.calls.at(-1);assert.equal(call.url,`/v1/academic/transcripts/examples/${button.dataset.transcriptExample}`);assert.equal(call.options.body,"{}");reply(call,example(button.dataset.transcriptExample));await operation;assert.ok(ui.window.AcademicTranscript.current());assert.equal(ui.el("transcript-review").open,false);assert.match(ui.el("transcript-results").textContent,/127학점/);}
  const operation=ask(ui,"남은 전필은?");assert.ok(JSON.parse(ui.calls.at(-1).options.body).transcript.confirmed);
  await ui.el("transcript-clear").emit("click");assert.ok(ui.calls.at(-1).options.signal.aborted);reply(ui.calls.at(-1),answer());await operation;
  assert.equal(ui.window.AcademicTranscript.current(),null);assert.equal(ui.el("conversation-turns").children.length,0);
  const completed=ask(ui,"현재 이수 기록 비교");reply(ui.calls.at(-1),answer({answer:"현재 기록의 부족분은127학점"}));await completed;
  assert.match(ui.el("answer-text").textContent,/127학점/);
  await ui.el("transcript-clear").emit("click");
  assert.equal(ui.el("answer-text").textContent,"");assert.ok(ui.el("result").hidden);assert.equal(ui.el("semantic-parts"),null);
}
async function testCapturedQuestionAndMultipleRuleCitationIdentity(){
  const ui=setup(),operation=ask(ui,"원래 질문");ui.el("question").value="대기 중 바꾼 질문";
  const refs=[{rule_id:"required",source_id:"curriculum",locator:"PDF262"},{rule_id:"required",source_id:"ta",locator:"PDF1"}];
  reply(ui.calls.at(-1),answer({parts:[{title:"필수과목",status:"supported",evidence_packet:{status:"supported",evidence:refs}}]}));await operation;
  assert.equal(ui.el("current-question").textContent,"원래 질문");assert.equal(ui.el("question").value,"대기 중 바꾼 질문");
  assert.deepEqual(ui.evidenceCalls.map(x=>[x.ref.source_id,x.index]),[["curriculum",0],["ta",1]]);
}
function insideDetails(item){for(let parent=item.parent;parent;parent=parent.parent)if(parent.tagName==="DETAILS")return true;return false;}
async function testCollapsedCourseDetailsAndOnePreviewPerCourse(){
  for(const count of [1,2,4,5]){
    const courses=Array.from({length:count},(_,index)=>({...course,course_name:`확인과목${index+1}`,course_code:`CODE${index+1}`,course_id:`course.${index+1}`,source_id:`source.${index+1}`}));
    const body=courses.map(fact=>`${fact.course_name}는 ${fact.credits}학점이에요.`).join(" ");
    const ui=setup(),operation=ask(ui,"과목별 학점을 알려줘");
    reply(ui.calls.at(-1),answer({answer:body,parts:[{title:"과목 조회",status:"supported",text:body,course_evidence:{status:"supported",courses,evidence:[]}}]}));await operation;
    const rendered=ui.el("semantic-parts"),details=rendered.all().filter(item=>item.tagName==="DETAILS");
    assert.equal(ui.el("answer-text").textContent,body);assert.equal(details.length,1);assert.equal(details[0].open,false,"Full details must be collapsed even for one course");
    assert.match(details[0].children[0].textContent,/과목 전체 상세/);
    assert.ok(!rendered.all().some(item=>item.tagName==="P"&&item.textContent===body),"Do not repeat the writer body in a card");
    for(const fact of courses){assert.ok(details[0].textContent.includes(fact.course_code));assert.ok(details[0].textContent.includes(fact.offering_label));}
    const buttons=rendered.all().filter(item=>item.tagName==="BUTTON");
    assert.equal(buttons.length,count,"Each course has exactly one usable evidence trigger");
    assert.equal(buttons.filter(item=>!insideDetails(item)).length,count<=4?count:0);
    if(count<=4)for(let index=0;index<count;index++)assert.equal(buttons[index].attributes["aria-label"],`${courses[index].course_name} 근거 PDF 보기`);
    assert.deepEqual(ui.evidenceCalls.map(item=>[item.ref.rule_id,item.ref.source_id]),courses.map(fact=>[fact.course_id,fact.source_id]));
    details[0].open=true;assert.ok(details[0].textContent.includes(courses[0].course_code));
  }
  const ui=setup(),operation=ask(ui,"과목 목록");
  reply(ui.calls.at(-1),answer({answer:"원문의 과목 편성을 확인할 수 있어요."}));await operation;
  const rendered=ui.el("semantic-parts"),buttons=rendered.all().filter(item=>item.tagName==="BUTTON");
  assert.equal(buttons.length,1);assert.equal(insideDetails(buttons[0]),true,"Unnamed list evidence stays with collapsed course details");
}
async function testUnsupportedTextAlreadyInBodyAppearsOnce(){
  for(const status of ["insufficient_evidence","conflict","out_of_scope"]){
    const ui=setup(),body="원문을 확인할 수 없어요. 추가 자료가 필요해요.",extra="추가 확인은 담당 부서에 요청해 주세요.",operation=ask(ui,"근거 확인");
    reply(ui.calls.at(-1),answer({status,answer:body,parts:[{title:"확인 안내",status,text:"원문을 확인할 수 없어요.\n추가 자료가 필요해요."},{title:"추가 안내",status,text:extra}]}));await operation;
    assert.equal(ui.el("answer-text").textContent,body);
    assert.equal(ui.el("semantic-parts").children.length,1,"A duplicate-only part must not leave an empty heading");
    assert.equal(ui.el("semantic-parts").textContent,`추가 안내${extra}`);
  }
}
async function testCollapsedRuleReferencesAndCalculationPreserveIndices(){
  const ui=setup(),operation=ask(ui,"졸업 부족 학점");
  const refs=[{rule_id:"required",source_id:"curriculum",locator:"PDF262"},{rule_id:"other",source_id:"policy",locator:"PDF3"},{rule_id:"required",source_id:"ta",locator:"PDF1"}];
  const body="기준은130학점이고, 입력한107학점에서는23학점이 부족해요.";
  reply(ui.calls.at(-1),answer({answer:body,parts:[{title:"졸업학점",status:"supported",text:body,evidence_packet:{status:"supported",evidence:refs},calculations:[{metric:"credits.graduation.total",required:130,earned:107,gap:23}]}]}));await operation;
  const rendered=ui.el("semantic-parts"),details=rendered.all().filter(item=>item.tagName==="DETAILS");
  assert.equal(ui.el("answer-text").textContent,body);assert.equal(details.length,2);
  assert.ok(details.every(item=>item.open===false));assert.match(details[0].children[0].textContent,/규칙 근거 · 3개/);assert.match(details[1].children[0].textContent,/계산 상세/);
  assert.match(details[1].textContent,/기준 130학점 · 입력 107학점 · 부족 23학점/);
  assert.deepEqual(ui.evidenceCalls.map(item=>[item.ref.rule_id,item.ref.source_id,item.index]),[["required","curriculum",0],["other","policy",0],["required","ta",1]]);
  for(const trigger of rendered.all().filter(item=>item.tagName==="BUTTON"))assert.ok(insideDetails(trigger));
}
(async()=>{for(const test of [testSingleSemanticEndpointAndNaturalReply,testCancellationFailureAndReset,testActualOneClickConsumerAndTranscriptRevision,testCapturedQuestionAndMultipleRuleCitationIdentity,testCollapsedCourseDetailsAndOnePreviewPerCourse,testUnsupportedTextAlreadyInBodyAppearsOnce,testCollapsedRuleReferencesAndCalculationPreserveIndices]){await test();console.log(`PASS ${test.name}`);}console.log("PASS: 7 semantic consumer groups, collapsed details, one body, source preview identity, one-click PDF consumer, cancellation, captured question, exact citation indices and volatile context (mock HTTP, not live inference)");})().catch(error=>{console.error(error);process.exitCode=1;});
