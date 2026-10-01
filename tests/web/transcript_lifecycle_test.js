"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const webRoot = path.join(__dirname, "../../src/academic_assistant/web");
const source = fs.readFileSync(path.join(webRoot, "transcript.js"), "utf8");
const html = fs.readFileSync(path.join(webRoot, "index.html"), "utf8");

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.files = [];
    this._text = "";
  }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set textContent(value) { this.replaceChildren(); this._text = String(value); }
  set innerHTML(_value) { throw new Error("Unsafe HTML assignment"); }
  append(...nodes) {
    for (const node of nodes) {
      node.remove(); node.parentNode = this; this.children.push(node);
    }
  }
  replaceChildren(...nodes) {
    this.children.forEach(child => { child.parentNode = null; });
    this.children = []; this._text = ""; this.append(...nodes);
  }
  remove() {
    if (this.parentNode) this.parentNode.children = this.parentNode.children.filter(child => child !== this);
    this.parentNode = null;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, handler) { (this.listeners[name] ??= []).push(handler); }
  emit(name, properties = {}) {
    const event = {target: this, preventDefault() {}, ...properties};
    return Promise.all((this.listeners[name] ?? []).map(handler => handler(event)));
  }
  all() { return [this, ...this.children.flatMap(child => child.all())]; }
  querySelector(selector) {
    const field = selector.match(/^\[data-field=['"]([^'"]+)['"]\]$/)?.[1];
    assert.ok(field, `Unexpected fixture selector ${selector}`);
    return this.all().find(node => node.dataset.field === field) || null;
  }
  focus() { this.focused = true; }
  scrollIntoView() {}
}

