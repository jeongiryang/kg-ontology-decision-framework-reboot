"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../src/academic_assistant/web/evidence.js"), "utf8");
const ruleA = "fixture.approved.first";
const ruleB = "fixture.approved.second";
const sourceId = "fixture.approved.source";

class Element {
  constructor(tag, document) {
    this.tagName = tag.toUpperCase();
    this.document = document;
    this.children = [];
    this.attributes = {};
    this.listeners = {};
    this.className = "";
    this.open = false;
    this._text = "";
  }
  get isConnected() { return Boolean(this.root || (this.parentNode && this.parentNode.isConnected)); }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
  set textContent(value) { this.replaceChildren(); this._text = String(value); }
  set innerHTML(_value) { throw new Error("Unsafe HTML assignment"); }
  set style(_value) { throw new Error("Inline style violates CSP"); }
  append(...nodes) {
    for (const node of nodes) {
      node.remove();
      node.parentNode = this;
      this.children.push(node);
    }
  }
  appendChild(node) { this.append(node); return node; }
  replaceChildren(...nodes) {
    for (const child of this.children) child.parentNode = null;
    this.children = [];
    this._text = "";
    this.append(...nodes);
  }
  setAttribute(name, value) {
    assert.notEqual(name, "style");
    this.attributes[name] = String(value);
  }
  addEventListener(name, handler) { (this.listeners[name] ??= []).push(handler); }
  emit(name, values = {}) {
    const event = { target: this, preventDefault() { this.defaultPrevented = true; }, ...values };
    return Promise.all((this.listeners[name] ?? []).map((handler) => handler(event)));
  }
  remove() {
    if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    this.parentNode = null;
  }
  focus() { assert.equal(this.isConnected, true, "Cannot focus a removed element"); this.document.activeElement = this; }
  showModal() { this.open = true; this.focus(); }
  close() { this.open = false; return this.emit("close"); }
}

function setup() {
  const document = { createElement: (tag) => new Element(tag, document) };
  document.body = new Element("body", document);
  document.body.root = true;
  const pending = [];
  const window = { location: { href: "http://127.0.0.1:8010/", origin: "http://127.0.0.1:8010" } };
  const context = {
    document, window, URL, AbortController,
    fetch: (url, options) => new Promise((resolve, reject) => pending.push({ url, options, resolve, reject })),
  };
  vm.runInNewContext(source, context);
  function trigger(ruleId = ruleA, index = 0, extras = {}) {
    const button = window.AcademicEvidence.button({ rule_id: ruleId, source_id: sourceId, ...extras }, index);
    document.body.appendChild(button);
    return button;
  }
  function all(predicate, node = document.body) {
    return [node, ...node.children.flatMap((child) => all(() => true, child))].filter(predicate);
  }
  const findClass = (name) => all((node) => node.className.split(" ").includes(name))[0];
  const dialog = () => all((node) => node.tagName === "DIALOG")[0];
  return { document, window, pending, trigger, all, findClass, dialog };
}

function metadata(ruleId = ruleA, index = 0, page = 577, changes = {}) {
  const query = `evidence_index=${index}&pdf_page=${page}`;
  return {
    schema_version: "1.0.0", rule_id: ruleId, source_id: sourceId, source_sha256: "a".repeat(64),
    pdf_page: page, printed_page: page - 8, quote: "졸업학점 130", location: "PDF p.577 (printed p.569)",
    precision: "exact", notice: "승인된 인용 위치에 빨간 밑줄을 표시했습니다.",
    image_url: `/v1/academic/evidence/${ruleId}/preview.png?${query}`,
    pdf_url: `/v1/academic/evidence/${ruleId}/preview.pdf?${query}`,
    ...changes,
  };
}

function reply(request, data, status = 200) {
  request.resolve({ ok: status >= 200 && status < 300, status, json: async () => data });
}

async function loaded(ui, button, data) {
  const operation = button.emit("click");
  reply(ui.pending.at(-1), data);
  await operation;
  return ui;
}

async function testExactViewAndSafeCopyLink() {
  const ui = setup();
  const trigger = ui.trigger(ruleA, 1);
  await loaded(ui, trigger, metadata(ruleA, 1));
  assert.equal(trigger.textContent, "근거 PDF 보기");
  assert.equal(trigger.type, "button");
  assert.equal(ui.pending[0].url, `/v1/academic/evidence/${ruleA}/preview?evidence_index=1`);
  assert.equal(ui.pending[0].options.cache, "no-store");
  assert.equal(ui.dialog().open, true);
  assert.equal(ui.dialog().attributes["aria-labelledby"], "academic-evidence-heading");
  assert.match(ui.dialog().textContent, /PDF 577페이지 · 인쇄 569페이지/);
  assert.match(ui.dialog().textContent, /졸업학점 130/);
  assert.match(ui.dialog().textContent, /개인의 졸업 가능 여부를 판정하지 않습니다/);
  const image = ui.findClass("evidence-preview-image");
  assert.equal(image.src, metadata(ruleA, 1).image_url);
  await image.emit("load");
  assert.match(ui.findClass("evidence-image-status").textContent, /빨간 밑줄/);
  const download = ui.findClass("evidence-download");
  assert.equal(download.href, metadata(ruleA, 1).pdf_url);
  assert.match(download.textContent, /복사본/);
  assert.equal(download.download, `citation-${ruleA}-p577.pdf`);
}

