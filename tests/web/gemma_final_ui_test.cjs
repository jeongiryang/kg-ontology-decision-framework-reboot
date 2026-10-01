"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.join(__dirname, "../../src/academic_assistant/web");
const source = fs.readFileSync(path.join(root, "app.js"), "utf8");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");

class Element {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase(); this.children = []; this.listeners = {};
    this.attributes = {}; this.dataset = {}; this.value = ""; this._text = "";
    this.hidden = false; this.disabled = false; this.checked = false;
  }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set textContent(value) { this.children = []; this._text = String(value); }
  set innerHTML(_value) { throw new Error("Unsafe HTML assignment"); }
  insertAdjacentHTML() { throw new Error("Unsafe HTML insertion"); }
  get firstChild() { return this.children[0] || null; }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { children.forEach(child => this.appendChild(child)); }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] || null; }
  addEventListener(name, handler) { (this.listeners[name] ??= []).push(handler); }
  dispatchEvent(event) { return this.emit(event.type); }
  emit(type) { return Promise.all((this.listeners[type] || []).map(handler => handler({type, preventDefault() {}}))); }
  querySelector(selector) {
    assert.equal(selector, "button[type='submit']");
    return this.children.find(child => child.type === "submit");
  }
  setCustomValidity(value) { this.validity = value; }
  reportValidity() { return !this.validity; }
  focus() {}
}

function setup() {
  const elements = new Map();
  for (const match of html.matchAll(/<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
    elements.set(match[2], new Element(match[1]));
  }
  const credits = [];
  for (const match of html.matchAll(/data-credit-metric="([^"]+)"/g)) {
    const input = new Element("input"); input.dataset.creditMetric = match[1]; credits.push(input);
  }
  const quickQuestions = Array.from(html.matchAll(/data-question="([^"]+)"/g), match => {
    const button = new Element("button"); button.dataset.question = match[1]; return button;
  });
  const node = id => {
    assert.ok(elements.has(id), `Real HTML missing #${id}`);
    return elements.get(id);
  };
  const button = new Element("button"); button.type = "submit"; node("question-form").appendChild(button);
  const document = {
    getElementById: node, createElement: tag => new Element(tag),
    querySelectorAll: selector => {
      if (selector === "[data-credit-metric]") return credits;
      assert.equal(selector, "[data-question]"); return quickQuestions;
    },
  };
  const calls = [];
  const window = {location: {hostname: "demo.invalid"}};
  const context = {document, window, location: window.location, AbortController, Event,
    fetch: (url, options) => {
      if (url === "/v1/academic/runtime") return Promise.resolve({ok: true, json: async () => ({})});
      return new Promise(resolve => calls.push({url, options, resolve}));
    },
  };
  for (const target of [context, window]) {
    for (const key of ["localStorage", "sessionStorage", "indexedDB"]) {
      Object.defineProperty(target, key, {get() { throw new Error(`Persistent storage accessed: ${key}`); }});
    }
  }
  vm.createContext(context);
  vm.runInContext(source, context, {filename: "actual-app.js"});
  return {node, credits, calls, render: vm.runInContext("renderResponse", context),
    error: vm.runInContext("renderError", context)};
}

const ids = ["cwnu.cs.2026.credits.general-total", "cwnu.cs.2026.credits.general-foundation"];
function response(updates = {}) {
  return {status: "supported", answer: "기존 승인 근거 답변", packet_id: "academic-demo",
    calculations: [{metric: "credits.graduation.total", required: 130, earned: 107, gap: 23}],
    evidence_packet: {status: "supported", applied_rules: ids.map(rule_id => ({rule_id, rule_sha256: "a".repeat(64)})),
      evidence: [], issues: []}, generation_status: "generated", generated_claim_ids: [...ids],
    generated_answer: "모델이 반환한 검증 문장", ...updates};
}
function fallback(ui, value) {
  ui.render(value);
  assert.equal(ui.node("answer-text").textContent, value.answer);
  assert.doesNotMatch(ui.node("generation-note").textContent, /Gemma/);
}

