"use strict";
// Exercise actual frontend scripts with owned deferred HTTP responses, no server/model.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const webRoot = path.join(__dirname, "../../src/academic_assistant/web");
const html = fs.readFileSync(path.join(webRoot, "index.html"), "utf8");
const scripts = ["evidence.js", "transcript.js", "app.js"].map(name => [name, fs.readFileSync(path.join(webRoot, name), "utf8")]);
const tick = () => new Promise(resolve => setImmediate(resolve));
const clone = value => JSON.parse(JSON.stringify(value));

class Element {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {};
    this.attributes = {}; this.listeners = {}; this._text = ""; this._value = "";
    this.hidden = false; this.disabled = false; this.checked = false; this.files = [];
  }
  get value() { return this._value; }
  set value(value) { this._value = String(value); if (this.type === "file" && !value) this.files = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set textContent(value) { this.replaceChildren(); this._text = String(value); }
  set innerHTML(_value) { throw new Error("Unsafe HTML assignment"); }
  insertAdjacentHTML() { throw new Error("Unsafe HTML insertion"); }
  get firstChild() { return this.children[0] || null; }
  appendChild(child) { child.remove(); child.parentNode = this; this.children.push(child); return child; }
  append(...children) { children.forEach(child => this.appendChild(child)); }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); child.parentNode = null; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  replaceChildren(...children) { this.children.forEach(child => { child.parentNode = null; }); this.children = []; this._text = ""; this.append(...children); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  addEventListener(type, handler) { (this.listeners[type] ??= []).push(handler); }
  emit(type, properties = {}) { const event = {type, target: this, preventDefault() {}, ...properties}; return Promise.all((this.listeners[type] || []).map(handler => handler(event))); }
  dispatchEvent(event) { return this.emit(event.type); }
  all() { return [this, ...this.children.flatMap(child => child.all())]; }
  querySelector(selector) {
    if (selector === "button[type='submit']") return this.children.find(child => child.type === "submit");
    const field = selector.match(/^\[data-field=['"]([^'"]+)['"]\]$/)?.[1];
    assert.ok(field, `Unsupported fixture selector ${selector}`);
    return this.all().find(child => child.dataset.field === field) || null;
  }
  setCustomValidity(value) { this.validity = value; }
  reportValidity() { return !this.validity; }
  focus() { this.focused = true; }
  scrollIntoView() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
}

function setup({runtime = {}, pendingRuntime = false} = {}) {
  const elements = new Map(); const root = new Element("body");
  for (const match of html.matchAll(/<([a-z][a-z0-9]*)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const node = new Element(match[1]); node.id = match[3];
    node.hidden = /\bhidden\b/.test(match[2]); node.disabled = /\bdisabled\b/.test(match[2]);
    node.type = match[2].match(/\btype="([^"]+)"/)?.[1];
    for (const data of match[2].matchAll(/\bdata-([a-z-]+)="([^"]+)"/g)) {
      node.dataset[data[1].replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = data[2];
    }
    elements.set(node.id, node); root.append(node);
  }
  const credits = Array.from(html.matchAll(/data-credit-metric="([^"]+)"/g), match => {
    const node = new Element("input"); node.dataset.creditMetric = match[1]; root.append(node); return node;
  });
  const questions = Array.from(html.matchAll(/data-question="([^"]+)"/g), match => {
    const node = new Element("button"); node.dataset.question = match[1]; return node;
  });
  const transcriptQuestions = Array.from(html.matchAll(/data-transcript-question="([^"]+)"/g), match => {
    const node = new Element("button"); node.dataset.transcriptQuestion = match[1]; return node;
  });
  const node = id => elements.get(id) || root.all().find(item => item.id === id) || null;
  assert.equal(node("new-conversation").dataset.conversationMode, "friendly", "Actual UI must opt in");
  const submit = new Element("button"); submit.type = "submit"; node("question-form").append(submit);
  const document = {body: root, getElementById: node, createElement: tag => new Element(tag),
    querySelectorAll: selector => {
      if (selector === "[data-credit-metric]") return credits;
      if (selector === "[data-question]") return questions;
      assert.equal(selector, "[data-transcript-question]"); return transcriptQuestions;
    },
  };
  const calls = []; const window = new Element("window");
  window.location = {hostname: "demo.invalid", href: "https://demo.invalid/", origin: "https://demo.invalid"};
  let releaseRuntime;
  const runtimeReply = () => ({ok: true, json: async () => clone(runtime)});
  const context = {document, window, location: window.location, AbortController, Event, URL,
    fetch: (url, options) => {
      if (url === "/v1/academic/runtime") return pendingRuntime ? new Promise(resolve => { releaseRuntime = () => resolve(runtimeReply()); }) : Promise.resolve(runtimeReply());
      return new Promise((resolve, reject) => calls.push({url, options, resolve, reject}));
    },
  };
  for (const target of [context, window]) for (const key of ["localStorage", "sessionStorage", "indexedDB"]) {
    Object.defineProperty(target, key, {get() { throw new Error(`Persistent storage accessed ${key}`); }});
  }
  vm.createContext(context);
  for (const [filename, source] of scripts) vm.runInContext(source, context, {filename: `actual-${filename}`});
  return {node, root, calls, credits, window, releaseRuntime: () => releaseRuntime(),
    render: vm.runInContext("renderResponse", context)};
}

