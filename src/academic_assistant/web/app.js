"use strict";

const form = document.getElementById("question-form");
const question = document.getElementById("question");
const counter = document.getElementById("question-count");
const submit = form.querySelector("button[type='submit']");
const panel = document.getElementById("answer-panel");
const heading = document.getElementById("result-heading");
const badge = document.getElementById("status-badge");
const emptyState = document.getElementById("empty-state");
const loadingState = document.getElementById("loading-state");
const result = document.getElementById("result");
const creditInputs = Array.from(document.querySelectorAll("[data-credit-metric]"));
const feedbackSection = document.getElementById("feedback-section");
const feedbackCategory = document.getElementById("feedback-category");
const feedbackConsent = document.getElementById("feedback-consent");
const feedbackSubmit = document.getElementById("feedback-submit");
const feedbackMessage = document.getElementById("feedback-message");
const demoHostname = typeof location !== "undefined" ? location.hostname.toLowerCase() : "localhost";
const publicDemo = !["localhost", "[::1]", "::1"].includes(demoHostname) && !/^127(?:\.\d{1,3}){3}$/.test(demoHostname);
let latestResponse = null;
let latestRequest = null;
let previousQuestion = null;
let answerSequence = 0;
let activeAnswerController = null;
let activeRefinementController = null;
let previousTranscriptQuestion = null;
let latestTurnKind = null;
let conversationTurns = [];
let generationReady = false;
const newConversation = document.getElementById("new-conversation");
const conversationMode = newConversation?.dataset?.conversationMode === "friendly";

function resetDialogueContext() {
  previousQuestion = null;
  previousTranscriptQuestion = null;
  answerSequence += 1;
  if (activeAnswerController) activeAnswerController.abort();
  if (activeRefinementController) activeRefinementController.abort();
  activeAnswerController = null;
  activeRefinementController = null;
}

function transcriptRevision() {
  return window.AcademicTranscript?.revision?.() ?? 0;
}

function renderConversationHistory() {
  if (!conversationMode) return;
  const history = document.getElementById("conversation-history");
  const turns = document.getElementById("conversation-turns");
  clearChildren(turns);
  conversationTurns.slice(0, -1).forEach(turn => {
    const entry = document.createElement("div"); entry.className = "conversation-turn";
    const prompt = document.createElement("strong"); prompt.textContent = turn.question;
    const reply = document.createElement("p"); reply.textContent = turn.answer;
    entry.append(prompt, reply); turns.appendChild(entry);
  });
  history.hidden = conversationTurns.length < 2;
}

function rememberTurn(value, answer, kind, sequence) {
  if (!conversationMode) return;
  conversationTurns.push({question: value, answer, kind, sequence});
  conversationTurns = conversationTurns.slice(-6);
  const current = document.getElementById("current-question");
  current.textContent = value; current.hidden = false;
  renderConversationHistory();
  updateContextNotice();
}

function updateContextNotice() {
  if (!conversationMode) return;
  const transcript = window.AcademicTranscript?.current?.();
  const parts = [];
  if (previousQuestion || previousTranscriptQuestion) parts.push("앞 질문에 이어 물어보실 수 있어요");
  if (transcript) parts.push("확인한 성적표로 비교할 수 있어요");
  document.getElementById("conversation-context").textContent = parts.length
    ? parts.join(" · ") + ". 새 대화에서는 입력과 기록을 지웁니다."
    : "이 탭에서만 대화를 기억합니다. 새 대화에서는 성적표와 입력 학점도 지웁니다.";
}

function dialogueRoute(value, snapshot, earnedCredits = {}) {
  if (!conversationMode) return "policy";
  const compact = value.normalize("NFKC").toLowerCase().replace(/[^0-9a-z가-힣]/g, "");
  const personalComparison = /성적표|이수내역|입력한기록|확인된기록|남은(?:전공)?필수과목|남아있는(?:전공)?필수과목|아직안들은|안들은|미이수(?:인)?과목/.test(compact)
    || /(?:내가|나는|저는|제가|내|제|나의|저의).*(?:부족|남았|남은|모자|더들|이수했|들었|충족했)/.test(compact);
  const gap = /부족|모자|남았|남아|남은학점|몇학점(?:을)?더|얼마나더/.test(compact);
  const contextOnly = /^(?:그럼|그러면|그건|그것은|그기준은)?(?:몇|얼마나|다시|무슨뜻|그과목)/.test(compact);
  if (personalComparison) {
    if (!snapshot && gap && previousQuestion && Object.keys(earnedCredits).length
      && !/성적표|이수내역|기록|필수과목|미이수과목/.test(compact)) return "policy";
    return snapshot ? "transcript" : "missing-transcript";
  }
  // Explicit aggregate inputs cannot be silently replaced by a transcript total.
  // Without a compatible anchor the policy endpoint asks which metric to compare.
  if (gap && Object.keys(earnedCredits).length) return "policy";
  if (latestTurnKind === "transcript" && contextOnly) return snapshot ? "transcript" : "missing-transcript";
  if (gap && snapshot && (!previousQuestion || latestTurnKind === "transcript")) return "transcript";
  return "policy";
}

