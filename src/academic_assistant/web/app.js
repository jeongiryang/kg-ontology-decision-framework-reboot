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

  const packet = data.evidence_packet || {};
  renderCalculations(Array.isArray(data.calculations) ? data.calculations : []);
  renderList("evidence-section", "evidence-list", Array.isArray(packet.evidence) ? packet.evidence : [], (evidence) =>
    makeListItem(evidence.claim || "근거", `${evidence.source_id || "출처 미상"} · ${evidence.locator || "위치 미상"}`)
  );
  renderList("rules-section", "rules-list", Array.isArray(packet.applied_rules) ? packet.applied_rules : [], (rule) =>
    makeListItem(rule.rule_id || "규칙", rule.rule_sha256 ? `검증값 ${rule.rule_sha256.slice(0, 16)}…` : "")
  );
  renderList("issues-section", "issues-list", Array.isArray(packet.issues) ? packet.issues : [], (issue) =>
    makeListItem(issue.message || "추가 확인이 필요합니다.", issue.kind ? `분류 · ${issue.kind}` : "")
  );
  heading.focus();
}

function renderError() {
  finishLoading();
  badge.className = "status-badge status-error";
  badge.textContent = "연결 오류";
  setText("answer-text", "답변 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.");
  setText("packet-id", "");
  renderCalculations([]);
  renderList("evidence-section", "evidence-list", [], () => null);
  renderList("rules-section", "rules-list", [], () => null);
  renderList("issues-section", "issues-list", [], () => null);
  heading.focus();
}

async function askAcademicQuestion(event) {
  event.preventDefault();
  const value = question.value.trim();
  if (!value) {
    question.setCustomValidity("질문을 입력해 주세요.");
    question.reportValidity();
    question.setCustomValidity("");
    return;
  }
  showLoading();
  try {
    const response = await fetch("/v1/academic/answers", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      cache: "no-store",
      credentials: "omit",
      body: JSON.stringify({
        schema_version: "1.0.0",
        question: value,
        admission_year: 2026,
        matched_curriculum_year: 2026,
        department: "컴퓨터공학과",
        earned_credits: {},
      }),
    });
    const data = await response.json();
    if (!data || typeof data !== "object" || !data.status || !data.evidence_packet) throw new Error("invalid response");
    renderResponse(data);
  } catch (_error) {
    renderError();
  }
}

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
    question.value = button.dataset.question || "";
    question.dispatchEvent(new Event("input"));
    question.focus();
  });
});

form.addEventListener("submit", askAcademicQuestion);