const ready = {llm_mode: "grounded_answer_generation", llm_configured: true, llm_model_available: true};
const ids = ["cwnu.cs.2026.credits.graduation-total", "cwnu.cs.2026.credits.general-total"];
function policy(updates = {}) {
  return {schema_version: "1.0.0", packet_id: "academic-synthetic", status: "supported", answer: "승인된 정식 기준",
    intent_ids: ["credits.graduation.total", "credits.general.total"], context_used: false,
    context_question: "졸업 총학점 및 교양 총학점 기준", conversational_answer: "졸업은130학점 이상, 교양은34학점 이상이 필요해요.",
    presentation_claim_ids: [...ids], calculations: [{metric: "credits.graduation.total", required: 130, earned: 107, gap: 23}],
    evidence_packet: {status: "supported", applied_rules: ids.map(rule_id => ({rule_id, rule_sha256: "a".repeat(64)})), evidence: [], issues: []},
    llm_status: "skipped", generation_status: "disabled", generated_answer: null, generated_claim_ids: [], ...updates};
}
function generated(initial, updates = {}) {
  return {...clone(initial), generation_status: "generated", generated_claim_ids: [...ids],
    generated_answer: "검증된 모델 문장", conversational_answer: "검증된 모델 문장", ...updates};
}
function reply(call, value, status = 200) { call.resolve({ok: status >= 200 && status < 300, status, json: async () => clone(value)}); }
function ask(ui, question = "졸업학점과 교양학점 기준을 알려주세요") {
  ui.node("question").value = question; ui.node("question").dispatchEvent(new Event("input"));
  return ui.node("question-form").emit("submit");
}
async function first(ui, value = policy(), question) {
  const operation = ask(ui, question); const call = ui.calls.at(-1);
  reply(call, value); await operation; await tick(); return call;
}
function transcriptReply(updates = {}) {
  return {status: "supported", answer: "현재 이수 기록 부분 비교 · 최종 졸업 인증 아님",
    conversational_answer: "현재 기록에서는23학점이 부족해요. 최종 졸업 인증은 아니에요.",
    context_question: "졸업까지 몇 학점 더 필요해?", context_used: false,
    selected_checks: [{check_id: "credits.graduation.total", label: "졸업 총학점", result: "not_met", required: 130, earned: 107, gap: 23,
      note: "부분 비교", missing_courses: [], evidence_packet: {status: "supported", applied_rules: [], evidence: []}}],
    focus_check_ids: ["credits.graduation.total"], verification_items: [], ...updates};
}
async function assess(ui) {
  await ui.node("transcript-demo").emit("click");
  ui.node("transcript-confirmed").checked = true; ui.node("transcript-degree").value = "single_major";
  await ui.node("transcript-confirmed").emit("change");
  const operation = ui.node("transcript-assess").emit("click");
  const call = ui.calls.at(-1); assert.equal(call.url, "/v1/academic/transcripts/assess");
  reply(call, {checks: [], answer: "가상 비교", issues: [], verification_items: [], raw_earned_credits: 6});
  await operation; assert.ok(ui.window.AcademicTranscript.current());
}