function isContextLikeQuestion(value) {
  const compact = value.normalize("NFKC").toLowerCase().replace(/[^0-9a-z가-힣]/g, "");
  // Feedback replays a standalone core request, not chat context. Conservatively
  // exclude follow-up-shaped requests even when no anchor was supplied.
  return /^(?:그럼|그러면|그건|그것은|그기준은)?(?:몇|얼마나|학점기준|다시|무슨뜻)/.test(compact);
}

const statusLabels = {
  supported: "근거 확인",
  insufficient_evidence: "근거 부족",
  conflict: "규칙 충돌",
  out_of_scope: "지원 범위 밖",
};

const metricLabels = {
  "credits.general.balanced": "균형교양",
  "credits.general.foundation": "기초교양",
  "credits.general.remaining": "교양 잔여",
  "credits.general.total": "교양 합계",
  "credits.graduation.remaining": "졸업 잔여",
  "credits.graduation.total": "졸업 총학점",
  "credits.major.advanced": "심화전공",
  "credits.major.elective": "전공선택",
  "credits.major.minimum": "최소전공",
  "credits.major.required": "전공필수",
  "credits.major.total": "전공 합계",
};

function clearChildren(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function setText(id, value) {
  document.getElementById(id).textContent = String(value ?? "");
}

function showLoading() {
  panel.setAttribute("aria-busy", "true");
  submit.disabled = true;
  emptyState.hidden = true;
  result.hidden = true;
  loadingState.hidden = false;
  badge.className = "status-badge status-idle";
  badge.textContent = "확인 중";
}

function finishLoading() {
  panel.setAttribute("aria-busy", "false");
  submit.disabled = false;
  loadingState.hidden = true;
  result.hidden = false;
}

function makeListItem(primary, secondary) {
  const item = document.createElement("li");
  const title = document.createElement("strong");
  title.textContent = primary;
  item.appendChild(title);
  if (secondary) {
    const detail = document.createElement("span");
    detail.textContent = secondary;
    item.appendChild(detail);
  }
  return item;
}

function renderList(sectionId, listId, values, mapper) {
  const section = document.getElementById(sectionId);
  const list = document.getElementById(listId);
  clearChildren(list);
  values.forEach((value) => list.appendChild(mapper(value)));
  section.hidden = values.length === 0;
}

function renderCalculations(values) {
  const section = document.getElementById("calculations-section");
  const container = document.getElementById("calculations");
  clearChildren(container);
  values.forEach((calculation) => {
    const card = document.createElement("div");
    card.className = "calculation";
    const label = document.createElement("span");
    label.textContent = metricLabels[calculation.metric] || calculation.metric;
    const value = document.createElement("strong");
    value.textContent = `${calculation.gap}학점 부족`;
    const detail = document.createElement("span");
    detail.textContent = `기준 ${calculation.required} · 이수 ${calculation.earned}`;
    card.append(label, value, detail);
    container.appendChild(card);
  });
  section.hidden = values.length === 0;
}

function renderResponse(data) {
  finishLoading();
  const knownStatus = Object.prototype.hasOwnProperty.call(statusLabels, data.status);
  badge.className = `status-badge status-${knownStatus ? data.status : "error"}`;
  badge.textContent = knownStatus ? statusLabels[data.status] : "응답 오류";
  const packetForGeneration = data.evidence_packet || {};
  const expectedClaims = Array.isArray(packetForGeneration.applied_rules)
    ? packetForGeneration.applied_rules.map(rule => rule.rule_id) : [];
  const generatedClaims = data.generated_claim_ids;
  const generated = data.status === "supported" && packetForGeneration.status === "supported"
    && ["generated", "cached"].includes(data.generation_status)
    && typeof data.generated_answer === "string" && data.generated_answer.length > 0
    && data.generated_answer.length <= 2400 && expectedClaims.length > 0
    && Array.isArray(generatedClaims) && generatedClaims.length === expectedClaims.length
    && generatedClaims.every((id, index) => id === expectedClaims[index]);
  const presentation = typeof data.conversational_answer === "string" && data.conversational_answer.length > 0
    && data.conversational_answer.length <= 8000 && Array.isArray(data.presentation_claim_ids)
    && data.presentation_claim_ids.length === expectedClaims.length
    && data.presentation_claim_ids.every((id, index) => id === expectedClaims[index])
    && (!generated || data.conversational_answer.includes(data.generated_answer))
    && (data.status === "supported" ? packetForGeneration.status === "supported" && expectedClaims.length > 0
      : expectedClaims.length === 0 && data.status === packetForGeneration.status);
  setText("answer-text", presentation ? data.conversational_answer
    : generated ? data.generated_answer : data.answer || "답변을 표시할 수 없습니다.");
  const generationNote = document.getElementById("generation-note");
  if (generationNote) {
    generationNote.hidden = false;
    generationNote.textContent = generated
      ? (data.generation_status === "cached" ? "Gemma 작성 · 검증된 문장 재사용" : "Gemma 작성 · 근거 문장 검증 통과")
      : "검증된 규칙 답변 · 모델 문장 미사용";
  }
  setText("packet-id", data.packet_id ? `응답 ID · ${data.packet_id}` : "");
  const suggestionSection = document.getElementById("suggestion-section");
  const suggestion = data.status === "insufficient_evidence" && data.llm_status === "suggested"
    ? data.suggested_question : null;
  const choices = data.status === "insufficient_evidence" && Array.isArray(data.clarification_choices)
    ? data.clarification_choices.slice(0, 3).filter((choice) => choice && typeof choice.label === "string"
      && choice.label.length > 0 && choice.label.length <= 100 && typeof choice.question === "string"
      && choice.question.length > 0 && choice.question.length <= 500) : [];
  suggestionSection.hidden = !suggestion && choices.length === 0;
  const suggestionText = document.getElementById("suggestion-text");
  clearChildren(suggestionText);
  if (suggestion) suggestionText.textContent = suggestion;
  choices.forEach((choice) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary-button";
    button.textContent = choice.label;
    button.addEventListener("click", () => {
      resetDialogueContext();
      question.value = choice.question;
      question.dispatchEvent(new Event("input"));
      question.focus();
    });
    suggestionText.appendChild(button);
  });

  const packet = data.evidence_packet || {};
  const evidenceCounters = new Map();
  renderCalculations(Array.isArray(data.calculations) ? data.calculations : []);
  renderList("transcript-checks-section", "transcript-checks-list", [], () => null);
  renderList("evidence-section", "evidence-list", Array.isArray(packet.evidence) ? packet.evidence : [], (evidence) => {
    const item = makeListItem(evidence.claim || "근거", `${evidence.source_id || "출처 미상"} · ${evidence.locator || "위치 미상"}`);
    const index = evidenceCounters.get(evidence.rule_id) || 0;
    evidenceCounters.set(evidence.rule_id, index + 1);
    if (window.AcademicEvidence) item.appendChild(window.AcademicEvidence.button(evidence, index));
    return item;
  });
  renderList("rules-section", "rules-list", Array.isArray(packet.applied_rules) ? packet.applied_rules : [], (rule) =>
    makeListItem(rule.rule_id || "규칙", rule.rule_sha256 ? `검증값 ${rule.rule_sha256.slice(0, 16)}…` : "")
  );
  renderList("issues-section", "issues-list", Array.isArray(packet.issues) ? packet.issues : [], (issue) =>
    makeListItem(issue.message || "추가 확인이 필요합니다.", issue.kind ? `분류 · ${issue.kind}` : "")
  );
  latestResponse = data;
  feedbackSection.hidden = publicDemo || !latestRequest || !latestRequest.feedbackCompatible
    || !["insufficient_evidence", "conflict"].includes(data.status);
  feedbackConsent.checked = false;
  feedbackSubmit.disabled = true;
  feedbackMessage.textContent = "";
  heading.focus();
}