async function testPageFallbackAndUntrustedTextAreVisibleWithoutHTML() {
  const ui = setup();
  const trigger = ui.trigger(ruleA, 0, { locator: "<script>unsafe()</script>" });
  const quote = '<img src="https://invalid.example/" onerror="unsafe()">';
  await loaded(ui, trigger, metadata(ruleA, 0, 2, { printed_page: null, precision: "page_only", quote,
    notice: "정확한 위치를 확인하지 못해 페이지만 표시합니다." }));
  assert.match(ui.dialog().textContent, /페이지 근거 · 위치 미확인/);
  assert.match(ui.dialog().textContent, /인쇄 페이지 표기 없음/);
  assert.equal(ui.findClass("evidence-quote").textContent, quote);
  assert.equal(ui.all((node) => node.tagName === "IMG").length, 1);
  await ui.findClass("evidence-preview-image").emit("load");
  assert.match(ui.findClass("evidence-image-status").textContent, /밑줄 없이/);
}

async function testReversedSuccessAndOldErrorCannotReplaceNewResponse() {
  const ui = setup();
  const first = ui.trigger(ruleA).emit("click");
  const second = ui.trigger(ruleB).emit("click");
  assert.equal(ui.pending[0].options.signal.aborted, true);
  reply(ui.pending[1], metadata(ruleB, 0, 577, { quote: "최신 인용" }));
  await second;
  reply(ui.pending[0], metadata(ruleA, 0, 577, { quote: "오래된 인용" }));
  await first;
  assert.match(ui.dialog().textContent, /최신 인용/);
  assert.doesNotMatch(ui.dialog().textContent, /오래된 인용/);

  const staleError = ui.trigger(ruleA).emit("click");
  const newer = ui.trigger(ruleB).emit("click");
  reply(ui.pending[3], metadata(ruleB, 0, 577, { quote: "최신 성공" }));
  await newer;
  ui.pending[2].reject(new Error("C:\\private\\source.pdf"));
  await staleError;
  assert.match(ui.dialog().textContent, /최신 성공/);
  assert.doesNotMatch(ui.dialog().textContent, /private|표시할 수 없습니다/);
}

async function testDelayedJSONAndDetachedImageErrorsAreStale() {
  const ui = setup();
  let resolveJSON;
  const old = ui.trigger(ruleA).emit("click");
  ui.pending[0].resolve({ ok: true, status: 200, json: () => new Promise((resolve) => { resolveJSON = resolve; }) });
  await new Promise((resolve) => setImmediate(resolve));
  const newer = ui.trigger(ruleB).emit("click");
  reply(ui.pending[1], metadata(ruleB));
  await newer;
  resolveJSON(metadata(ruleA, 0, 577, { quote: "늦은 JSON" }));
  await old;
  assert.doesNotMatch(ui.dialog().textContent, /늦은 JSON/);
  const oldImage = ui.findClass("evidence-preview-image");
  await loaded(ui, ui.trigger(ruleA), metadata(ruleA, 0, 577, { quote: "새 이미지" }));
  const currentImage = ui.findClass("evidence-preview-image");
  assert.notEqual(oldImage, currentImage);
  await oldImage.emit("error");
  assert.notEqual(currentImage.hidden, true);
  assert.doesNotMatch(ui.dialog().textContent, /표시할 수 없습니다/);
}

async function testUnsafeURLsAndMismatchedMetadataAreRejected() {
  const base = metadata();
  const variants = [
    { image_url: "javascript:alert(1)" },
    { image_url: `https://remote.example${base.image_url}` },
    { pdf_url: `//remote.example${base.pdf_url}` },
    { image_url: base.image_url.replace(ruleA, ruleB) },
    { pdf_url: base.pdf_url.replace("pdf_page=577", "pdf_page=999") },
    { image_url: base.image_url + "&path=C:/private.pdf" },
    { image_url: base.image_url + "&evidence_index=0" },
    { pdf_url: base.pdf_url + "#fragment" },
    { pdf_url: base.pdf_url.replace("evidence_index=0", "evidence_index=1") },
    { source_id: "fixture.different.source" },
    { rule_id: ruleB },
    { precision: "approximate" },
    { schema_version: "2.0.0" },
    { source_sha256: "unverified" },
  ];
  for (const changes of variants) {
    const ui = setup();
    await loaded(ui, ui.trigger(), { ...base, ...changes });
    assert.match(ui.dialog().textContent, /근거 페이지를 표시할 수 없습니다/);
    assert.equal(ui.all((node) => node.tagName === "IMG" || node.tagName === "A").length, 0);
    assert.doesNotMatch(ui.dialog().textContent, /remote|javascript|private/);
  }
}

