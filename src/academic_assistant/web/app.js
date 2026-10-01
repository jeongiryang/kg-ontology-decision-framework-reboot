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
let latestResponse = null;
let latestRequest = null;
let previousQuestion = null;
let answerSequence = 0;
let activeAnswerController = null;

function resetDialogueContext() {
  previousQuestion = null;
  answerSequence += 1;
  if (activeAnswerController) activeAnswerController.abort();
  activeAnswerController = null;
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
  setText("answer-text", data.answer || "답변을 표시할 수 없습니다.");
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
  feedbackSection.hidden = !latestRequest || !latestRequest.feedbackCompatible
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
  setText("packet-id", "");
  document.getElementById("suggestion-section").hidden = true;
  setText("suggestion-text", "");
  renderCalculations([]);
  renderList("evidence-section", "evidence-list", [], () => null);
  renderList("rules-section", "rules-list", [], () => null);
  renderList("issues-section", "issues-list", [], () => null);
  latestResponse = null;
  latestRequest = null;
  feedbackSection.hidden = true;
  heading.focus();
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
  const sequence = ++answerSequence;
  const anchor = previousQuestion;
  const controller = new AbortController();
  activeAnswerController = controller;
  showLoading();
  try {
    const response = await fetch("/v1/academic/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      cache: "no-store",
      credentials: "omit",
      signal: controller.signal,
      body: JSON.stringify({
        schema_version: "1.0.0",
        question: value,
        admission_year: 2026,
        matched_curriculum_year: 2026,
        department: "컴퓨터공학과",
        earned_credits: earnedCredits,
        previous_question: anchor,
      }),
    });
    if (sequence !== answerSequence) return;
    if (!response.ok) throw new Error("request rejected");
    const data = await response.json();
    if (sequence !== answerSequence) return;
    if (!data || typeof data !== "object" || !data.status || !data.evidence_packet) throw new Error("invalid response");
    previousQuestion = data.status === "supported" && data.evidence_packet.status === "supported"
      ? (data.context_used ? anchor : value) : null;
    latestRequest = {
      question: value,
      earned_credits: {...earnedCredits},
      feedbackCompatible: anchor === null && !data.context_used
        && !(Array.isArray(data.clarification_choices) && data.clarification_choices.length)
        && !isContextLikeQuestion(value),
    };
    renderResponse(data);
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

fetch("/v1/academic/runtime", {cache: "no-store", credentials: "omit"})
  .then(response => { if (!response.ok) throw new Error("unavailable"); return response.json(); })
  .then(state => {
    const graph = state.graph_verified ? "Neo4j 근거 검증됨" : "승인 규칙 저장소 사용";
    const llm = state.llm_configured && state.llm_model_available ? "LLM 연결 확인 · 질문 표현 제안용" : "LLM 미사용 · 규칙 답변 사용 가능";
    document.getElementById("runtime-state").textContent = `${graph} · ${llm}`;
  })
  .catch(() => { document.getElementById("runtime-state").textContent = "연결 상태를 확인할 수 없습니다."; });

feedbackConsent.addEventListener("change", () => {
  feedbackSubmit.disabled = !feedbackConsent.checked;
});

feedbackSubmit.addEventListener("click", async () => {
  if (!feedbackConsent.checked || !latestResponse || !latestRequest || !latestRequest.feedbackCompatible
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