function renderError(message = "답변 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.") {
  resetDialogueContext();
  finishLoading();
  badge.className = "status-badge status-error";
  badge.textContent = "연결 오류";
  setText("answer-text", message);
  const generationNote = document.getElementById("generation-note");
  if (generationNote) { generationNote.hidden = true; generationNote.textContent = ""; }
  setText("packet-id", "");
  document.getElementById("suggestion-section").hidden = true;
  setText("suggestion-text", "");
  renderCalculations([]);
  renderList("transcript-checks-section", "transcript-checks-list", [], () => null);
  renderList("evidence-section", "evidence-list", [], () => null);
  renderList("rules-section", "rules-list", [], () => null);
  renderList("issues-section", "issues-list", [], () => null);
  latestResponse = null;
  latestRequest = null;
  latestTurnKind = null;
  feedbackSection.hidden = true;
  heading.focus();
}

function renderTranscriptResponse(data) {
  finishLoading();
  badge.className = `status-badge status-${data.status}`;
  badge.textContent = statusLabels[data.status] || "확인 필요";
  const text = typeof data.conversational_answer === "string" && data.conversational_answer.length <= 8000
    ? data.conversational_answer : data.answer;
  setText("answer-text", text || "이수내역의 확인할 항목을 구체적으로 질문해 주세요.");
  setText("generation-note", "확인한 성적표 · 규칙 기반 부분 비교 · 최종 졸업 인증 아님");
  document.getElementById("generation-note").hidden = false;
  setText("packet-id", "");
  document.getElementById("suggestion-section").hidden = true;
  setText("suggestion-text", "");
  renderCalculations([]);
  const checks = Array.isArray(data.selected_checks) ? data.selected_checks : [];
  renderList("transcript-checks-section", "transcript-checks-list", checks, check => {
    const supported = check.evidence_packet?.status === "supported";
    const label = supported ? ({met: "충족", not_met: "미충족"}[check.result] || "확인 필요") : "확인 필요";
    const unit = check.check_id === "general.balanced.area_coverage" ? "영역" : "학점";
    const detail = supported && Number.isInteger(check.gap)
      ? `기준 ${check.required}${unit} · 확인 ${check.earned}${unit} · 부족 ${check.gap}${unit}`
      : supported && Array.isArray(check.missing_courses) && check.missing_courses.length
        ? `남은 과목: ${check.missing_courses.join(", ")}` : check.note;
    const item = makeListItem(`${check.label} · ${label}`, detail);
    const packet = supported ? check.evidence_packet : check.policy_packet;
    if (packet?.status === "supported") {
      const indices = new Map();
      (packet.evidence || []).forEach(ref => {
        const citation = document.createElement("p");
        citation.textContent = `${supported ? "비교 근거" : "기준 설명만 · 개인 판정 근거 아님"} · ${ref.locator} · ${ref.claim}`;
        const index = indices.get(ref.rule_id) || 0; indices.set(ref.rule_id, index + 1);
        if (window.AcademicEvidence) citation.appendChild(window.AcademicEvidence.button(ref, index));
        item.appendChild(citation);
      });
    }
    return item;
  });
  renderList("evidence-section", "evidence-list", [], () => null);
  renderList("rules-section", "rules-list", [], () => null);
  renderList("issues-section", "issues-list", data.verification_items || [], item => makeListItem(item.message, item.action));
  latestResponse = null; latestRequest = null;
  feedbackSection.hidden = true; feedbackConsent.checked = false;
  feedbackSubmit.disabled = true; feedbackMessage.textContent = "";
  heading.focus();
}

