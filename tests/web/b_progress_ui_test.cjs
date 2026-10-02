"use strict";
// Execute the shipped consumers with deferred HTTP/stream fixtures. These are
// UI boundary checks, not observations of live Gemma, Neo4j, or a browser.
const assert=require("node:assert/strict"), fs=require("node:fs"), path=require("node:path"), vm=require("node:vm");
const web=path.join(__dirname,"../../src/academic_assistant/web");
const html=fs.readFileSync(path.join(web,"index.html"),"utf8");
const css=fs.readFileSync(path.join(web,"app.css"),"utf8");
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const encoder=new TextEncoder();

class Element {
  constructor(tag="div") {
    this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.listeners={};this.attributes={};
    this.value="";this._text="";this.hidden=false;this.disabled=false;this.checked=false;this.files=[];this.open=false;
  }
  get textContent(){return this._text+this.children.map(child=>child.textContent).join("");}
  set textContent(text){this.replaceChildren();this._text=String(text);}
  set innerHTML(_){throw Error("Unsafe HTML assignment");}
  insertAdjacentHTML(){throw Error("Unsafe HTML insertion");}
  get firstChild(){return this.children[0]||null;}
  append(...children){for(const child of children){child.remove();child.parent=this;this.children.push(child);}}
  appendChild(child){this.append(child);return child;}
  removeChild(child){child.remove();}
  remove(){if(this.parent){this.parent.children=this.parent.children.filter(child=>child!==this);this.parent=null;}}
  replaceChildren(...children){for(const child of this.children)child.parent=null;this.children=[];this._text="";this.append(...children);}
  setAttribute(key,value){this.attributes[key]=String(value);}
  getAttribute(key){return this.attributes[key]??null;}
  all(){return [this,...this.children.flatMap(child=>child.all())];}
  querySelector(selector){
    if(selector==="button[type='submit']")return this.children.find(child=>child.type==="submit");
    const field=selector.match(/^\[data-field='([^']+)'\]$/)?.[1];assert.ok(field,selector);
    return this.all().find(child=>child.dataset.field===field);
  }
  addEventListener(type,fn,capture=false){(this.listeners[type]??=[]).push({fn,capture:!!capture});}
  async emit(type,extra={}){
    const event={type,target:this,preventDefault(){},stopImmediatePropagation(){this.stopped=true;},...extra};
    for(const item of [...(this.listeners[type]||[])].sort((a,b)=>Number(b.capture)-Number(a.capture))){if(event.stopped)break;await item.fn(event);}
  }
  dispatchEvent(event){return this.emit(event.type);}
  requestSubmit(){this.pendingSubmit=this.emit("submit");}
  focus(){this.focused=true;} scrollIntoView(){} setCustomValidity(){} reportValidity(){return true;}
}