function setup({hostname = "127.0.0.1", rejectOnAbort = false} = {}) {
  const elements = new Map();
  const root = new Element("body");
  for (const match of html.matchAll(/<([a-z]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const node = new Element(match[1]); node.id = match[3];
    node.hidden = /\bhidden\b/.test(match[2]); node.disabled = /\bdisabled\b/.test(match[2]);
    elements.set(node.id, node); root.append(node);
  }
  for (const match of html.matchAll(/<button\b[^>]*data-transcript-question="([^"]+)"[^>]*>([^<]+)<\/button>/g)) {
    const node = new Element("button"); node.dataset.transcriptQuestion = match[1]; node.textContent = match[2];
    elements.get("transcript-followups").append(node);
  }
  const document = {
    body: root, createElement: tag => new Element(tag), getElementById: id => {
      assert.ok(elements.has(id), `Missing real HTML element #${id}`); return elements.get(id);
    },
    querySelectorAll: selector => {
      if (selector === "[data-transcript-example]") return [];
      assert.equal(selector, "[data-transcript-question]");
      return root.all().filter(node => node.dataset.transcriptQuestion);
    },
  };
  const window = new Element("window"); window.location = hostname === null ? undefined : {hostname};
  const pending = [];
  const context = {document, window, AbortController,
    fetch: (url, options) => new Promise((resolve, reject) => {
      const request = {url, options, resolve, reject, abortCount: 0}; pending.push(request);
      options.signal.addEventListener("abort", () => {
        request.abortCount += 1;
        if (rejectOnAbort) { const error = new Error("aborted"); error.name = "AbortError"; reject(error); }
      });
    }),
  };
  for (const target of [context, window]) {
    for (const name of ["localStorage", "sessionStorage", "indexedDB"]) {
      Object.defineProperty(target, name, {get() { throw new Error(`Persistent storage accessed: ${name}`); }});
    }
  }
  vm.runInNewContext(source, context);
  return {pending, window, root, node: id => document.getElementById(id)};
}

function reply(request, data, status = 200) {
  request.resolve({ok: status >= 200 && status < 300, status, json: async () => data});
}
const tick = () => new Promise(resolve => setImmediate(resolve));
const course = (name = "예제 과목") => ({course_name: name, course_code: "DEMO0001", credits: 3, grade: "A0", category: "major_required"});
const extraction = (name = "예제 과목") => ({courses: [course(name)], detected_admission_year: 2026, issues: []});
const assessment = (answer = "최신 비교") => ({answer, raw_earned_credits: 3, recognized_graduation_credits: 3,
  checks: [{check_id: "credits.graduation.total", label: "졸업 총학점", result: "needs_review", required: 130,
    earned: 3, gap: 127, missing_courses: [], note: "확인 필요", evidence_packet: {status: "supported", evidence: [], applied_rules: []}}],
  issues: [], verification_items: [],
});

function extract(ui, name = "sample.pdf") {
  ui.node("transcript-file").files = [{name, size: 1024}];
  return ui.node("transcript-extract").emit("click");
}
async function prepare(ui) {
  await ui.node("transcript-demo").emit("click");
  ui.node("transcript-confirmed").checked = true;
  await ui.node("transcript-confirmed").emit("change");
}
async function assessed(ui) {
  await prepare(ui);
  const operation = ui.node("transcript-assess").emit("click");
  reply(ui.pending.at(-1), assessment()); await operation;
}
function ask(ui, index = 0) {
  return ui.root.all().filter(node => node.dataset.transcriptQuestion)[index].emit("click");
}

async function testClearActuallyAbortsExtractionAndDropsStudentRows() {
  const ui = setup({rejectOnAbort: true});
  const operation = extract(ui);
  await ui.node("transcript-clear").emit("click"); await operation;
  assert.equal(ui.pending[0].options.signal.aborted, true);
  assert.equal(ui.pending[0].abortCount, 1);
  assert.equal(ui.node("transcript-rows").children.length, 0);
  assert.equal(ui.node("transcript-review").hidden, true);
  assert.equal(ui.node("transcript-extract").disabled, false);
  assert.match(ui.node("transcript-message").textContent, /메모리에서 지웠습니다/);
  for (const request of ui.pending) {
    assert.equal(request.options.cache, "no-store"); assert.equal(request.options.credentials, "omit");
  }
}

async function testReplacedPDFIgnoresLateResponseAndOldFinally() {
  const ui = setup();
  const old = extract(ui, "old.pdf");
  await ui.node("transcript-file").emit("change");
  const current = extract(ui, "new.pdf");
  assert.equal(ui.pending[0].options.signal.aborted, true);
  reply(ui.pending[0], extraction("오래된 학생 과목")); await old;
  assert.equal(ui.node("transcript-extract").disabled, true, "Old finally must not unlock current extraction");
  assert.equal(ui.node("transcript-rows").children.length, 0);
  reply(ui.pending[1], extraction("새 과목")); await current;
  assert.equal(ui.node("transcript-rows").children[0].querySelector("[data-field='course_name']").value, "새 과목");
  assert.equal(ui.node("transcript-extract").disabled, false);
}

async function testManualReplacementAndPageChangeAbortExtraction() {
  for (const control of ["transcript-manual", "transcript-demo", "transcript-page"]) {
    const ui = setup(); const old = extract(ui);
    await ui.node(control).emit(control === "transcript-page" ? "input" : "click");
    assert.equal(ui.pending[0].options.signal.aborted, true, control);
    const rowCount = ui.node("transcript-rows").children.length;
    ui.pending[0].reject(new Error("C:/private/student.pdf")); await old;
    assert.equal(ui.node("transcript-rows").children.length, rowCount);
    assert.doesNotMatch(ui.node("transcript-message").textContent, /private|student\.pdf/);
    assert.equal(ui.node("transcript-extract").disabled, false);
  }
}

async function testClearAssessmentAndLateJSONCannotRestoreResult() {
  const ui = setup(); await prepare(ui);
  let resolveJSON;
  const old = ui.node("transcript-assess").emit("click");
  ui.pending[0].resolve({ok: true, json: () => new Promise(resolve => {resolveJSON = resolve;})});
  await tick(); await ui.node("transcript-clear").emit("click");
  assert.equal(ui.pending[0].options.signal.aborted, true);
  resolveJSON(assessment("오래된 학생 결과")); await old;
  assert.equal(ui.node("transcript-results").hidden, true);
  assert.equal(ui.node("transcript-results").textContent, "");
  assert.equal(ui.node("transcript-followups").hidden, true);
  assert.equal(ui.node("transcript-assess").disabled, true);
}

async function testOldAssessmentFinallyCannotEnableNewAssessment() {
  const ui = setup(); await prepare(ui);
  const old = ui.node("transcript-assess").emit("click");
  await ui.node("transcript-rows").emit("input");
  assert.equal(ui.pending[0].options.signal.aborted, true);
  ui.node("transcript-confirmed").checked = true; await ui.node("transcript-confirmed").emit("change");
  const current = ui.node("transcript-assess").emit("click");
  reply(ui.pending[0], assessment("오래된 비교")); await old;
  assert.equal(ui.node("transcript-assess").disabled, true, "Old finally must preserve the new busy generation");
  assert.equal(ui.node("transcript-results").hidden, true);
  reply(ui.pending[1], assessment("새 비교")); await current;
  assert.match(ui.node("transcript-results").textContent, /새 비교/);
  assert.doesNotMatch(ui.node("transcript-results").textContent, /오래된 비교/);
  assert.equal(ui.node("transcript-assess").disabled, false);
}

async function testAssessmentIsCancelledByEveryTranscriptEdit() {
  for (const [id, event] of [["transcript-rows", "change"], ["transcript-confirmed", "change"],
    ["transcript-complete", "change"], ["transcript-degree", "change"], ["transcript-add", "click"],
    ["transcript-manual", "click"], ["transcript-demo", "click"]]) {
    const ui = setup({rejectOnAbort: true}); await prepare(ui);
    const old = ui.node("transcript-assess").emit("click");
    await ui.node(id).emit(event); await old;
    assert.equal(ui.pending[0].abortCount, 1, `${id} must cancel assessment`);
    assert.equal(ui.node("transcript-results").hidden, true);
  }
}

async function testOverlappingFollowupsCancelAndKeepNewestAnswer() {
  const ui = setup(); await assessed(ui);
  const old = ask(ui, 0); const current = ask(ui, 1);
  assert.equal(ui.pending[1].options.signal.aborted, true);
  assert.equal(ui.pending[2].options.signal.aborted, false);
  assert.equal(ui.pending[2].options.cache, "no-store");
  reply(ui.pending[2], {answer: "새 답변", focus_check_ids: [], verification_items: []}); await current;
  ui.pending[1].reject(new Error("C:/private/student.json")); await old;
  assert.equal(ui.node("transcript-chat").textContent, "새 답변");
  assert.equal(ui.pending[2].options.signal.aborted, false, "Old finally must not abort the newer followup");
}

async function testNewGenerationAndPagehideCancelFollowupAndReleaseData() {
  for (const action of ["clear", "manual", "pagehide"]) {
    const ui = setup(); await assessed(ui);
    const old = ask(ui);
    const questionInput = ui.node("transcript-followups").all().find(node => node.tagName === "INPUT");
    questionInput.value = "학생의 질문";
    if (action === "pagehide") await ui.window.emit("pagehide");
    else await ui.node(`transcript-${action}`).emit("click");
    assert.equal(ui.pending[1].options.signal.aborted, true, action);
    reply(ui.pending[1], {answer: "오래된 학생 답변", focus_check_ids: [], verification_items: []}); await old;
    assert.equal(ui.node("transcript-chat").textContent, "");
    assert.equal(ui.node("transcript-results").textContent, "");
    assert.equal(questionInput.value, "");
  }
  const ui = setup({rejectOnAbort: true}); const operation = extract(ui);
  await ui.window.emit("pagehide"); await operation;
  assert.equal(ui.pending[0].abortCount, 1);
}

async function testTabsRemainIndependentAndPublicCopyNamesHostingPC() {
  const publicUI = setup({hostname: "demo.trycloudflare.com"});
  assert.equal(publicUI.node("public-demo-note").hidden, false);
  assert.match(publicUI.node("transcript-privacy-note").textContent, /HTTPS.*Cloudflare.*운영자의 PC.*요청 메모리/);
  assert.match(publicUI.node("feedback-privacy-note").textContent, /공개 데모에서는 보완 요청 저장을 제공하지 않습니다/);
  assert.equal(publicUI.pending.length, 0, "Privacy mode must work without an optional gateway metadata request");
  for (const hostname of ["localhost", "127.0.0.1", "127.0.0.2", "[::1]", "::1", null]) {
    const local = setup({hostname}); assert.equal(local.node("public-demo-note").hidden, true, hostname);
  }
  const first = setup(); const second = setup();
  await assessed(first); await assessed(second);
  await first.node("transcript-clear").emit("click");
  assert.equal(first.node("transcript-results").textContent, "");
  assert.match(second.node("transcript-results").textContent, /최신 비교/);
  const followup = ask(second);
  reply(second.pending[1], {answer: "독립된 탭", focus_check_ids: [], verification_items: []}); await followup;
  assert.equal(second.node("transcript-chat").textContent, "독립된 탭");
}

async function testCurrentNetworkErrorsAreSafeAndControlsRecover() {
  const ui = setup(); const operation = extract(ui);
  ui.pending[0].reject(new Error("C:/private/student.pdf")); await operation;
  assert.doesNotMatch(ui.node("transcript-message").textContent, /private|student\.pdf/);
  assert.match(ui.node("transcript-message").textContent, /성적표를 읽을 수 없습니다/);
  assert.equal(ui.node("transcript-extract").disabled, false);
  await prepare(ui); const compare = ui.node("transcript-assess").emit("click");
  reply(ui.pending[1], {}, 503); await compare;
  assert.match(ui.node("transcript-message").textContent, /학사 근거를 확인할 수 없습니다/);
  assert.equal(ui.node("transcript-assess").disabled, false);
}

async function run() {
  const checks = [testClearActuallyAbortsExtractionAndDropsStudentRows, testReplacedPDFIgnoresLateResponseAndOldFinally,
    testManualReplacementAndPageChangeAbortExtraction, testClearAssessmentAndLateJSONCannotRestoreResult,
    testOldAssessmentFinallyCannotEnableNewAssessment, testAssessmentIsCancelledByEveryTranscriptEdit,
    testOverlappingFollowupsCancelAndKeepNewestAnswer, testNewGenerationAndPagehideCancelFollowupAndReleaseData,
    testTabsRemainIndependentAndPublicCopyNamesHostingPC, testCurrentNetworkErrorsAreSafeAndControlsRecover];
  for (const check of checks) { await check(); console.log(`PASS ${check.name}`); }
  console.log(`PASS ${checks.length} transcript lifecycle regression groups`);
}
run().catch(error => {console.error(error); process.exitCode = 1;});