function policyIdentity(data) {
  return JSON.stringify({packet_id: data.packet_id, status: data.status, answer: data.answer,
    intent_ids: data.intent_ids, calculations: data.calculations, evidence_packet: data.evidence_packet,
    context_question: data.context_question, context_used: data.context_used});
}

async function refinePolicy(payload, initial, sequence, revision) {
  const controller = new AbortController(); activeRefinementController = controller;
  const current = () => sequence === answerSequence && revision === transcriptRevision()
    && activeRefinementController === controller;
  setText("generation-note", "근거 확인 완료 · Gemma가 표현을 다듬는 중");
  try {
    const response = await fetch("/v1/academic/chat", {method: "POST",
      headers: {"Content-Type": "application/json"}, cache: "no-store", credentials: "omit",
      signal: controller.signal, body: JSON.stringify({...payload, generate_answer: true})});
    if (!current()) return;
    if (!response.ok) throw new Error("optional refinement unavailable");
    const refined = await response.json();
    if (!current()) return;
    const expected = initial.evidence_packet.applied_rules.map(rule => rule.rule_id);
    if (policyIdentity(refined) !== policyIdentity(initial)
      || !["generated", "cached"].includes(refined.generation_status)
      || !Array.isArray(refined.generated_claim_ids) || refined.generated_claim_ids.length !== expected.length
      || !refined.generated_claim_ids.every((id, index) => id === expected[index])
      || typeof refined.generated_answer !== "string" || !refined.generated_answer
      || refined.generated_answer.length > 2400
      || typeof refined.conversational_answer !== "string" || refined.conversational_answer.length > 8000
      || !refined.conversational_answer.includes(refined.generated_answer)
      || !Array.isArray(refined.presentation_claim_ids) || refined.presentation_claim_ids.length !== expected.length
      || !refined.presentation_claim_ids.every((id, index) => id === expected[index])) {
      setText("generation-note", "검증된 근거 안내 · 이번 답변은 모델 문장 미사용"); return;
    }
    // Optional wording cannot replace the first turn's decision, facts or evidence.
    renderResponse({...initial, generation_status: refined.generation_status,
      generated_answer: refined.generated_answer, generated_claim_ids: refined.generated_claim_ids,
      conversational_answer: refined.conversational_answer, presentation_claim_ids: refined.presentation_claim_ids});
    const turn = conversationTurns.find(item => item.sequence === sequence);
    if (turn) turn.answer = document.getElementById("answer-text").textContent;
  } catch (_error) {
    if (current()) setText("generation-note", "검증된 근거 안내 · 모델을 기다리지 않고 이용할 수 있어요");
  } finally {
    if (activeRefinementController === controller) activeRefinementController = null;
  }
}