function setup(){
  const root=new Element("body"), elements=new Map();
  for(const match of html.matchAll(/<([a-z][a-z0-9]*)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){
    const item=new Element(match[1]);item.id=match[3];item.hidden=/\bhidden\b/.test(match[2]);
    for(const data of match[2].matchAll(/data-([a-z-]+)="([^"]+)"/g))item.dataset[data[1].replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase())]=data[2];
    for(const attribute of match[2].matchAll(/(aria-[a-z-]+|role)="([^"]+)"/g))item.setAttribute(attribute[1],attribute[2]);
    elements.set(item.id,item);root.append(item);
  }
  // Preserve the shipped trace nesting, so moving a live control into the
  // collapsed region cannot pass as a flat collection of independent IDs.
  const traceMarkup=html.match(/<details\b([^>]*\bid="assistant-trace"[^>]*)>([\s\S]*?)<\/details>/);
  if(traceMarkup){
    const trace=elements.get("assistant-trace");trace.open=/\bopen\b/.test(traceMarkup[1]);
    const summary=traceMarkup[2].match(/<summary\b[^>]*>([^<]*)<\/summary>/);
    if(summary){const item=new Element("summary");item.textContent=summary[1];trace.append(item);}
    for(const match of traceMarkup[2].matchAll(/\bid="([^"]+)"/g))trace.append(elements.get(match[1]));
  }
  const credits=[...html.matchAll(/data-credit-metric="([^"]+)"/g)].map(match=>{const item=new Element("input");item.dataset.creditMetric=match[1];return item;});
  const examples=[...html.matchAll(/data-transcript-example="([^"]+)"/g)].map(match=>{const item=new Element("button");item.dataset.transcriptExample=match[1];return item;});
  const followups=[...html.matchAll(/data-transcript-question="([^"]+)"/g)].map(match=>{const item=new Element("button");item.dataset.transcriptQuestion=match[1];return item;});
  const questions=[...html.matchAll(/data-question="([^"]+)"/g)].map(match=>{const item=new Element("button");item.dataset.question=match[1];return item;});
  const el=id=>elements.get(id)||root.all().find(item=>item.id===id)||null;
  const submit=new Element("button");submit.type="submit";el("question-form").append(submit);
  const calls=[], evidenceCalls=[], timers=new Map(), window=new Element("window");
  let elapsed=0, timerId=0, evidenceCleared=0;
  window.location={hostname:"demo.invalid"};
  window.AcademicEvidence={button(ref,index){evidenceCalls.push({ref,index});return new Element("button");},clear(){evidenceCleared++;}};
  const document={getElementById:el,createElement:tag=>new Element(tag),createTextNode:text=>{const item=new Element();item.textContent=text;return item;},querySelectorAll:selector=>({"[data-credit-metric]":credits,"[data-transcript-example]":examples,"[data-transcript-question]":followups,"[data-question]":questions})[selector]||[]};
  const context={document,window,location:window.location,AbortController,Event,TextDecoder,performance:{now:()=>elapsed},
    setInterval(fn){const id=++timerId;timers.set(id,fn);return id;},clearInterval(id){timers.delete(id);},
    fetch(url,options){
      if(url==="/v1/academic/runtime")return Promise.resolve({ok:true,json:async()=>({})});
      return new Promise((resolve,reject)=>calls.push({url,options,resolve,reject}));
    }};
  for(const target of [context,window])for(const key of ["localStorage","sessionStorage","indexedDB"])Object.defineProperty(target,key,{get(){throw Error(`Persistent storage accessed: ${key}`);}});
  vm.createContext(context);
  for(const script of ["app.js","transcript.js","semantic-ui.js"])vm.runInContext(fs.readFileSync(path.join(web,script),"utf8"),context,{filename:script});
  return {el,calls,evidenceCalls,credits,window,submit,timers,evidenceCleared:()=>evidenceCleared,
    advance(ms){elapsed+=ms;for(const fn of [...timers.values()])fn();}};
}
function ask(ui,question="컴퓨터구조는 몇 학점이야?"){ui.el("question").value=question;return ui.el("question-form").emit("submit");}
const course={course_name:"컴퓨터구조",course_code:"CDA0016",course_id:"course.cda0016",source_id:"source",credits:3,category:"major_required",offering_label:"3학년 1학기"};
function answer(updates={}){return {packet_id:"assistant-fixture",status:"supported",kind:"academic",answer:"컴퓨터구조는 3학점 전공필수 과목이에요.",plan_status:"generated",generation_status:"generated",context_question:"컴퓨터구조 학점",parts:[{status:"supported",title:"과목 조회",text:"조회 사실",course_evidence:{status:"supported",courses:[course],evidence:[]}}],...updates};}
function progress(sequence,updates={}){return {schema_version:"1.0.0",type:"progress",request_id:"request-fixture",sequence,stage:"received",state:"started",elapsed_ms:sequence*10,duration_ms:null,message:"질문을 접수했습니다.",explanation_source:"system",details:{},...updates};}
const result=(response=answer(),elapsed_ms=1234)=>({type:"result",response,elapsed_ms});
function jsonReply(call,response=answer(),status=200){call.resolve({ok:status===200,status,headers:{get:()=>"application/json"},json:async()=>response});}
function streamReply(call){
  const queue=[], pending=[];
  let canceled=0, released=0;
  const dispatch=item=>{const waiting=pending.shift();if(waiting){if(item.error)waiting.reject(item.error);else waiting.resolve(item);}else queue.push(item);};
  const reader={read(){const item=queue.shift();if(item)return item.error?Promise.reject(item.error):Promise.resolve(item);return new Promise((resolve,reject)=>pending.push({resolve,reject}));},
    // Deliberately permit late chunks after cancel to test the consumer's guards.
    cancel(){canceled++;return Promise.resolve();},releaseLock(){released++;}};
  call.resolve({ok:true,status:200,headers:{get:()=>"application/x-ndjson; charset=utf-8"},body:{getReader:()=>reader},json(){throw Error("A stream must not call response.json");}});
  return {bytes(value){dispatch({done:false,value});},text(value){dispatch({done:false,value:encoder.encode(value)});},row(value){this.text(`${JSON.stringify(value)}\n`);},end(){dispatch({done:true});},fail(error){dispatch({error});},canceled:()=>canceled,released:()=>released};
}

async function testObservedStagesAndClockHaveSeparateSources(){
  const ui=setup();await tick();const question="홍길동 202612345입니다. 컴퓨터구조는 몇 학점이야?", operation=ask(ui,question), call=ui.calls[0];
  assert.equal(call.url,"/v1/academic/assistant");assert.equal(call.options.headers.Accept,"application/x-ndjson");
  assert.equal(call.options.cache,"no-store");assert.equal(call.options.credentials,"omit");assert.equal(JSON.parse(call.options.body).question,question);
  assert.equal(ui.el("answer-panel").getAttribute("aria-busy"),"true");assert.equal(ui.el("loading-state").hidden,false);
  assert.equal(ui.el("cancel-question").hidden,false);assert.equal(ui.el("assistant-stages").children.length,0);
  ui.advance(1250);assert.match(ui.el("assistant-elapsed").textContent,/1\.250초 · 1250ms/);
  assert.equal(ui.el("assistant-stages").children.length,0,"A clock tick cannot invent an intent or query stage");
  const stream=streamReply(call);stream.row(progress(1));await tick();
  const first=ui.el("assistant-stages").textContent;ui.advance(1000);assert.equal(ui.el("assistant-stages").textContent,first);
  const injection='<img src=x onerror="globalThis.compromised=true">';
  stream.row(progress(2,{stage:"intent",state:"completed",elapsed_ms:432.125,duration_ms:421.5,message:"필요한 과목 학점을 확인할게요.",explanation_source:"llm",details:{plan:{tasks:[{course_name:"컴퓨터구조",requested_fields:["credits"]}]}}}));
  stream.row(progress(3,{stage:"query",state:"completed",elapsed_ms:500,duration_ms:12.25,message:"교육과정에서 해당 과목을 찾았습니다.",details:{cypher:`MATCH (c:Course)\nRETURN c`,parameters:{course_name:injection},backend:"neo4j",result_count:1,result_summary:"컴퓨터구조 · 3학점"}}));await tick();
  const stages=ui.el("assistant-stages"), details=stages.all().filter(item=>item.tagName==="DETAILS");
  assert.equal(details.length,3);assert.ok(details.every(item=>item.open===false&&item.children[0].tagName==="SUMMARY"));
  assert.match(stages.textContent,/서버 경과 0\.432초 · 432\.125ms/);assert.match(stages.textContent,/단계 소요 0\.421초 · 421\.5ms/);
  assert.match(stages.textContent,/시스템 설명/);assert.match(stages.textContent,/모델 설명/);
  assert.match(stages.textContent,/조회 계획/);assert.match(stages.textContent,/실행한 Cypher/);assert.match(stages.textContent,/조회 매개변수/);assert.match(stages.textContent,/조회 결과 개수/);assert.match(stages.textContent,/조회 결과 요약/);
  assert.ok(stages.textContent.includes(JSON.stringify(injection)));assert.equal(stages.all().filter(item=>item.tagName==="IMG").length,0);
  ui.el("question").value="대기 중 새로 쓴 질문";
  stream.text(`${JSON.stringify(progress(4,{stage:"complete",state:"completed",elapsed_ms:1345.125,duration_ms:1345.125,message:"답변을 확인했습니다."}))}\n${JSON.stringify(result(answer(),1345.125))}\n`);await operation;
  assert.equal(ui.el("current-question").textContent,question);assert.equal(ui.el("question").value,"대기 중 새로 쓴 질문");
  assert.equal(ui.el("answer-text").textContent,answer().answer);assert.equal(ui.el("assistant-stages").children.length,4);
  assert.match(ui.el("assistant-elapsed").textContent,/서버 처리 1\.345초 · 1345\.125ms/);
  const finished=ui.el("assistant-elapsed").textContent;ui.advance(20000);assert.equal(ui.el("assistant-elapsed").textContent,finished);
  assert.equal(ui.timers.size,0);assert.equal(ui.el("answer-panel").getAttribute("aria-busy"),"false");assert.equal(ui.submit.disabled,false);
  assert.equal(ui.calls.length,1);assert.equal(ui.evidenceCalls.length,1);assert.equal(stream.canceled(),1);
}

async function testUTF8ChunkAndLineFramingIncludingFinalWithoutNewline(){
  const ui=setup(), operation=ask(ui), stream=streamReply(ui.calls[0]);await tick();
  const rows=`${JSON.stringify(progress(1,{message:"한글 질문을 받았습니다."}))}\r\n\r\n${JSON.stringify(progress(2,{stage:"retrieval",state:"completed",duration_ms:7,message:"근거를 찾았습니다.",details:{backend:"registry",result_count:1}}))}\n${JSON.stringify(result())}`;
  const bytes=encoder.encode(rows), hangul=bytes.findIndex(byte=>byte>=0xe0);
  stream.bytes(bytes.slice(0,hangul+1));await tick();assert.equal(ui.el("assistant-stages").children.length,0);
  stream.bytes(bytes.slice(hangul+1,hangul+2));await tick();assert.equal(ui.el("assistant-stages").children.length,0);
  for(let index=hangul+2;index<bytes.length;index+=11)stream.bytes(bytes.slice(index,index+11));
  await tick();assert.equal(ui.el("assistant-stages").children.length,2);assert.equal(ui.el("result").hidden,true);
  stream.end();await operation;
  assert.equal(ui.el("answer-text").textContent,answer().answer);assert.match(ui.el("assistant-stages").textContent,/한글 질문을 받았습니다/);
  assert.match(ui.el("assistant-stages").textContent,/registry/);assert.doesNotMatch(ui.el("assistant-stages").textContent,/실행한 Cypher/);
  assert.equal(ui.calls.length,1);assert.equal(ui.timers.size,0);
}

async function testIncompleteMalformedAndBrokenStreamsCannotBecomeAnswersOrRetry(){
  const failures=["incomplete","malformed","wrong-request","duplicate","unknown-stage","bad-time","invalid-utf8","network","oversized"];
  for(const kind of failures){
    const ui=setup(), operation=ask(ui), stream=streamReply(ui.calls[0]);stream.row(progress(1));await tick();
    if(kind==="incomplete")stream.end();
    if(kind==="malformed"){stream.text('{"type":');stream.end();}
    if(kind==="wrong-request")stream.row(progress(2,{request_id:"other-request"}));
    if(kind==="duplicate")stream.row(progress(1));
    if(kind==="unknown-stage")stream.row(progress(2,{stage:"made_up_stage"}));
    if(kind==="bad-time")stream.row(result(answer(),-1));
    if(kind==="invalid-utf8")stream.bytes(new Uint8Array([0xff]));
    if(kind==="network")stream.fail(new Error("C:/private/student.pdf ssh_password=secret"));
    if(kind==="oversized")stream.text("x".repeat(1048577));
    await operation;
    assert.equal(ui.calls.length,1,`${kind}: stream failure must not make a hidden JSON request`);
    assert.equal(ui.el("conversation-turns").children.length,0);assert.equal(ui.el("semantic-parts"),null);
    assert.equal(ui.el("assistant-stages").children.length,1);assert.match(ui.el("status-badge").className,/status-error/);
    assert.match(ui.el("generation-note").textContent,/학사 근거가 없다는 뜻은 아닙니다/);
    assert.doesNotMatch(ui.el("answer-text").textContent,/private|student\.pdf|ssh_password|secret/);
    assert.equal(ui.el("answer-panel").getAttribute("aria-busy"),"false");assert.equal(ui.submit.disabled,false);assert.equal(ui.timers.size,0);
    const next=ask(ui,"다시 확인해 주세요");assert.equal(JSON.parse(ui.calls[1].options.body).previous_question,null);
    jsonReply(ui.calls[1]);await next;
  }
}

async function testAcceptedServerErrorAndFailedWritingPreserveEvidenceTruthfully(){
  const ui=setup(), operation=ask(ui), stream=streamReply(ui.calls[0]);
  stream.row(progress(1));stream.row(progress(2,{stage:"error",state:"failed",elapsed_ms:300,duration_ms:290,message:"답변 연결을 확인하지 못했습니다.",details:{failure_code:"processing_unavailable"}}));
  stream.row({type:"error",message:"처리를 마치지 못했습니다. 다시 질문해 주세요.",elapsed_ms:305});await operation;
  assert.equal(ui.el("answer-text").textContent,"처리를 마치지 못했습니다. 다시 질문해 주세요.");
  assert.match(ui.el("assistant-elapsed").textContent,/서버 처리 0\.305초 · 305ms/);assert.equal(ui.el("assistant-stages").children.length,2);
  assert.equal(ui.el("conversation-turns").children.length,0);assert.equal(ui.calls.length,1);
  const next=ask(ui), second=streamReply(ui.calls[1]);
  const failed=answer({status:"insufficient_evidence",generation_status:"fallback",reason_code:"processing_unavailable",answer:"조회 사실은 확인했지만 안내 문장을 완성하지 못했습니다."});
  second.row(progress(1,{stage:"repair",state:"failed",details:{attempt:2,failure_code:"writer_rejected"}}));second.row(result(failed,450));await next;
  assert.equal(ui.el("answer-text").textContent,failed.answer);assert.equal(ui.el("status-badge").textContent,"질문 처리 실패");
  assert.match(ui.el("status-badge").className,/status-error/);assert.match(ui.el("generation-note").textContent,/질문 처리 실패/);
  assert.ok(ui.el("semantic-parts").textContent.includes("CDA0016"));assert.equal(ui.evidenceCalls.length,1,"Approved source button remains available after writing failure");
  assert.equal(ui.calls.length,2);assert.equal(ui.timers.size,0);
}

async function testCancelResetAndLateChunksCannotRestoreOldSession(){
  for(const action of ["cancel","new-conversation","credits","transcript","pagehide"]){
    const ui=setup(), old=ask(ui,"예전 질문"), oldCall=ui.calls[0], stream=streamReply(oldCall);
    stream.row(progress(1));await tick();ui.advance(350);ui.el("assistant-trace").open=true;
    if(action==="cancel")await ui.el("cancel-question").emit("click");
    if(action==="new-conversation")await ui.el("new-conversation").emit("click");
    if(action==="credits"){ui.credits[0].value="10";await ui.credits[0].emit("input");}
    if(action==="transcript")await ui.window.emit("academic-transcript-change");
    if(action==="pagehide")await ui.window.emit("pagehide");
    assert.equal(oldCall.options.signal.aborted,true,action);assert.equal(ui.el("assistant-stages").children.length,0,action);
    assert.equal(ui.el("assistant-trace").open,false,`${action}: expansion belongs to the cleared request`);
    assert.equal(ui.el("assistant-progress").hidden,true);assert.equal(ui.timers.size,0);
    const next=ask(ui,"현재 질문");assert.equal(JSON.parse(ui.calls[1].options.body).previous_question,null);
    stream.text(`${JSON.stringify(progress(2,{stage:"complete",state:"completed",message:"늦은 완료"}))}\n${JSON.stringify(result(answer({answer:"늦은 답변"})))}\n`);await old;
    assert.equal(ui.el("assistant-stages").children.length,0);assert.equal(ui.el("answer-panel").getAttribute("aria-busy"),"true");
    assert.equal(ui.submit.disabled,true);assert.doesNotMatch(ui.el("answer-text").textContent,/늦은 답변/);
    const second=streamReply(ui.calls[1]);second.row(progress(1,{request_id:"new-request",message:"현재 요청만 표시"}));second.row(result(answer({answer:"현재 답변"})));await next;
    assert.equal(ui.el("answer-text").textContent,"현재 답변");assert.equal(ui.el("assistant-stages").children.length,1);
    assert.match(ui.el("assistant-stages").textContent,/현재 요청만 표시/);assert.doesNotMatch(ui.el("assistant-stages").textContent,/늦은 완료/);
    assert.equal(ui.el("conversation-turns").children.length,1);assert.equal(ui.timers.size,0);assert.ok(stream.canceled()>=1);
  }
}

async function testCollapsedTraceRetainsSixteenEventsAndCurrentProgress(){
  const ui=setup(), trace=ui.el("assistant-trace"), stages=ui.el("assistant-stages");
  assert.equal(trace.tagName,"DETAILS");assert.equal(trace.open,false);
  assert.equal(trace.children[0].tagName,"SUMMARY");assert.equal(trace.children[0].textContent,"단계별 조회 상세");
  assert.equal(stages.parent,trace);assert.match(html,/<ol\b[^>]*id="assistant-stages"[^>]*tabindex="0"[^>]*aria-label=/);
  const style=css.match(/\.assistant-stages\s*\{([^}]+)\}/)?.[1]||"";
  const height=Number(style.match(/max-height:\s*([\d.]+)rem/)?.[1]);
  assert.ok(height>0&&height<=24,"Expanded observed-event log must have a bounded height");
  assert.match(style,/overflow-y:\s*auto/);
  const operation=ask(ui), stream=streamReply(ui.calls[0]);
  const observed=[["received","started"],["intent","started"],["intent","completed"],["query","started"],
    ["query","completed"],["retrieval","started"],["retrieval","completed"],["writing","started"],
    ["writing","completed"],["verification","started"],["verification","failed"],["repair","started"],
    ["repair","completed"],["verification","started"],["verification","completed"],["complete","completed"]];
  const query={cypher:"MATCH (c:Course) RETURN c",parameters:{course_name:"컴퓨터구조"},backend:"neo4j",result_count:1,result_summary:"컴퓨터구조 · 3학점"};
  for(let index=0;index<observed.length;index++){
    const [stage,state]=observed[index], message=`관찰한 이벤트 ${index+1}`;
    stream.row(progress(index+1,{stage,state,message,duration_ms:2.5,details:index===4?query:{}}));await tick();
    assert.equal(stages.children.length,index+1);assert.equal(trace.open,false,"Progress must not expand the full trace");
    assert.equal(ui.el("loading-message").textContent,message);assert.ok(ui.el("assistant-progress-note").textContent.includes(message));
    assert.equal(ui.el("assistant-progress").hidden,false);assert.equal(ui.el("loading-state").hidden,false);assert.equal(ui.el("cancel-question").hidden,false);
    for(const id of ["loading-state","loading-message","assistant-progress-title","assistant-progress-note","assistant-elapsed","cancel-question"])
      assert.ok(!trace.all().includes(ui.el(id)),`${id} must remain outside the collapsed trace`);
  }
  ui.advance(1234);assert.match(ui.el("assistant-elapsed").textContent,/전체 대기 1\.234초 · 1234ms/);
  stream.row(result());await operation;
  assert.equal(ui.el("result").hidden,false);assert.equal(ui.el("answer-text").textContent,answer().answer);
  assert.equal(trace.open,false);assert.equal(stages.children.length,16);
  const summaries=stages.children.map(item=>item.children[0].children[0]);
  for(let index=0;index<summaries.length;index++)assert.equal(summaries[index].children[1].textContent,`관찰한 이벤트 ${index+1}`);
  assert.match(summaries.at(-1).textContent,/처리 완료 · 완료/);
  const retained=stages.textContent;trace.open=true;stages.children[4].children[0].open=true;
  assert.equal(stages.textContent,retained,"Opening either detail must retain all observed records");
  for(const value of [query.cypher,JSON.stringify(query.parameters,null,2),query.backend,query.result_summary])assert.ok(stages.textContent.includes(value));
  assert.match(stages.textContent,/조회 결과 개수1/);assert.equal(ui.calls.length,1);assert.equal(ui.timers.size,0);
  await ui.el("new-conversation").emit("click");assert.equal(trace.open,false);assert.equal(stages.children.length,0);
}