function testAcceptedModelTextAndLocalGapRemainSeparate() {
  const ui = setup(); const data = response(); const before = JSON.stringify(data);
  ui.render(data);
  assert.equal(ui.node("answer-text").textContent, data.generated_answer);
  assert.match(ui.node("generation-note").textContent, /Gemma/);
  assert.match(ui.node("calculations").textContent, /23학점 부족/);
  assert.doesNotMatch(ui.node("answer-text").textContent, /23/);
  assert.equal(JSON.stringify(data), before);
}
function testCachedIsTruthfullyMarkedAsReuse() {
  const ui = setup(); ui.render(response({generation_status: "cached"}));
  assert.match(ui.node("generation-note").textContent, /Gemma/);
  assert.match(ui.node("generation-note").textContent, /재사용/);
}
function testEveryFallbackIgnoresUnverifiedGeneratedText() {
  for (const status of ["disabled", "unavailable", "rejected", "busy", "not_applicable", "invented"]) {
    fallback(setup(), response({generation_status: status}));
  }
}
function testUnsupportedOuterOrPacketStatusCannotDisplayModelText() {
  for (const status of ["insufficient_evidence", "conflict", "out_of_scope"]) {
    fallback(setup(), response({status}));
    fallback(setup(), response({evidence_packet: {...response().evidence_packet, status}}));
  }
}
function testCompleteOrderedUniqueClaimCoverageRequired() {
  for (const generated_claim_ids of [[], [ids[0]], [...ids, "cwnu.cs.2026.extra"],
    [...ids].reverse(), [ids[0], ids[0]], "not-array", null]) {
    fallback(setup(), response({generated_claim_ids}));
  }
  fallback(setup(), response({evidence_packet: {...response().evidence_packet, applied_rules: []}}));
}
function testGeneratedTextMustBeBoundedNonemptyString() {
  for (const generated_answer of ["", "x".repeat(2401), null, {}, 1]) fallback(setup(), response({generated_answer}));
}
function testBothGeneratedAndTrustedTextUseSafeTextNodes() {
  const injected = '<img src=x onerror="globalThis.compromised=true">';
  const ui = setup(); ui.render(response({generated_answer: injected}));
  assert.equal(ui.node("answer-text").textContent, injected);
  assert.equal(ui.node("answer-text").children.length, 0);
  fallback(ui, response({generation_status: "rejected", answer: injected}));
  assert.equal(ui.node("answer-text").children.length, 0);
}
function testLegacyResponseAndNetworkErrorNeverLeaveSuccessLabel() {
  const ui = setup(); const legacy = response();
  delete legacy.generation_status; delete legacy.generated_answer; delete legacy.generated_claim_ids;
  fallback(ui, legacy);
  ui.render(response()); ui.error();
  assert.equal(ui.node("generation-note").textContent, "");
  assert.equal(ui.node("generation-note").hidden, true);
  assert.doesNotMatch(ui.node("answer-text").textContent, /모델이 반환한/);
}
async function testActualChatSubmissionOptsInWithoutPersistence() {
  const ui = setup(); ui.node("question").value = "졸업학점은 얼마인가요?";
  ui.credits.find(input => input.dataset.creditMetric === "credits.graduation.total").value = "107";
  const operation = ui.node("question-form").emit("submit");
  assert.equal(ui.calls.length, 1);
  const call = ui.calls[0]; const payload = JSON.parse(call.options.body);
  assert.equal(call.url, "/v1/academic/chat"); assert.equal(call.options.method, "POST");
  assert.equal(payload.generate_answer, true); assert.equal(payload.previous_question, null);
  assert.deepEqual(payload.earned_credits, {"credits.graduation.total": 107});
  assert.equal(call.options.cache, "no-store"); assert.equal(call.options.credentials, "omit");
  call.resolve({ok: true, json: async () => response()}); await operation;
  assert.equal(ui.node("answer-text").textContent, "모델이 반환한 검증 문장");
}

async function main() {
  const tests = [testAcceptedModelTextAndLocalGapRemainSeparate, testCachedIsTruthfullyMarkedAsReuse,
    testEveryFallbackIgnoresUnverifiedGeneratedText, testUnsupportedOuterOrPacketStatusCannotDisplayModelText,
    testCompleteOrderedUniqueClaimCoverageRequired, testGeneratedTextMustBeBoundedNonemptyString,
    testBothGeneratedAndTrustedTextUseSafeTextNodes, testLegacyResponseAndNetworkErrorNeverLeaveSuccessLabel,
    testActualChatSubmissionOptsInWithoutPersistence];
  for (const test of tests) { await test(); process.stdout.write(`PASS ${test.name}\n`); }
  process.stdout.write(`PASS ${tests.length} final Gemma consumer regression groups\n`);
}
main().catch(error => { process.stderr.write(`${error.stack}\n`); process.exitCode = 1; });