function readCredits() {
  const credits = {};
  for (const input of creditInputs) {
    const raw = input.value.trim();
    if (!raw) continue;
    const value = Number(raw);
    if (!Number.isInteger(value) || value < 0 || value > 500) {
      input.setCustomValidity("0부터 500 사이의 정수 학점을 입력해 주세요.");
      input.reportValidity();
      input.setCustomValidity("");
      return null;
    }
    credits[input.dataset.creditMetric] = value;
  }
  return credits;
}

async function askAcademicQuestion(event) {
  event.preventDefault();
  const value = question.value.trim();
  if (!value) {
    resetDialogueContext();
    question.setCustomValidity("질문을 입력해 주세요.");
    question.reportValidity();
    question.setCustomValidity("");
    return;
  }
  const earnedCredits = readCredits();
  if (earnedCredits === null) {
    resetDialogueContext();
    return;
  }
  if (activeAnswerController) activeAnswerController.abort();
  if (activeRefinementController) activeRefinementController.abort();
  activeRefinementController = null;
  const sequence = ++answerSequence;
  const anchor = previousQuestion;
  const snapshot = conversationMode ? window.AcademicTranscript?.current?.() : null;
  const revision = transcriptRevision();
  const route = dialogueRoute(value, snapshot, earnedCredits);
  const refineReady = generationReady;
  if (route === "missing-transcript") {
    renderError("내 이수내역을 비교하려면 먼저 아래에서 성적표를 입력하고 확인해 주세요. 일반 기준은 성적표 없이도 물어보실 수 있어요.");
    badge.textContent = "이수내역 필요";
    updateContextNotice(); return;
  }
  if (route === "transcript" && value.length > 200) {
    renderError("성적표에 대한 질문은 200자 이내로 나누어 주세요. 개인 면제나 최종 졸업 인증은 확정하지 않습니다.");
    badge.textContent = "질문 확인"; return;
  }
  const controller = new AbortController();
  activeAnswerController = controller;
  const payload = {
    schema_version: "1.0.0", question: value, admission_year: 2026,
    matched_curriculum_year: 2026, department: "컴퓨터공학과",
    earned_credits: earnedCredits, previous_question: anchor,
    generate_answer: !conversationMode,
    ...(conversationMode ? {response_style: "friendly"} : {}),
  };
  showLoading();
  try {
    if (route === "transcript") {
      const response = await fetch("/v1/academic/transcripts/chat", {method: "POST",
        headers: {"Content-Type": "application/json"}, cache: "no-store", credentials: "omit",
        signal: controller.signal, body: JSON.stringify({question: value, transcript: snapshot,
          previous_question: previousTranscriptQuestion, response_style: "friendly"})});
      if (sequence !== answerSequence || revision !== transcriptRevision()) return;
      if (!response.ok) throw new Error("transcript request rejected");
      const data = await response.json();
      if (sequence !== answerSequence || revision !== transcriptRevision()) return;
      if (!data || !Object.hasOwn(statusLabels, data.status) || !Array.isArray(data.selected_checks)) throw new Error("invalid transcript reply");
      previousTranscriptQuestion = data.status === "supported" && typeof data.context_question === "string"
        ? data.context_question : null;
      previousQuestion = null; latestTurnKind = "transcript";
      renderTranscriptResponse(data);
      rememberTurn(value, document.getElementById("answer-text").textContent, "transcript", sequence);
      if (question.value.trim() === value) { question.value = ""; question.dispatchEvent(new Event("input")); }
      return;
    }
    const response = await fetch("/v1/academic/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      cache: "no-store",
      credentials: "omit",
      signal: controller.signal,
      body: JSON.stringify(payload),
    });
    if (sequence !== answerSequence || revision !== transcriptRevision()) return;
    if (!response.ok) throw new Error("request rejected");
    const data = await response.json();
    if (sequence !== answerSequence || revision !== transcriptRevision()) return;
    if (!data || typeof data !== "object" || !data.status || !data.evidence_packet) throw new Error("invalid response");
    previousQuestion = data.status === "supported" && data.evidence_packet.status === "supported"
      ? (conversationMode ? (data.context_question || null) : (data.context_used ? anchor : value)) : null;
    previousTranscriptQuestion = null; latestTurnKind = "policy";
    latestRequest = {
      question: value,
      earned_credits: {...earnedCredits},
      feedbackCompatible: anchor === null && !data.context_used
        && !(Array.isArray(data.clarification_choices) && data.clarification_choices.length)
        && !isContextLikeQuestion(value),
    };
    renderResponse(data);
    rememberTurn(value, document.getElementById("answer-text").textContent, "policy", sequence);
    if (conversationMode) {
      if (question.value.trim() === value) { question.value = ""; question.dispatchEvent(new Event("input")); }
      if (refineReady && data.status === "supported" && data.evidence_packet.status === "supported"
        && !data.context_used && data.evidence_packet.applied_rules.length > 0
        && data.evidence_packet.applied_rules.length <= 5) void refinePolicy(payload, data, sequence, revision);
    }
  } catch (_error) {
    if (sequence !== answerSequence) return;
    renderError();
  } finally {
    if (sequence === answerSequence) activeAnswerController = null;
  }
}