async function testJSONCompatibilityAndLateJSONBodyStillRespectReset(){
  for(const withHeaders of [true,false]){
    const ui=setup(), operation=ask(ui);ui.advance(210);
    const response={ok:true,status:200,json:async()=>answer()};if(withHeaders)response.headers={get:()=>"application/json"};
    ui.calls[0].resolve(response);await operation;
    assert.equal(ui.el("answer-text").textContent,answer().answer);assert.equal(ui.el("assistant-stages").children.length,0);
    assert.match(ui.el("assistant-progress-note").textContent,/JSON 응답/);assert.match(ui.el("assistant-elapsed").textContent,/전체 대기 0\.210초 · 210ms/);
    assert.equal(ui.calls.length,1);assert.equal(ui.timers.size,0);
  }
  const ui=setup(), old=ask(ui);let resolveJSON;
  ui.calls[0].resolve({ok:true,status:200,json:()=>new Promise(resolve=>{resolveJSON=resolve;})});await tick();
  await ui.el("new-conversation").emit("click");resolveJSON(answer());await old;
  assert.equal(ui.el("conversation-turns").children.length,0);assert.equal(ui.el("answer-text").textContent,"");assert.equal(ui.el("assistant-progress").hidden,true);
  const invalid=ask(ui,"홍길동 202612345입니다. 질문을 다시 확인해 주세요.");jsonReply(ui.calls[1],{},422);await invalid;
  assert.match(ui.el("answer-text").textContent,/질문 길이와 학점 입력 범위/);assert.doesNotMatch(ui.el("answer-text").textContent,/이름·학번.*넣지/);assert.equal(ui.calls.length,2);
}