async function testCloseEscapeAndRemovedFocusTargetInvalidateLoads() {
  const ui = setup();
  const trigger = ui.trigger();
  const pending = trigger.emit("click");
  await ui.findClass("evidence-close").emit("click");
  assert.equal(ui.dialog().open, false);
  assert.equal(ui.pending[0].options.signal.aborted, true);
  assert.equal(ui.document.activeElement, trigger);
  reply(ui.pending[0], metadata());
  await pending;
  assert.equal(ui.findClass("evidence-dialog-body").textContent, "");

  const escapeLoad = trigger.emit("click");
  await ui.dialog().emit("cancel");
  ui.pending[1].reject(new Error("private source error"));
  await escapeLoad;
  assert.equal(ui.dialog().open, false);
  assert.equal(ui.findClass("evidence-dialog-body").textContent, "");

  const removedLoad = trigger.emit("click");
  trigger.remove();
  await ui.findClass("evidence-close").emit("click");
  reply(ui.pending[2], metadata());
  await removedLoad;
  assert.equal(ui.document.activeElement, ui.findClass("evidence-close"));
}

async function testServerAndImageErrorsNeverExposePaths() {
  for (const status of [404, 503, 429]) {
    const ui = setup();
    const operation = ui.trigger().emit("click");
    reply(ui.pending[0], { detail: "C:/private/source.pdf" }, status);
    await operation;
    assert.doesNotMatch(ui.dialog().textContent, /private|source\.pdf/);
    assert.match(ui.dialog().textContent, status === 429 ? /확인 중인 요청/ : /표시할 수 없습니다/);
    assert.equal(ui.findClass("evidence-message").attributes.role, "alert");
  }
  const ui = setup();
  await loaded(ui, ui.trigger(), metadata());
  const image = ui.findClass("evidence-preview-image");
  await image.emit("error");
  assert.equal(image.hidden, true);
  assert.match(ui.findClass("evidence-image-status").textContent, /표시할 수 없습니다/);
}

async function testQueuedCloseEventDoesNotClearReopenedDialog() {
  const ui = setup();
  await loaded(ui, ui.trigger(ruleA), metadata(ruleA));
  ui.dialog().close = function () { this.open = false; };
  await ui.findClass("evidence-close").emit("click");
  await loaded(ui, ui.trigger(ruleB), metadata(ruleB, 0, 577, { quote: "다시 열린 최신 근거" }));
  await ui.dialog().emit("close");
  assert.equal(ui.dialog().open, true);
  assert.match(ui.dialog().textContent, /다시 열린 최신 근거/);
  assert.equal(ui.pending[1].options.signal.aborted, false);
}

async function testInvalidCitationInputsAreDisabledAndCaptureCannotMutate() {
  const ui = setup();
  for (const value of ["../../private", "javascript:alert(1)", "<script>", "UPPER.id", "한글.id", "a"]) {
    const button = ui.trigger(value);
    assert.equal(button.disabled, true);
    await button.emit("click");
  }
  assert.equal(ui.window.AcademicEvidence.button(null).disabled, true);
  assert.equal(ui.trigger(ruleA, -1).disabled, true);
  assert.equal(ui.trigger(ruleA, 0.5).disabled, true);
  assert.equal(ui.pending.length, 0);
  const ref = { rule_id: ruleA, source_id: sourceId };
  const button = ui.window.AcademicEvidence.button(ref, 0);
  ui.document.body.appendChild(button);
  ref.rule_id = "../../private";
  await loaded(ui, button, metadata(ruleA));
  assert.match(ui.pending[0].url, new RegExp(ruleA.replaceAll(".", "\\.")));
}

async function run() {
  const checks = [testExactViewAndSafeCopyLink, testPageFallbackAndUntrustedTextAreVisibleWithoutHTML,
    testReversedSuccessAndOldErrorCannotReplaceNewResponse, testDelayedJSONAndDetachedImageErrorsAreStale,
    testUnsafeURLsAndMismatchedMetadataAreRejected, testCloseEscapeAndRemovedFocusTargetInvalidateLoads,
    testServerAndImageErrorsNeverExposePaths, testQueuedCloseEventDoesNotClearReopenedDialog,
    testInvalidCitationInputsAreDisabledAndCaptureCannotMutate];
  for (const check of checks) {
    await check();
    console.log(`PASS ${check.name}`);
  }
  console.log(`PASS ${checks.length} evidence UI regression groups`);
}

run().catch((error) => { console.error(error); process.exitCode = 1; });