creditInputs.forEach((input) => input.addEventListener("input", () => {
  resetDialogueContext();
  if (latestResponse || panel.getAttribute("aria-busy") === "true") {
    renderError("이수학점이 변경되었습니다. 현재 입력으로 다시 질문해 주세요.");
    badge.textContent = "입력 변경";
  }
}));

question.addEventListener("input", () => {
  counter.textContent = `${question.value.length} / 500`;
});

question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelectorAll("[data-question]").forEach((button) => {
  button.addEventListener("click", () => {
    resetDialogueContext();
    question.value = button.dataset.question || "";
    question.dispatchEvent(new Event("input"));
    question.focus();
  });
});

form.addEventListener("submit", askAcademicQuestion);

function clearVisibleConversation() {
  resetDialogueContext();
  latestResponse = null; latestRequest = null; latestTurnKind = null;
  conversationTurns = [];
  question.value = ""; question.dispatchEvent(new Event("input"));
  creditInputs.forEach(input => { input.value = ""; input.setCustomValidity(""); });
  if (conversationMode) {
    window.AcademicTranscript?.clear?.();
    window.AcademicEvidence?.clear?.();
    renderConversationHistory();
    const current = document.getElementById("current-question"); current.textContent = ""; current.hidden = true;
  }
  panel.setAttribute("aria-busy", "false"); submit.disabled = false;
  loadingState.hidden = true; result.hidden = true; emptyState.hidden = false;
  setText("answer-text", ""); setText("packet-id", ""); setText("generation-note", "");
  document.getElementById("generation-note").hidden = true;
  badge.className = "status-badge status-idle"; badge.textContent = "새 대화";
  feedbackSection.hidden = true; feedbackConsent.checked = false; feedbackSubmit.disabled = true;
  feedbackMessage.textContent = "";
  renderCalculations([]);
  for (const [section, list] of [["evidence-section", "evidence-list"], ["rules-section", "rules-list"],
    ["issues-section", "issues-list"], ["transcript-checks-section", "transcript-checks-list"]]) renderList(section, list, [], () => null);
  document.getElementById("suggestion-section").hidden = true; setText("suggestion-text", "");
  updateContextNotice();
}