async function testPrototypeDisclosureAccessibleControlsAndVolatileSessions(){
  assert.match(html,/id="question-help"[^>]*>[^<]*이름·학번이 포함된 질문도 모델에서 처리/);
  assert.match(html,/class="answer-skeleton" aria-hidden="true"/);assert.match(css,/@media \(prefers-reduced-motion: reduce\)[^}]*animation: none !important/);
  assert.match(css,/@media \(max-width: 760px\)/);assert.match(css,/\.stage-details pre[^}]*white-space: pre-wrap/);
  const first=setup(), second=setup();await tick();
  assert.equal(first.el("loading-state").getAttribute("role"),"status");assert.equal(first.el("assistant-elapsed").getAttribute("aria-live"),"off");
  assert.equal(first.el("cancel-question").tagName,"BUTTON");assert.match(first.el("feedback-privacy-note").textContent,/이름·학번이 포함된 질문도 연구실 Gemma/);
  assert.match(first.el("feedback-privacy-note").textContent,/자동 저장하지 않습니다/);assert.match(first.el("transcript-privacy-note").textContent,/LLM에 보내지/);
  for(let index=0;index<7;index++){const operation=ask(first,`질문${index}`);jsonReply(first.calls.at(-1));await operation;}
  assert.equal(first.el("conversation-turns").children.length,6);assert.equal(second.el("conversation-turns").children.length,0);
  first.credits[0].value="107";await first.el("new-conversation").emit("click");
  assert.equal(first.el("conversation-turns").children.length,0);assert.equal(first.credits[0].value,"");assert.equal(first.el("question").value,"");
  assert.equal(first.el("assistant-stages").children.length,0);assert.equal(first.el("assistant-progress").hidden,true);assert.ok(first.evidenceCleared()>0);
  assert.equal(first.timers.size,0);
}

(async()=>{
  const checks=[testObservedStagesAndClockHaveSeparateSources,testUTF8ChunkAndLineFramingIncludingFinalWithoutNewline,
    testIncompleteMalformedAndBrokenStreamsCannotBecomeAnswersOrRetry,testAcceptedServerErrorAndFailedWritingPreserveEvidenceTruthfully,
    testCancelResetAndLateChunksCannotRestoreOldSession,testJSONCompatibilityAndLateJSONBodyStillRespectReset,
    testPrototypeDisclosureAccessibleControlsAndVolatileSessions,testCollapsedTraceRetainsSixteenEventsAndCurrentProgress];
  for(const check of checks){await check();console.log(`PASS ${check.name}`);}
  console.log(`PASS ${checks.length} B progress consumer groups (mock streams; no live model/server/browser calls)`);
})().catch(error=>{console.error(error);process.exitCode=1;});