async function testUsefulFastReplyThenOneCapturedRefinement() {
  const ui = setup({runtime: ready}); await tick();
  ui.credits.find(item => item.dataset.creditMetric === "credits.graduation.total").value = "107";
  const initial = policy(); const call = await first(ui, initial);
  const payload = JSON.parse(call.options.body);
  assert.equal(payload.response_style, "friendly"); assert.equal(payload.generate_answer, false);
  assert.equal(payload.previous_question, null); assert.equal(ui.calls.length, 2);
  assert.equal(ui.node("answer-text").textContent, initial.conversational_answer);
  assert.match(ui.node("calculations").textContent, /23학점 부족/);
  assert.doesNotMatch(ui.node("generation-note").textContent, /Gemma 작성/);
  const refinement = ui.calls[1]; assert.deepEqual(JSON.parse(refinement.options.body), {...payload, generate_answer: true});
  assert.equal(refinement.options.cache, "no-store"); assert.equal(refinement.options.credentials, "omit");
  reply(refinement, generated(initial)); await tick();
  assert.equal(ui.node("answer-text").textContent, "검증된 모델 문장");
  assert.match(ui.node("generation-note").textContent, /Gemma 작성/);
  assert.match(ui.node("calculations").textContent, /23학점 부족/);
  assert.equal(ui.calls.length, 2);
}

async function testRuntimeGateAndCapturedReadinessDoNotQueueInference() {
  for (const runtime of [{}, {...ready, llm_configured: false}, {...ready, llm_model_available: false}, {...ready, llm_mode: "intent_suggestion"}]) {
    const ui = setup({runtime}); await tick(); await first(ui);
    assert.equal(ui.calls.length, 1); assert.doesNotMatch(ui.node("generation-note").textContent, /Gemma 작성/);
  }
  const ui = setup({runtime: ready, pendingRuntime: true});
  const operation = ask(ui); ui.releaseRuntime(); await tick();
  reply(ui.calls[0], policy()); await operation; await tick();
  assert.equal(ui.calls.length, 1, "Readiness learned after submit must not queue inference");
}

async function testOptionalFailuresKeepFirstReplyAndNeverRetry() {
  for (const failure of ["network", "http", "busy", "rejected", "cached"]) {
    const ui = setup({runtime: ready}); await tick(); const initial = policy(); await first(ui, initial);
    if (failure === "network") ui.calls[1].reject(new Error("C:/private/student.json"));
    else if (failure === "http") reply(ui.calls[1], {detail: "private"}, 503);
    else reply(ui.calls[1], failure === "cached" ? generated(initial, {generation_status: "cached"})
      : {...initial, generation_status: failure});
    await tick(); assert.equal(ui.calls.length, 2);
    if (failure === "cached") assert.match(ui.node("generation-note").textContent, /재사용/);
    else { assert.equal(ui.node("answer-text").textContent, initial.conversational_answer); assert.doesNotMatch(ui.node("generation-note").textContent, /Gemma 작성|private|student/); }
  }
}

