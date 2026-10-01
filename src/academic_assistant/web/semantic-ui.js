"use strict";
// One default dialogue path. Legacy APIs are retained for integrations, not used
// as a question whitelist in this screen. All conversation state is tab memory.
(() => {
  const el = id => document.getElementById(id);
  const form = el("question-form");
  if (!form || el("new-conversation")?.dataset.assistantMode !== "semantic") return;
  const statusLabels = {supported:"근거 확인", insufficient_evidence:"추가 확인", conflict:"근거 충돌", out_of_scope:"지원 범위 안내"};
  let previous = null;
  let turns = [];
  let sequence = 0;
  let controller = null;
  const button = form.querySelector("button[type='submit']");
  const node = (tag, text, cls) => { const n=document.createElement(tag); if(text !== undefined) n.textContent=text; if(cls) n.className=cls; return n; };

  function cancel() {
    sequence += 1;
    if(controller) controller.abort();
    controller=null;
    button.disabled=false;
    el("answer-panel").setAttribute("aria-busy", "false");
  }
  function clear() {
    cancel(); previous=null; turns=[];
    hideOldSections();
    el("answer-text").textContent=""; el("current-question").textContent=""; el("current-question").hidden=true;
    el("result").hidden=true; el("loading-state").hidden=true; el("empty-state").hidden=false;
    el("generation-note").hidden=true; el("packet-id").textContent="";
    el("status-badge").textContent="질문을 기다립니다"; el("status-badge").className="status-badge status-idle";
    el("conversation-turns").replaceChildren();
    el("conversation-history").hidden=true;
    el("conversation-context").textContent="이 대화에서만 앞 질문과 확인된 이수내역을 참고합니다.";
  }
  function hideOldSections() {
    for(const id of ["suggestion-section","calculations-section","evidence-section","transcript-checks-section","rules-section","issues-section","feedback-section"]) el(id).hidden=true;
    el("semantic-parts")?.remove();
  }
  function loading() {
    hideOldSections();
    el("empty-state").hidden=true; el("result").hidden=true; el("loading-state").hidden=false;
    el("loading-state").textContent="질문을 이해하고 교육과정 근거를 찾아 답변하는 중입니다…";
    el("status-badge").textContent="질문 분석 중";
    el("status-badge").className="status-badge status-idle";
    el("answer-panel").setAttribute("aria-busy", "true");
    button.disabled=true;
  }
  function showFailure(text) {
    hideOldSections();
    el("empty-state").hidden=true; el("loading-state").hidden=true; el("result").hidden=false;
    el("answer-text").textContent=text;
    el("status-badge").textContent="연결 확인 필요";
    el("status-badge").className="status-badge status-error";
    el("generation-note").hidden=false;
    el("generation-note").textContent="질문 처리 실패 · 학사 근거가 없다는 뜻은 아닙니다";
  }
  function render(data, capturedQuestion) {
    if(!data || !Object.hasOwn(statusLabels,data.status) || !Array.isArray(data.parts) || typeof data.answer !== "string") throw new Error("invalid dialogue response");
    hideOldSections();
    el("empty-state").hidden=true; el("loading-state").hidden=true; el("result").hidden=false;
    el("answer-text").textContent=data.answer;
    el("current-question").textContent=capturedQuestion; el("current-question").hidden=false;
    el("status-badge").textContent=data.kind === "greeting" ? "대화" : data.reason_code === "processing_unavailable" ? "질문 처리 대기" : statusLabels[data.status];
    el("status-badge").className=`status-badge status-${data.kind === "greeting" ? "supported" : data.status}`;
    el("generation-note").hidden=false;
    el("generation-note").textContent=data.generation_status === "generated"
      ? "Gemma 질문 이해 · 근거 조회 · 자연어 답변"
      : data.plan_status === "generated" ? (data.kind === "greeting" ? "Gemma 질문 이해 · 대화 안내" : "Gemma 질문 이해 · 검증된 근거 안내") : "모델 처리 불가 · 확인 가능한 근거만 안내";
    const holder=node("div",undefined,"semantic-parts"); holder.id="semantic-parts";
    const answerText=data.answer.replace(/\s+/g," ").trim();
    for(const part of data.parts) {
      const section=node("section",undefined,"detail-section");
      section.append(node("h3",part.title));
      const packet=part.evidence_packet || part.course_evidence;
      const partText=typeof part.text === "string" ? part.text.replace(/\s+/g," ").trim() : "";
      if(part.status !== "supported" && partText && !answerText.includes(partText)) section.append(node("p",part.text));
      if(part.course_evidence?.status === "supported") {
        const courses=part.course_evidence.courses;
        const directPreview=courses.length > 0 && courses.length <= 4 && courses.every(fact =>
          (fact.course_name && data.answer.includes(fact.course_name)) || (fact.course_code && data.answer.includes(fact.course_code)));
        const previews=node("div",undefined,"course-source-previews");
        const details=node("details");
        details.open=false;
        details.append(node("summary",`과목 전체 상세 · ${courses.length}과목`));
        const list=node("ul",undefined,"detail-list");
        for(const fact of courses) {
          const li=node("li");
          li.append(node("strong",`${fact.course_name} (${fact.course_code}) · ${fact.credits}학점`));
          li.append(node("p",`${fact.category === "major_required" ? "전공필수" : "전공선택"} · ${fact.offering_label || "편성 정보 확인 필요"}`));
          if(window.AcademicEvidence) {
            const preview=window.AcademicEvidence.button({rule_id:fact.course_id,source_id:fact.source_id});
            if(directPreview) {
              if(preview.tagName === "BUTTON") {
                preview.textContent=`${fact.course_name} · 근거 PDF 보기`;
                preview.setAttribute("aria-label",`${fact.course_name} 근거 PDF 보기`);
              }
              previews.append(preview);
            } else li.append(preview);
          }
          list.append(li);
        }
        if(previews.children.length) section.append(previews);
        details.append(list); section.append(details);
      }
      if(packet?.status === "supported" && part.evidence_packet) {
        const details=node("details"); details.open=false;
        details.append(node("summary",`규칙 근거 · ${packet.evidence.length}개`));
        const refs=node("ul",undefined,"detail-list");
        const indices=new Map();
        for(const ref of packet.evidence) {
          const li=node("li"); li.append(node("p",ref.locator));
          const index=indices.get(ref.rule_id)||0; indices.set(ref.rule_id,index+1);
          if(window.AcademicEvidence) li.append(window.AcademicEvidence.button(ref,index));
          refs.append(li);
        }
        details.append(refs); section.append(details);
      }
      if(part.calculations?.length) {
        const details=node("details"); details.open=false;
        details.append(node("summary",`계산 상세 · ${part.calculations.length}개`));
        for(const calc of part.calculations) details.append(node("p",`기준 ${calc.required}학점 · 입력 ${calc.earned}학점 · 부족 ${calc.gap}학점`));
        section.append(details);
      }
      if(section.children.length > 1) holder.append(section);
    }
    el("result").append(holder);
    el("packet-id").textContent=data.packet_id;
  }
  async function ask(event) {
    event.preventDefault(); event.stopImmediatePropagation();
    const question=el("question").value.trim();
    if(!question) {el("question").focus();return;}
    const credits={};
    for(const input of document.querySelectorAll("[data-credit-metric]")) {
      if(!input.value.trim()) continue;
      const value=Number(input.value);
      if(!Number.isSafeInteger(value) || value<0 || value>500) {input.focus(); showFailure("학점은 0~500 사이의 정수로 입력해 주세요.");return;}
      credits[input.dataset.creditMetric]=value;
    }
    cancel(); const token=sequence; const requestController=new AbortController(); controller=requestController;
    const revision=window.AcademicTranscript?.revision?.();
    const request={question,admission_year:2026,matched_curriculum_year:2026,department:"컴퓨터공학과",earned_credits:credits,
      previous_question:previous,transcript:window.AcademicTranscript?.current?.() || null};
    loading();
    try {
      const response=await fetch("/v1/academic/assistant",{method:"POST",headers:{"Content-Type":"application/json"},cache:"no-store",credentials:"omit",signal:requestController.signal,body:JSON.stringify(request)});
      if(token !== sequence || revision !== window.AcademicTranscript?.revision?.()) return;
      if(!response.ok) throw new Error(response.status === 422 ? "invalid_input" : "processing_failure");
      const data=await response.json();
      if(token !== sequence || revision !== window.AcademicTranscript?.revision?.()) return;
      render(data,question);
      previous=data.context_question || (data.kind === "academic" ? question : previous);
      turns.push({question,answer:data.answer}); turns=turns.slice(-6);
      const list=el("conversation-turns"); list.replaceChildren();
      for(const turn of turns) {const item=node("li");item.append(node("strong",turn.question),node("p",turn.answer));list.append(item);}
      el("conversation-history").hidden=false;
      el("conversation-context").textContent="같은 대화의 앞 질문과 현재 확인된 이수내역을 참고합니다. 새 대화에서 지워집니다.";
      if(el("question").value.trim() === question) {el("question").value="";el("question").dispatchEvent(new Event("input"));}
    } catch(error) {
      if(token === sequence && error.name !== "AbortError") showFailure(error.message === "invalid_input"
        ? "입력 내용을 확인해 주세요. 이름·학번·연락처는 질문에 넣지 마세요."
        : "지금 질문 처리 연결을 확인하지 못했습니다. 교육과정에 근거가 없다는 뜻은 아니에요. 잠시 후 다시 질문해 주세요.");
    } finally {
      if(token === sequence) {controller=null;button.disabled=false;el("answer-panel").setAttribute("aria-busy","false");}
    }
  }
  form.addEventListener("submit",ask,true);
  el("new-conversation").addEventListener("click",clear,true);
  window.addEventListener("academic-transcript-change",clear);
  document.querySelectorAll("[data-credit-metric]").forEach(input=>input.addEventListener("input",clear));
  window.addEventListener("pagehide",clear);
})();