if (conversationMode) {
  newConversation.addEventListener("click", () => { clearVisibleConversation(); question.focus(); });
  window.addEventListener?.("academic-transcript-change", () => {
    const waiting = panel.getAttribute("aria-busy") === "true";
    const refining = activeRefinementController !== null;
    resetDialogueContext();
    conversationTurns = conversationTurns.filter(turn => turn.kind !== "transcript");
    if (latestTurnKind === "transcript") {
      latestTurnKind = null; latestResponse = null; latestRequest = null;
      result.hidden = true; emptyState.hidden = false; setText("answer-text", "");
      const current = document.getElementById("current-question"); current.textContent = ""; current.hidden = true;
      renderList("transcript-checks-section", "transcript-checks-list", [], () => null);
    }
    if (refining && latestTurnKind === "policy" && !waiting) {
      setText("generation-note", "검증된 근거 안내 · 입력 변경으로 문장 다듬기를 취소했어요");
    }
    if (waiting) { result.hidden = true; emptyState.hidden = false; }
    panel.setAttribute("aria-busy", "false"); submit.disabled = false; loadingState.hidden = true;
    renderConversationHistory(); updateContextNotice();
  });
  window.addEventListener?.("pagehide", clearVisibleConversation);
}

fetch("/v1/academic/runtime", {cache: "no-store", credentials: "omit"})
  .then(response => { if (!response.ok) throw new Error("unavailable"); return response.json(); })
  .then(state => {
    generationReady = state.llm_configured === true && state.llm_model_available === true
      && state.llm_mode === "grounded_answer_generation";
    const graph = state.graph_verified ? "Neo4j 근거 검증됨" : "승인 규칙 저장소 사용";
    const llm = state.llm_configured && state.llm_model_available
      ? (state.default_dialogue_mode === "semantic_retrieval" ? "Gemma 질문 이해·근거 기반 답변 연결 준비됨" : state.llm_mode === "grounded_answer_generation" ? "Gemma 문장 생성 연결 준비됨" : "LLM 질문 표현 제안용")
      : "모델 미연결 · 근거 답변 사용 가능";
    document.getElementById("runtime-state").textContent = `${graph} · ${llm}`;
  })
  .catch(() => { document.getElementById("runtime-state").textContent = "연결 상태를 확인할 수 없습니다."; });

feedbackConsent.addEventListener("change", () => {
  feedbackSubmit.disabled = publicDemo || !feedbackConsent.checked;
});

feedbackSubmit.addEventListener("click", async () => {
  if (publicDemo || !feedbackConsent.checked || !latestResponse || !latestRequest || !latestRequest.feedbackCompatible
    || !["insufficient_evidence", "conflict"].includes(latestResponse.status)) return;
  feedbackSubmit.disabled = true;
  feedbackMessage.textContent = "저장 중입니다.";
  try {
    const response = await fetch("/v1/academic/feedback", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      cache: "no-store",
      credentials: "omit",
      body: JSON.stringify({
        schema_version: "1.0.0",
        packet_id: latestResponse.packet_id,
        status: latestResponse.status,
        question: latestRequest.question,
        earned_credits: latestRequest.earned_credits,
        category: feedbackCategory.value,
        consent_to_store: true,
      }),
    });
    if (response.status !== 201) throw new Error("feedback rejected");
    const data = await response.json();
    if (!data.feedback_id || data.stored !== true) throw new Error("invalid feedback response");
    feedbackMessage.textContent = "보완 요청을 비공개 로컬 기록에 저장했습니다.";
  } catch (_error) {
    feedbackMessage.textContent = "저장하지 못했습니다. 개인정보 포함 여부를 확인한 뒤 다시 시도해 주세요.";
    feedbackSubmit.disabled = false;
  }
});