async function testChangedRefinementIdentityCannotReplaceTrustedCore() {
  const changes = [{packet_id: "academic-other"}, {answer: "바뀐 판정"}, {status: "out_of_scope"},
    {calculations: [{metric: "credits.graduation.total", required: 130, earned: 1, gap: 129}]},
    {evidence_packet: {...policy().evidence_packet, applied_rules: [{rule_id: ids[0], rule_sha256: "b".repeat(64)}]}},
    {context_question: "전공필수 기준"}];
  for (const change of changes) {
    const ui = setup({runtime: ready}); await tick(); const initial = policy(); await first(ui, initial);
    reply(ui.calls[1], generated(initial, change)); await tick();
    assert.equal(ui.node("answer-text").textContent, initial.conversational_answer);
    assert.match(ui.node("calculations").textContent, /23학점 부족/);
    assert.doesNotMatch(ui.node("generation-note").textContent, /Gemma 작성/);
  }
}

async function testMissingReorderedDuplicateOrExtraModelClaimsCannotDisplay() {
  for (const claims of [[], [ids[0]], [...ids].reverse(), [ids[0], ids[0]], [...ids, "extra"]]) {
    const ui = setup({runtime: ready}); await tick(); const initial = policy(); await first(ui, initial);
    reply(ui.calls[1], generated(initial, {generated_claim_ids: claims})); await tick();
    assert.equal(ui.node("answer-text").textContent, initial.conversational_answer);
    assert.doesNotMatch(ui.node("generation-note").textContent, /Gemma 작성/);
  }
}

async function testFriendlyPresentationCoverageAndTextOnlyRendering() {
  const ui = setup(); const injection = '<img src=x onerror="alert(1)">';
  await first(ui, policy({conversational_answer: injection}));
  assert.equal(ui.node("answer-text").textContent, injection); assert.equal(ui.node("answer-text").children.length, 0);
  for (const changes of [{presentation_claim_ids: []}, {presentation_claim_ids: [...ids].reverse()},
    {conversational_answer: ""}, {conversational_answer: "가".repeat(8001)}]) {
    ui.render(policy(changes)); assert.equal(ui.node("answer-text").textContent, policy().answer);
  }
  const refused = policy({status: "insufficient_evidence", context_question: null, presentation_claim_ids: [],
    conversational_answer: "어떤 기준인지 알려주실래요?", evidence_packet: {status: "insufficient_evidence", applied_rules: [], evidence: [], issues: []}});
  await first(ui, refused); assert.equal(ui.node("answer-text").textContent, refused.conversational_answer);
  const operation = ask(ui, "그럼 몇 학점 남았어?"); assert.equal(JSON.parse(ui.calls.at(-1).options.body).previous_question, null);
  reply(ui.calls.at(-1), refused); await operation;
}

async function testBothStagesRejectLateCompletionsAfterInvalidation() {
  for (const stage of ["first", "refinement"]) for (const action of ["new-question", "reset", "credit", "transcript", "pagehide"]) {
    const ui = setup({runtime: ready}); await tick(); let operation; let stale;
    if (stage === "first") { operation = ask(ui); stale = ui.calls[0]; }
    else { await first(ui); stale = ui.calls[1]; }
    if (action === "new-question") {
      const current = ask(ui, "전공선택 학점 기준"); const active = ui.calls.at(-1);
      reply(active, policy({conversational_answer: "새 질문의 답변"})); await current;
    } else if (action === "reset") await ui.node("new-conversation").emit("click");
    else if (action === "credit") await ui.credits[0].emit("input");
    else if (action === "transcript") await ui.node("transcript-demo").emit("click");
    else await ui.window.emit("pagehide");
    assert.equal(stale.options.signal.aborted, true, `${stage}/${action}`);
    const before = ui.node("answer-text").textContent;
    reply(stale, stage === "first" ? policy({conversational_answer: "오래된 답변"}) : generated(policy(), {conversational_answer: "오래된 모델 답변"}));
    if (operation) await operation; await tick();
    assert.equal(ui.node("answer-text").textContent, before, `${stage}/${action}`);
    assert.doesNotMatch(ui.node("answer-text").textContent, /오래된/);
  }
}

