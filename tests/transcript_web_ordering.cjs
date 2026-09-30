"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.listeners={}; this.dataset={}; this.value=""; this.checked=false; this._text=""; }
  get textContent() { return this._text + this.children.map(child=>child.textContent).join(""); }
  set textContent(value) { this._text=String(value); this.children=[]; }
  append(...nodes) { for(const node of nodes) { node.parent=this; this.children.push(node); } }
  replaceChildren(...nodes) { this.children=[]; this._text=""; this.append(...nodes); }
  setAttribute() {}
  addEventListener(event,handler) { (this.listeners[event] ??= []).push(handler); }
  emit(event) { return Promise.all((this.listeners[event] ?? []).map(handler=>handler({target:this}))); }
  remove() { this.parent.children=this.parent.children.filter(child=>child!==this); }
  querySelector(selector) {
    const field=/data-field=['"]([^'"]+)['"]/.exec(selector)?.[1];
    const visit=(node)=>node.dataset.field===field ? node : node.children.map(visit).find(Boolean);
    return visit(this);
  }
}

const ids=new Map();
const byId=(id)=>{ if(!ids.has(id)) ids.set(id,new Element("div")); return ids.get(id); };
const questions=[new Element("button"),new Element("button")];
questions[0].dataset.transcriptQuestion="남은필수과목";
questions[1].dataset.transcriptQuestion="몇학점남았어?";
const pending=[];
const evidence={status:"supported",evidence:[{rule_id:"synthetic.policy",locator:"PDF 1",claim:"승인 정책"}],applied_rules:[{rule_id:"synthetic.policy",rule_sha256:"0".repeat(64)}]};
const response={answer:"합성 비교",raw_earned_credits:6,recognized_graduation_credits:6,issues:[],checks:[
  {check_id:"general.balanced.area_coverage",label:"균형교양",result:"not_met",required:4,earned:2,gap:2,missing_courses:[],note:"영역 확인",evidence_packet:evidence,policy_packet:null},
  {check_id:"credits.major.advanced",label:"심화전공",result:"needs_review",required:33,earned:null,gap:null,missing_courses:[],note:"배분 확인",evidence_packet:{status:"insufficient_evidence",evidence:[],applied_rules:[]},policy_packet:evidence},
]};
const context={
  document:{getElementById:byId,createElement:(tag)=>new Element(tag),querySelectorAll:()=>questions},
  window:{addEventListener() {}},
  fetch:async (url)=>{
    if(url.endsWith("/assess")) return {ok:true,json:async()=>response};
    if(url.endsWith("/chat")) return new Promise(resolve=>pending.push(resolve));
    throw new Error("Unexpected synthetic URL");
  },
};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,"../src/academic_assistant/web/transcript.js"),"utf8"),context);

async function assess() {
  await byId("transcript-demo").emit("click");
  byId("transcript-confirmed").checked=true;
  await byId("transcript-confirmed").emit("change");
  byId("transcript-degree").value="single_major";
  await byId("transcript-assess").emit("click");
}
async function run() {
  await assess();
  const text=byId("transcript-results").textContent;
  assert.match(text,/입력상 PASS학점 합계 6/);
  assert.match(text,/4영역 기준 \/ 2영역 인정 \/ 2영역 부족/);
  assert.doesNotMatch(text,/4학점 기준/);
  assert.match(text,/정책 참고 · PDF 근거 보기 \(개인 판정 근거 아님\)/);
  assert.match(text,/승인 정책/);

  const first=questions[0].emit("click");
  const second=questions[1].emit("click");
  assert.equal(pending.length,2);
  pending[1]({ok:true,json:async()=>({answer:"최신 합성 응답"})}); await second;
  pending[0]({ok:true,json:async()=>({answer:"이전 합성 응답"})}); await first;
  assert.equal(byId("transcript-chat").textContent,"최신 합성 응답");

  const staleError=questions[0].emit("click");
  const newer=questions[1].emit("click");
  pending[3]({ok:true,json:async()=>({answer:"최신 성공 응답"})}); await newer;
  pending[2]({ok:false}); await staleError;
  assert.equal(byId("transcript-chat").textContent,"최신 성공 응답");

  const invalidated=questions[0].emit("click");
  await byId("transcript-clear").emit("click");
  pending[4]({ok:true,json:async()=>({answer:"삭제 후 오래된 응답"})}); await invalidated;
  assert.equal(byId("transcript-chat").textContent,"");
  console.log("PASS: latest followup ordering, stale error/invalidation, area units, policy label");
}
run().catch(error=>{ console.error(error); process.exitCode=1; });
