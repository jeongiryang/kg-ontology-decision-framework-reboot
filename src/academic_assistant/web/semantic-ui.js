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
  let progress = null;
  const button = form.querySelector("button[type='submit']");
  const node = (tag, text, cls) => { const n=document.createElement(tag); if(text !== undefined) n.textContent=text; if(cls) n.className=cls; return n; };
  const stageLabels = {received:"요청 접수",intent:"질문 이해",query:"조회 실행",retrieval:"근거 확인",writing:"답변 작성",verification:"답변 검증",repair:"답변 다시 작성",complete:"처리 완료",error:"처리 오류"};
  const stateLabels = {started:"시작",completed:"완료",failed:"실패",info:"안내"};
  const detailLabels = {plan:"조회 계획",cypher:"실행한 Cypher",parameters:"조회 매개변수",backend:"조회 저장소",result_count:"조회 결과 개수",result_summary:"조회 결과 요약",summary:"결과 요약",failure_code:"실패 코드",reason_code:"처리 코드",attempt:"시도",failure:"확인한 실패",validation_failure:"검증 결과"};
  const now = () => typeof performance !== "undefined" && typeof performance.now === "function" ? performance.now() : Date.now();
  const validTime = value => typeof value === "number" && Number.isFinite(value) && value >= 0;
  const formatTime = value => `${(value/1000).toFixed(3)}초 · ${Number(value.toFixed(3))}ms`;

  // transcript.js sets the public-host disclosure before this deferred script.
  // Only the question prototype changed; transcript processing stays local.
  el("feedback-privacy-note").textContent="이름·학번이 포함된 질문도 연구실 Gemma에서 처리할 수 있으며 자동 저장하지 않습니다. 성적표·개인 이수내역·입력 학점은 LLM으로 보내지 않습니다. 새 대화 또는 페이지를 떠나면 이 탭의 대화와 입력을 지웁니다.";

  function stopClock(session) {
    if(session?.timer !== null && session?.timer !== undefined) clearInterval(session.timer);
    if(session) session.timer=null;
  }
  function cancelReader(session) {
    const reader=session?.reader;
    if(session) session.reader=null;
    if(reader) {
      try { reader.cancel().catch(()=>{}); } catch(_error) { /* Abort is already requested. */ }
    }
  }
  function clearProgress() {
    stopClock(progress); cancelReader(progress); progress=null;
    el("assistant-stages").replaceChildren(); el("assistant-progress").hidden=true;
    const trace=el("assistant-trace");
    if(trace) trace.open=false;
    el("cancel-question").hidden=true;
    el("assistant-elapsed").textContent="전체 대기 0.000초 · 0ms";
    el("assistant-progress-note").textContent="단계별 실제 진행 정보가 도착하면 표시합니다.";
  }
  function startProgress(isCurrent) {
    const session={startedAt:now(),timer:null,reader:null,requestId:null,lastSequence:0,terminalElapsed:null};
    progress=session;
    el("assistant-progress").hidden=false; el("cancel-question").hidden=false;
    el("assistant-progress-title").textContent="질문 처리 중";
    const update=()=> {
      if(progress===session && isCurrent()) el("assistant-elapsed").textContent=`전체 대기 ${formatTime(Math.max(0,now()-session.startedAt))}`;
    };
    update();
    if(typeof setInterval === "function") session.timer=setInterval(update,50);
    return session;
  }
  function finishProgress(session, failed=false) {
    if(progress !== session) return;
    stopClock(session);
    el("cancel-question").hidden=true;
    const serverMeasured=validTime(session.terminalElapsed);
    el("assistant-elapsed").textContent=`${serverMeasured ? "서버 처리" : "전체 대기"} ${formatTime(serverMeasured ? session.terminalElapsed : Math.max(0,now()-session.startedAt))}`;
    el("assistant-progress-title").textContent=failed ? "질문 처리 실패" : "응답 확인";
    el("assistant-progress-note").textContent=failed
      ? "처리가 끝나지 않았습니다. 도착한 단계 정보만 표시합니다. 다시 질문할 수 있습니다."
      : session.requestId ? "서버가 기록한 실제 단계와 시간입니다. 각 항목을 펼치면 조회 상세를 확인할 수 있습니다."
      : "JSON 응답을 받았습니다. 서버의 단계별 진행 정보는 제공되지 않았습니다.";
  }
  function appendProgress(record, session) {
    if(record.schema_version !== "1.0.0" || typeof record.request_id !== "string" || !record.request_id.length || record.request_id.length>100
      || !Number.isSafeInteger(record.sequence) || record.sequence<=session.lastSequence
      || !Object.hasOwn(stageLabels,record.stage) || !Object.hasOwn(stateLabels,record.state)
      || !validTime(record.elapsed_ms) || !(record.duration_ms === null || validTime(record.duration_ms))
      || typeof record.message !== "string" || !record.message.length || record.message.length>2000
      || !["system","llm"].includes(record.explanation_source)
      || !record.details || typeof record.details !== "object" || Array.isArray(record.details)
      || (session.requestId !== null && session.requestId !== record.request_id)) throw new Error("stream_invalid");
    session.requestId=record.request_id; session.lastSequence=record.sequence;
    const item=node("li"), details=node("details",undefined,"assistant-stage"), summary=node("summary");
    details.open=false; details.dataset.state=record.state;
    summary.append(node("span",`${stageLabels[record.stage]} · ${stateLabels[record.state]}`,"stage-title"),node("span",record.message,"stage-message"));
    summary.append(node("span",`서버 경과 ${formatTime(record.elapsed_ms)} · ${record.duration_ms === null ? "단계 소요 미제공" : `단계 소요 ${formatTime(record.duration_ms)}`} · ${record.explanation_source === "llm" ? "모델 설명" : "시스템 설명"}`,"stage-meta"));
    details.append(summary);
    const body=node("div",undefined,"stage-details");
    for(const [key,value] of Object.entries(record.details)) {
      body.append(node("h4",Object.hasOwn(detailLabels,key) ? detailLabels[key] : key));
      body.append(node("pre",typeof value === "string" ? value : JSON.stringify(value,null,2)));
    }
    if(!body.children.length) body.append(node("p","이 단계에는 추가 조회 상세가 없습니다."));
    details.append(body); item.append(details); el("assistant-stages").append(item);
    el("loading-message").textContent=record.message;
    el("status-badge").textContent=stageLabels[record.stage];
    el("assistant-progress-note").textContent=`${record.explanation_source === "llm" ? "모델 설명" : "시스템 설명"} · ${record.message}`;
  }
  async function readStream(response, session, isCurrent) {
    if(!response.body || typeof response.body.getReader !== "function") throw new Error("stream_incomplete");
    const reader=response.body.getReader(), decoder=new TextDecoder("utf-8",{fatal:true});
    session.reader=reader;
    let buffer="", terminal=null;
    function consume(line) {
      if(!line.trim()) return;
      if(terminal) throw new Error("stream_invalid");
      let record;
      try { record=JSON.parse(line); } catch(_error) { throw new Error("stream_invalid"); }
      if(!record || typeof record !== "object" || Array.isArray(record)) throw new Error("stream_invalid");
      if(record.type === "progress") appendProgress(record,session);
      else if(record.type === "result" && validTime(record.elapsed_ms) && record.response && typeof record.response === "object") {
        session.terminalElapsed=record.elapsed_ms; terminal=record;
      } else if(record.type === "error" && validTime(record.elapsed_ms) && typeof record.message === "string" && record.message.length && record.message.length<=2000) {
        session.terminalElapsed=record.elapsed_ms; terminal=record;
      } else throw new Error("stream_invalid");
    }
    function consumeLines(final=false) {
      let end;
      while((end=buffer.indexOf("\n")) !== -1) {
        if(end>1048576) throw new Error("stream_invalid");
        const line=buffer.slice(0,end); buffer=buffer.slice(end+1); consume(line);
      }
      if(buffer.length>1048576) throw new Error("stream_invalid");
      if(final && buffer.trim()) {consume(buffer);buffer="";}
    }
    try {
      while(!terminal) {
        const chunk=await reader.read();
        if(!isCurrent()) return null;
        buffer+=chunk.done ? decoder.decode() : decoder.decode(chunk.value,{stream:true});
        consumeLines(chunk.done);
        if(chunk.done) break;
      }
      if(!terminal) throw new Error("stream_incomplete");
      if(terminal.type === "error") {
        const error=new Error("stream_failure"); error.publicMessage=terminal.message; throw error;
      }
      return terminal.response;
    } finally {
      if(session.reader === reader) cancelReader(session);
      try { reader.releaseLock?.(); } catch(_error) { /* A canceled pending read can release later. */ }
    }
  }

  function cancel() {
    sequence += 1;
    if(controller) controller.abort();
    controller=null;
    clearProgress();
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
  function loading(question) {
    hideOldSections();
    el("empty-state").hidden=true; el("result").hidden=true; el("loading-state").hidden=false;
    el("loading-message").textContent="서버의 진행 정보를 기다리고 있습니다.";
    el("status-badge").textContent="요청 중";
    el("status-badge").className="status-badge status-idle";
    el("current-question").textContent=question; el("current-question").hidden=false;
    el("generation-note").hidden=true; el("packet-id").textContent="";
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
    el("status-badge").textContent=data.kind === "greeting" ? "대화" : data.reason_code === "processing_unavailable" ? "질문 처리 실패" : statusLabels[data.status];
    el("status-badge").className=`status-badge status-${data.reason_code === "processing_unavailable" ? "error" : data.kind === "greeting" ? "supported" : data.status}`;
    el("generation-note").hidden=false;
    el("generation-note").textContent=data.reason_code === "processing_unavailable" ? "질문 처리 실패 · 아래에서 조회 사실과 근거를 확인할 수 있습니다"
      : data.generation_status === "generated"
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
      if(!Number.isSafeInteger(value) || value<0 || value>500) {cancel();input.focus(); showFailure("학점은 0~500 사이의 정수로 입력해 주세요.");return;}
      credits[input.dataset.creditMetric]=value;
    }
    cancel(); const token=sequence; const requestController=new AbortController(); controller=requestController;
    const revision=window.AcademicTranscript?.revision?.();
    const isCurrent=()=>token === sequence && revision === window.AcademicTranscript?.revision?.() && !requestController.signal.aborted;
    const request={question,admission_year:2026,matched_curriculum_year:2026,department:"컴퓨터공학과",earned_credits:credits,
      previous_question:previous,transcript:window.AcademicTranscript?.current?.() || null};
    loading(question);
    const session=startProgress(isCurrent);
    try {
      const response=await fetch("/v1/academic/assistant",{method:"POST",headers:{"Content-Type":"application/json","Accept":"application/x-ndjson"},cache:"no-store",credentials:"omit",signal:requestController.signal,body:JSON.stringify(request)});
      if(!isCurrent()) return;
      if(!response.ok) throw new Error(response.status === 422 ? "invalid_input" : "processing_failure");
      const streamed=/^application\/x-ndjson(?:\s*;|$)/i.test(response.headers?.get?.("Content-Type") || "");
      const data=streamed ? await readStream(response,session,isCurrent) : await response.json();
      if(!isCurrent()) return;
      render(data,question);
      finishProgress(session,data.reason_code === "processing_unavailable");
      previous=data.context_question || (data.kind === "academic" ? question : previous);
      turns.push({question,answer:data.answer}); turns=turns.slice(-6);
      const list=el("conversation-turns"); list.replaceChildren();
      for(const turn of turns) {const item=node("li");item.append(node("strong",turn.question),node("p",turn.answer));list.append(item);}
      el("conversation-history").hidden=false;
      el("conversation-context").textContent="같은 대화의 앞 질문과 현재 확인된 이수내역을 참고합니다. 새 대화에서 지워집니다.";
      if(el("question").value.trim() === question) {el("question").value="";el("question").dispatchEvent(new Event("input"));}
    } catch(error) {
      if(isCurrent() && error.name !== "AbortError") {
        showFailure(error.publicMessage || (error.message === "invalid_input"
          ? "질문 길이와 학점 입력 범위를 확인해 주세요."
          : error.message === "stream_incomplete" ? "답변을 받기 전에 연결이 끝났습니다. 잠시 후 다시 질문해 주세요."
          : "지금 질문 처리를 마치지 못했습니다. 교육과정에 근거가 없다는 뜻은 아니에요. 잠시 후 다시 질문해 주세요."));
        finishProgress(session,true);
      }
    } finally {
      stopClock(session);
      if(token === sequence) {controller=null;button.disabled=false;el("cancel-question").hidden=true;el("answer-panel").setAttribute("aria-busy","false");}
    }
  }
  form.addEventListener("submit",ask,true);
  el("cancel-question").addEventListener("click",()=> {
    if(!controller) return;
    cancel(); showFailure("요청을 취소했습니다. 입력한 질문으로 다시 요청할 수 있어요.");
    el("status-badge").textContent="요청 취소";
    el("generation-note").textContent="요청을 취소했습니다.";
  });
  el("new-conversation").addEventListener("click",clear,true);
  window.addEventListener("academic-transcript-change",clear);
  document.querySelectorAll("[data-credit-metric]").forEach(input=>input.addEventListener("input",clear));
  window.addEventListener("pagehide",clear);
})();