async function testLateResponseJSONCannotRestoreNewConversation() {
  const ui = setup({runtime: ready}); await tick(); const operation = ask(ui); let releaseJSON;
  ui.calls[0].resolve({ok: true, json: () => new Promise(resolve => { releaseJSON = resolve; })}); await tick();
  await ui.node("new-conversation").emit("click"); releaseJSON(policy()); await operation; await tick();
  assert.equal(ui.node("answer-text").textContent, ""); assert.equal(ui.calls.length, 1);
  assert.equal(ui.node("conversation-turns").children.length, 0);
}

async function testNewConversationClearsActualTranscriptHistoryCreditsPDFAndRequests() {
  const ui = setup({runtime: ready}); await tick(); await assess(ui);
  ui.credits[0].value = "100";
  await first(ui, policy(), "졸업 총학점 기준"); const refinement = ui.calls.at(-1);
  const reference = {rule_id: ids[0], source_id: "cwnu.curriculum.2026.changwon-undergraduate", locator: "PDF3쪽", claim: "가상 근거"};
  const trigger = ui.window.AcademicEvidence.button(reference, 0);
  const preview = trigger.emit("click"); const previewCall = ui.calls.at(-1);
  assert.match(previewCall.url, /\/v1\/academic\/evidence\//);
  assert.ok(ui.root.all().some(item => item.tagName === "DIALOG" && item.open));
  ui.node("question").value = "다음 질문"; ui.node("transcript-file").value = "synthetic.pdf";
  await ui.node("new-conversation").emit("click");
  assert.equal(refinement.options.signal.aborted, true); assert.equal(previewCall.options.signal.aborted, true);
  assert.equal(ui.window.AcademicTranscript.current(), null);
  assert.equal(ui.node("transcript-rows").children.length, 0); assert.equal(ui.node("transcript-confirmed").checked, false);
  assert.equal(ui.node("transcript-file").value, ""); assert.equal(ui.node("transcript-results").textContent, "");
  assert.equal(ui.node("question").value, ""); assert.ok(ui.credits.every(input => input.value === ""));
  assert.equal(ui.node("conversation-turns").children.length, 0); assert.equal(ui.node("current-question").textContent, "");
  assert.equal(ui.node("answer-text").textContent, ""); assert.equal(ui.node("answer-panel").getAttribute("aria-busy"), "false");
  assert.ok(ui.root.all().filter(item => item.tagName === "DIALOG").every(dialog => !dialog.open));
  previewCall.reject(new Error("C:/private/preview.pdf")); await preview;
  reply(refinement, generated(policy())); await tick(); assert.equal(ui.node("answer-text").textContent, "");
}

async function testSixTurnBoundAndIndependentTabsWithoutPersistence() {
  const firstUI = setup(); const secondUI = setup(); await tick();
  for (let index = 1; index <= 8; index++) await first(firstUI, policy({conversational_answer: `답변${index}`}), `질문${index}`);
  assert.equal(firstUI.node("conversation-turns").children.length, 5);
  assert.equal(firstUI.node("current-question").textContent, "질문8");
  assert.doesNotMatch(firstUI.node("conversation-turns").textContent, /질문1|질문2/);
  await first(secondUI, policy({conversational_answer: "다른 탭 답변"}));
  await firstUI.node("new-conversation").emit("click");
  assert.equal(secondUI.node("answer-text").textContent, "다른 탭 답변");
}

async function testUnconfirmedFailedAndEditedTranscriptCannotBeReused() {
  const ui = setup(); await tick(); await ui.node("transcript-demo").emit("click");
  assert.equal(ui.window.AcademicTranscript.current(), null);
  await ask(ui, "내 성적표에서 몇 학점 부족해?"); assert.equal(ui.calls.length, 0);
  assert.match(ui.node("answer-text").textContent, /먼저.*성적표/);
  ui.node("transcript-confirmed").checked = true; await ui.node("transcript-confirmed").emit("change");
  const failed = ui.node("transcript-assess").emit("click"); reply(ui.calls.at(-1), {}, 503); await failed;
  assert.equal(ui.window.AcademicTranscript.current(), null);
  await assess(ui); const snapshot = ui.window.AcademicTranscript.current(); snapshot.courses[0].credits = 999;
  assert.notEqual(ui.window.AcademicTranscript.current().courses[0].credits, 999, "Bridge returns isolated snapshot");
  await ui.node("transcript-rows").emit("input"); assert.equal(ui.window.AcademicTranscript.current(), null);
  await ask(ui, "내 이수내역에서 몇 학점 남았어?"); assert.equal(ui.calls.length, 2);
}

async function testConfirmedTranscriptOwnResponseAndSeparateCompatibleAnchor() {
  const ui = setup(); await tick(); await assess(ui); const snapshot = clone(ui.window.AcademicTranscript.current());
  let operation = ask(ui, "내 이수내역에서 졸업까지 몇 학점 더 필요해?"); let call = ui.calls.at(-1);
  assert.equal(call.url, "/v1/academic/transcripts/chat"); let payload = JSON.parse(call.options.body);
  assert.deepEqual(payload.transcript, snapshot); assert.equal(payload.response_style, "friendly"); assert.equal(payload.previous_question, null);
  assert.ok(!("generate_answer" in payload)); assert.ok(!("earned_credits" in payload));
  reply(call, transcriptReply()); await operation;
  assert.match(ui.node("transcript-checks-list").textContent, /미충족.*23학점/);
  assert.equal(ui.node("calculations").textContent, ""); assert.doesNotMatch(ui.node("generation-note").textContent, /Gemma/);
  operation = ask(ui, "그럼 몇 학점 남았어?"); call = ui.calls.at(-1);
  assert.equal(call.url, "/v1/academic/transcripts/chat"); assert.equal(JSON.parse(call.options.body).previous_question, transcriptReply().context_question);
  reply(call, transcriptReply({context_used: true})); await operation;
  operation = ask(ui, "전공필수 학점 기준"); call = ui.calls.at(-1);
  assert.equal(call.url, "/v1/academic/chat"); assert.equal(JSON.parse(call.options.body).previous_question, null);
  reply(call, policy()); await operation;
  operation = ask(ui, "다시 설명해 주세요"); call = ui.calls.at(-1);
  assert.equal(call.url, "/v1/academic/chat"); assert.equal(JSON.parse(call.options.body).previous_question, policy().context_question);
  reply(call, policy({context_used: true})); await operation;
}

async function testNeedsReviewPolicyEvidenceDoesNotBecomePersonalPass() {
  const ui = setup(); await tick(); await assess(ui);
  const data = transcriptReply({status: "insufficient_evidence", context_question: null,
    conversational_answer: "확인이 필요해요. 최종 졸업 인증은 아니에요.", selected_checks: [{
      check_id: "credits.graduation.total", label: "졸업 총학점", result: "needs_review", required: 130, earned: 130, gap: 0,
      note: "개인 인정 확인 필요", missing_courses: [], evidence_packet: {status: "insufficient_evidence", applied_rules: [], evidence: []},
      policy_packet: {status: "supported", applied_rules: [], evidence: [{rule_id: ids[0], source_id: "cwnu.curriculum.2026.changwon-undergraduate", locator: "PDF3쪽", claim: "기준만 안내"}]},
    }]});
  const operation = ask(ui, "내 성적표에서 남은 학점을 알려주세요"); reply(ui.calls.at(-1), data); await operation;
  const text = ui.node("transcript-checks-list").textContent;
  assert.match(text, /확인 필요/); assert.match(text, /기준 설명만.*개인 판정 근거 아님/);
  assert.doesNotMatch(text, /0학점 부족|· 충족/); assert.match(ui.node("status-badge").textContent, /근거 부족/);
  assert.doesNotMatch(ui.node("generation-note").textContent, /Gemma|졸업 가능/);
}

async function testExplicitStructuredCreditFactsDoNotSilentlyUseTranscriptTotal() {
  const ui = setup(); await tick(); await assess(ui);
  ui.credits.find(item => item.dataset.creditMetric === "credits.general.total").value = "12";
  const operation = ask(ui, "그럼 몇 학점 남았어?"); const call = ui.calls.at(-1);
  assert.equal(call.url, "/v1/academic/chat"); assert.deepEqual(JSON.parse(call.options.body).earned_credits, {"credits.general.total": 12});
  reply(call, policy({status: "insufficient_evidence", context_question: null, conversational_answer: "비교할 기준을 먼저 선택해 주세요.",
    presentation_claim_ids: [], calculations: [], evidence_packet: {status: "insufficient_evidence", applied_rules: [], evidence: [], issues: []}})); await operation;
  assert.match(ui.node("answer-text").textContent, /먼저 선택/); assert.equal(ui.node("calculations").textContent, "");
}

async function testCurrentErrorsNeverEchoPrivateDetailsOrLeaveSuccessLabels() {
  const ui = setup(); await tick(); const operation = ask(ui); ui.calls[0].reject(new Error("C:/private/student.pdf")); await operation;
  assert.doesNotMatch(ui.node("answer-text").textContent, /private|student\.pdf/);
  assert.doesNotMatch(ui.node("generation-note").textContent, /Gemma/);
  assert.equal(ui.node("answer-panel").getAttribute("aria-busy"), "false");
  const next = ask(ui, "다시 설명해 주세요"); assert.equal(JSON.parse(ui.calls.at(-1).options.body).previous_question, null);
  reply(ui.calls.at(-1), policy()); await next;
}

async function testCurrentRefinementCannotLabelOmittedGeneratedTextAsGemma() {
  for (const generation_status of ["generated", "cached"]) {
    const ui = setup({runtime: ready}); await tick(); const initial = policy(); await first(ui, initial);
    const active = ui.calls[1]; assert.equal(active.options.signal.aborted, false);
    reply(active, generated(initial, {generation_status, conversational_answer: "모델 문장을 누락한 다른 친절한 답변"}));
    await tick();
    assert.equal(ui.node("answer-text").textContent, initial.conversational_answer,
      "A current response with identical core and ordered claims must still preserve the first reply when actual generated text is omitted");
    assert.match(ui.node("calculations").textContent, /23학점 부족/);
    assert.doesNotMatch(ui.node("generation-note").textContent, /Gemma 작성|재사용/);
    assert.equal(ui.calls.length, 2);
  }
}

async function main() {
  const checks = [testUsefulFastReplyThenOneCapturedRefinement, testRuntimeGateAndCapturedReadinessDoNotQueueInference,
    testOptionalFailuresKeepFirstReplyAndNeverRetry, testChangedRefinementIdentityCannotReplaceTrustedCore,
    testMissingReorderedDuplicateOrExtraModelClaimsCannotDisplay, testFriendlyPresentationCoverageAndTextOnlyRendering,
    testBothStagesRejectLateCompletionsAfterInvalidation, testLateResponseJSONCannotRestoreNewConversation,
    testNewConversationClearsActualTranscriptHistoryCreditsPDFAndRequests, testSixTurnBoundAndIndependentTabsWithoutPersistence,
    testUnconfirmedFailedAndEditedTranscriptCannotBeReused, testConfirmedTranscriptOwnResponseAndSeparateCompatibleAnchor,
    testNeedsReviewPolicyEvidenceDoesNotBecomePersonalPass, testExplicitStructuredCreditFactsDoNotSilentlyUseTranscriptTotal,
    testCurrentErrorsNeverEchoPrivateDetailsOrLeaveSuccessLabels, testCurrentRefinementCannotLabelOmittedGeneratedTextAsGemma];
  for (const check of checks) { await check(); process.stdout.write(`PASS ${check.name}\n`); }
  process.stdout.write(`PASS ${checks.length} independent natural conversation consumer groups\n`);
}
main().catch(error => { process.stderr.write(`${error.stack}\n`); process.exitCode = 1; });
