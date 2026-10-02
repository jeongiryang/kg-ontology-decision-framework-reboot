"use strict";
(() => {
  const byId = (id) => document.getElementById(id);
  const body = byId("transcript-rows");
  const message = byId("transcript-message");
  const review = byId("transcript-review");
  const output = byId("transcript-results");
  const confirm = byId("transcript-confirmed");
  const complete = byId("transcript-complete");
  let detectedYear = null;
  let generation = 0;
  let followupSequence = 0;
  let latest = null;
  let busy = false;
  let activeController = null;
  let activeFollowupController = null;
  let previousQuestion = null;
  const friendlyUI = byId("new-conversation")?.dataset?.conversationMode === "friendly";
  const semanticUI = byId("new-conversation")?.dataset?.assistantMode === "semantic";
  const checkSections = new Map();
  const checkLabels = new Map();
  const reviewFlags = {retake:"재수강 확인 필요", equivalence:"동일·대체 확인 필요", retroactivity:"소급 적용 확인 필요", recognition_unverified:"개인 인정 미확인"};
  const categories = {unknown:"미확인", foundation:"기초교양", balanced:"균형교양", expanded:"확대교양", major_required:"전공필수", major_elective:"전공선택", free:"자유선택"};
  const areas = {"":"미확인/해당 없음", "digital-communication":"디지털커뮤니케이션", "humanities-arts":"인문예술", "society-culture":"사회와문화", "nature-science-technology":"자연·과학·기술의이해"};
  const grades = {"":"미확인", "A+":"A+", A0:"A0", "B+":"B+", B0:"B0", "C+":"C+", C0:"C0", "D+":"D+", D0:"D0", F:"F", F0:"F0", S:"S", U:"U", P:"P", PASS:"PASS", W:"W"};
  function notifyRevision() {
    if (typeof window.dispatchEvent === "function" && typeof Event === "function") {
      window.dispatchEvent(new Event("academic-transcript-change"));
    }
  }
  function invalidate() {
    generation += 1;
    followupSequence += 1;
    if(activeController) activeController.abort();
    if(activeFollowupController) activeFollowupController.abort();
    activeController = null; activeFollowupController = null; busy = false;
    byId("transcript-extract").disabled = false;
    latest = null;
    previousQuestion = null;
    checkSections.clear(); checkLabels.clear();
    output.replaceChildren(); output.hidden = true;
    byId("transcript-followups").hidden = true;
    byId("transcript-chat").textContent = "";
    questionInput.value = "";
    enable();
    notifyRevision();
  }
  function enable() { byId("transcript-assess").disabled = busy || !confirm.checked || detectedYear !== 2026 || body.children.length === 0; }
  function clear() {
    invalidate(); body.replaceChildren(); review.hidden = true; confirm.checked = false; complete.checked = false;
    detectedYear = null; byId("transcript-file").value = ""; message.textContent = "이수내역을 메모리에서 지웠습니다."; enable();
    byId("transcript-degree").value="unknown";
    byId("transcript-page").value="";
  }
  function inputCell(row, field, value, type="text") {
    const td = document.createElement("td"); const input = document.createElement("input");
    input.type = type; input.value = value ?? ""; input.dataset.field = field; input.setAttribute("aria-label", field);
    if(type === "number") { input.min = "0"; input.max = "30"; input.step = "1"; }
    else input.maxLength = field === "course_name" ? 100 : 16;
    td.append(input); row.append(td);
  }
  function selectCell(row, field, values, value) {
    const td = document.createElement("td"); const select = document.createElement("select");
    select.dataset.field = field; select.setAttribute("aria-label", field);
    for(const [key,label] of Object.entries(values)) { const opt = document.createElement("option"); opt.value = key; opt.textContent = label; select.append(opt); }
    select.value = value ?? ""; td.append(select); row.append(td);
  }
  function add(course = {}) {
    if(body.children.length >= 250) return;
    const row = document.createElement("tr");
    row.dataset.rowId = course.row_id || "";
    row.dataset.term = course.term || "";
    inputCell(row, "course_name", course.course_name); inputCell(row,"course_code",course.course_code);
    inputCell(row,"credits",course.credits,"number"); selectCell(row,"grade",grades,course.grade);
    selectCell(row,"category",categories,course.category || "unknown"); selectCell(row,"balanced_area",areas,course.balanced_area);
    const td = document.createElement("td"); const excluded = document.createElement("input"); excluded.type = "checkbox"; excluded.dataset.field = "excluded"; excluded.checked = !!course.excluded; excluded.setAttribute("aria-label","학점 계산 제외"); td.append(excluded);
    const flags = document.createElement("details"); const flagsTitle = document.createElement("summary"); flagsTitle.textContent="인정 확인 표시"; flags.append(flagsTitle);
    for(const [flag,label] of Object.entries(reviewFlags)) {
      const item=document.createElement("label"); const input=document.createElement("input"); input.type="checkbox"; input.dataset.field=`review-${flag}`; input.checked=(course.review_flags || []).includes(flag); input.setAttribute("aria-label",label);
      const text=document.createElement("span"); text.textContent=label; item.append(input,text); flags.append(item);
    }
    td.append(flags); row.append(td);
    const remove = document.createElement("button"); remove.type="button"; remove.textContent="삭제";
    remove.addEventListener("click",()=>{ row.remove(); confirm.checked=false; invalidate(); enable(); });
    const rmCell = document.createElement("td"); rmCell.append(remove); row.append(rmCell); body.append(row); enable();
  }
  function reviewRows(courses, year, note) {
    invalidate(); body.replaceChildren(); confirm.checked=false; complete.checked=false; detectedYear=year; review.hidden=false;
    review.open=true;
    byId("transcript-degree").value="unknown";
    byId("transcript-scope").textContent = year === 2026 ? "2026학번 적용: 과목 내용을 확인해 주세요." : year ? `${year}학번 감지: 과목 인식은 확인할 수 있지만 2026 규칙으로 계산할 수 없습니다.` : "입학년도 미확인: 원문을 확인하세요. 2026 자료일 때 새 이수내역을 직접 입력할 수 있습니다.";
    courses.forEach(add); message.textContent=note; enable();
  }
  function payload() {
    const courses = Array.from(body.children).map((row,index)=>{
      const val = (field)=>row.querySelector(`[data-field='${field}']`).value;
      const creditText = val("credits"); const credit = Number(creditText);
      if(creditText === "" || !Number.isInteger(credit) || credit < 0 || credit > 30 || !val("grade") || !val("course_name").trim()) throw new Error("학점·과목명·성적을 모두 확인하세요.");
      row.dataset.rowId=`row-${index+1}`;
      return {row_id:row.dataset.rowId,course_name:val("course_name").trim(),course_code:val("course_code").trim() || null,credits:credit,grade:val("grade"),category:val("category"),balanced_area:val("balanced_area") || null,term:row.dataset.term || null,excluded:row.querySelector("[data-field='excluded']").checked,review_flags:Object.keys(reviewFlags).filter(flag=>row.querySelector(`[data-field='review-${flag}']`).checked)};
    });
    return {schema_version:"1.0.0",admission_year:2026,matched_curriculum_year:2026,department:"컴퓨터공학과",degree_track:byId("transcript-degree").value,confirmed:true,record_complete:complete.checked,courses};
  }
  function button(label, action) {
    const element=document.createElement("button"); element.type="button"; element.textContent=label; element.addEventListener("click",action); return element;
  }
  function focusChecks(ids) {
    for(const [id,section] of checkSections) {
      const focused=ids.includes(id); section.dataset.focused=String(focused);
      section.setAttribute("style",focused ? "outline:2px solid currentColor;outline-offset:3px" : "");
    }
    checkSections.get(ids[0])?.scrollIntoView?.({behavior:"smooth",block:"center"});
  }
  function focusRow(id, kind) {
    const row=Array.from(body.children).find(row=>row.dataset.rowId===id); if(!row) return;
    for(const candidate of body.children) candidate.setAttribute("style",candidate===row ? "outline:2px solid currentColor" : "");
    row.scrollIntoView?.({behavior:"smooth",block:"center"});
    const field={category:"category",balanced_area:"balanced_area",equivalence:"review-equivalence",retroactivity:"review-retroactivity",duplicate_or_retake:"review-retake"}[kind] || "course_name";
    row.querySelector(`[data-field='${field}']`)?.focus?.();
  }
  function renderVerification(container, items) {
    if(!items?.length) return;
    const section=document.createElement("section"); const title=document.createElement("h4"); title.textContent="확인할 항목"; section.append(title);
    for(const item of items) {
      const entry=document.createElement("div"); entry.dataset.verificationId=item.item_id;
      const description=document.createElement("p"); description.textContent=`${item.severity === "blocking" ? "확인 필요" : "참고"}: ${item.message} ${item.action}`; entry.append(description);
      for(const id of item.row_ids) {
        const rows=Array.from(body.children); const index=rows.findIndex(row=>row.dataset.rowId===id);
        if(index>=0) { const name=rows[index].querySelector("[data-field='course_name']").value; entry.append(button(`${index+1}행 ${name} 보기`,()=>focusRow(id,item.kind))); }
      }
      if(item.kind === "degree_track") entry.append(button("전공 이수유형 입력 보기",()=>byId("transcript-degree").focus?.()));
      if(item.kind === "record_completeness") entry.append(button("전체 이수내역 확인 보기",()=>complete.focus?.()));
      for(const id of item.check_ids) if(checkSections.has(id)) entry.append(button(`${checkLabels.get(id)} 비교 보기`,()=>focusChecks([id])));
      section.append(entry);
    }
    container.append(section);
  }
  function render(data) {
    output.replaceChildren(); output.hidden=false;
    checkSections.clear(); checkLabels.clear();
    const title=document.createElement("h3"); title.textContent=data.answer; output.append(title);
    const credits=data.credit_summary;
    const summary=document.createElement("p"); summary.textContent=`입력상 PASS학점 합계 ${credits?.input_pass_credits ?? data.raw_earned_credits} · 조건부 졸업인정학점 ${(credits ? credits.conditional_graduation_credits : data.recognized_graduation_credits) ?? "확인 필요"}${credits ? ` · 인정 미확인 PASS학점 ${credits.unresolved_pass_credits}` : ""} · 최종 졸업 인증 아님`; output.append(summary);
    let target=output;
    if(semanticUI) {
      const total=data.checks.find(c=>c.check_id === "credits.graduation.total");
      const required=data.checks.find(c=>c.check_id === "major.required.course_set");
      const thesis=data.checks.find(c=>c.check_id === "graduation.thesis");
      const simple=document.createElement("div"); simple.className="transcript-simple-summary";
      for(const text of [total?.gap != null ? `졸업학점 부족분: ${total.gap}학점 (조건부 비교)` : "졸업 인정학점: 추가 확인 필요",
          required?.missing_courses?.length ? `남은 전공필수: ${required.missing_courses.join(", ")}` : required?.result === "met" ? "확인된 전공필수 과목: 모두 이수" : "남은 전공필수: 확인 필요",
          thesis ? `0학점 졸업논문: ${{met:"이수 확인",not_met:"미이수 — 학점 합계와 별도로 필요",needs_review:"확인 필요"}[thesis.result]}` : "졸업논문: 확인 필요"]) {
        const p=document.createElement("p"); p.textContent=text; simple.append(p);
      }
      output.append(simple);
      target=document.createElement("details"); const cap=document.createElement("summary"); cap.textContent="분야별 계산 · 확인할 항목 · PDF 근거"; target.append(cap); output.append(target);
    }
    for(const check of data.checks) {
      const section=document.createElement("section"); section.className="transcript-check";
      section.dataset.checkId=check.check_id; checkSections.set(check.check_id,section); checkLabels.set(check.check_id,check.label);
      const heading=document.createElement("h4"); heading.textContent=`${check.label}: ${{met:"충족",not_met:"미충족",needs_review:"확인 필요"}[check.result]}`; section.append(heading);
      const unit=check.check_id === "general.balanced.area_coverage" ? "영역" : "학점";
      const detail=document.createElement("p"); detail.textContent=check.gap != null ? `${check.required}${unit} 기준 / ${check.earned}${unit} 인정 / ${check.gap}${unit} 부족` : check.missing_courses.length ? `남은 과목: ${check.missing_courses.join(", ")}` : check.note; section.append(detail);
      section.append(button("이 항목 질문하기",()=>ask(checkQuestion(check.check_id))));
      const supported=check.evidence_packet.status === "supported";
      const packet=supported ? check.evidence_packet : check.policy_packet;
      if(packet) {
        const evidence=document.createElement("details"); const cap=document.createElement("summary"); cap.textContent=supported ? "규칙 · PDF 근거 보기" : "정책 참고 · PDF 근거 보기 (개인 판정 근거 아님)"; evidence.append(cap);
        const indices=new Map();
        for(const ref of packet.evidence) { const p=document.createElement("p"); p.textContent=`${ref.rule_id} · ${ref.locator} · ${ref.claim}`; const index=indices.get(ref.rule_id)||0; indices.set(ref.rule_id,index+1); if(window.AcademicEvidence)p.append(window.AcademicEvidence.button(ref,index)); evidence.append(p); }
        for(const rule of packet.applied_rules) { const p=document.createElement("p"); p.className="packet-id"; p.textContent=`${rule.rule_id} · SHA-256 ${rule.rule_sha256}`; evidence.append(p); }
        section.append(evidence);
      }
      target.append(section);
    }
    renderVerification(target,data.verification_items);
    for(const text of data.issues) { const p=document.createElement("p"); p.textContent=text; target.append(p); }
  }
  function checkQuestion(id) {
    if(id === "major.required.course_set") return "남은 전공필수 과목 알려줘";
    if(id === "graduation.thesis") return "졸업논문 이수했어?";
    if(id === "major.counseling") return "심층상담 이수했어?";
    if(id === "general.balanced.area_coverage") return "균형교양 영역 얼마나 부족해?";
    if(id === "credits.major.advanced") return "심화전공 배분 확인할 항목은?";
    if(id.startsWith("credits.general.")) return "교양 몇 학점 부족해?";
    if(id.startsWith("credits.major.")) return "전공 몇 학점 부족해?";
    if(id === "credits.graduation.remaining") return "자유선택 잔여학점 얼마나 부족해?";
    return "졸업까지 몇 학점 남았어?";
  }
  async function ask(question) {
    if(semanticUI && latest && question.trim()) {
      byId("question").value=question.trim();
      byId("question").dispatchEvent(new Event("input"));
      byId("question-form").requestSubmit();
      byId("answer-panel").scrollIntoView?.({behavior:"smooth",block:"start"});
      return;
    }
    if(!latest || !question.trim()) return; const token=generation; const sequence=++followupSequence;
    if(activeFollowupController) activeFollowupController.abort();
    const controller=new AbortController(); activeFollowupController=controller;
    try { const response=await fetch("/v1/academic/transcripts/chat",{method:"POST",headers:{"Content-Type":"application/json"},cache:"no-store",credentials:"omit",signal:controller.signal,body:JSON.stringify({question:question.trim(),transcript:latest,...(friendlyUI ? {response_style:"friendly",previous_question:previousQuestion} : {})})});
      if(token!==generation || sequence!==followupSequence) return;
      if(!response.ok) throw new Error("이수내역 질문을 처리할 수 없습니다."); const data=await response.json();
      if(token!==generation || sequence!==followupSequence) return;
      const chat=byId("transcript-chat"); chat.textContent=friendlyUI && typeof data.conversational_answer === "string" ? data.conversational_answer : data.answer;
      previousQuestion=data.status === "supported" && typeof data.context_question === "string" ? data.context_question : null;
      focusChecks(data.focus_check_ids || []);
      if(data.focus_check_ids?.length) chat.append(button("관련 비교 항목 보기",()=>focusChecks(data.focus_check_ids)));
      renderVerification(chat,data.verification_items);
    } catch(_error) {if(token===generation && sequence===followupSequence) byId("transcript-chat").textContent="이수내역 질문을 처리할 수 없습니다. 잠시 후 다시 시도해 주세요.";}
    finally {if(token===generation && sequence===followupSequence && activeFollowupController===controller) activeFollowupController=null;}
  }
  byId("transcript-extract").addEventListener("click", async()=>{
    const file=byId("transcript-file").files[0]; if(!file || file.size > 10*1024*1024) { message.textContent="10MB 이하 PDF를 선택하세요."; return; }
    const page=byId("transcript-page").value;
    if(page && (!Number.isInteger(Number(page)) || Number(page)<1 || Number(page)>10)) { message.textContent="페이지는 1~10 정수입니다."; return; }
    invalidate(); const token=generation; const controller=new AbortController(); activeController=controller;
    busy=true; enable(); byId("transcript-extract").disabled=true;
    message.textContent="이 페이지를 제공하는 PC에서 과목을 인식하는 중입니다. 스캔 PDF는 시간이 걸릴 수 있습니다.";
    try {
      const response=await fetch(`/v1/academic/transcripts/extract${page ? `?page_number=${page}` : ""}`,{method:"POST",headers:{"Content-Type":"application/pdf"},cache:"no-store",credentials:"omit",signal:controller.signal,body:file});
      if(token!==generation) return;
      if(!response.ok) throw new Error("성적표를 읽을 수 없습니다. PDF 크기·페이지 또는 잠금 여부를 확인하고 직접 입력할 수 있습니다.");
      const data=await response.json(); if(token !== generation) return;
      reviewRows(data.courses,data.detected_admission_year,`${data.courses.length}과목 인식. ${data.issues.join(" ")}`);
    } catch(_error) { if(token===generation) message.textContent="성적표를 읽을 수 없습니다. PDF 크기·페이지 또는 잠금 여부를 확인하고 직접 입력할 수 있습니다."; }
    finally { if(token===generation && activeController===controller) {activeController=null;busy=false;byId("transcript-extract").disabled=false;enable();} }
  });
  byId("transcript-file").addEventListener("change",()=>{invalidate(); body.replaceChildren(); review.hidden=true; confirm.checked=false; complete.checked=false; detectedYear=null; byId("transcript-degree").value="unknown"; message.textContent="새 PDF의 과목 인식을 실행해 주세요."; enable();});
  byId("transcript-page").addEventListener("input",()=>{invalidate(); confirm.checked=false; enable();});
  byId("transcript-manual").addEventListener("click",()=>{reviewRows([],2026,"새 2026 이수내역입니다. 다른 학번 기록을 바꿔서 입력하지 마세요.");add();});
  byId("transcript-demo").addEventListener("click",()=>reviewRows([
    {course_name:"고급자료구조",course_code:"CDA0143",credits:3,grade:"A0",category:"major_required"},
    {course_name:"가상 기초교양",credits:3,grade:"B+",category:"foundation"},
    {course_name:"심층상담",course_code:"CDA0088",credits:0,grade:"S",category:"major_required"},
  ],2026,"가상 예제입니다. 실제 학생 성적표가 아닙니다."));
  byId("transcript-clear").addEventListener("click",clear);
  async function useExample(identifier) {
    invalidate(); const token=generation; const controller=new AbortController(); activeController=controller;
    busy=true; enable(); message.textContent="가상 PDF를 실제로 인식하고 비교하는 중입니다…";
    try {
      const response=await fetch(`/v1/academic/transcripts/examples/${identifier}`,{method:"POST",headers:{"Content-Type":"application/json"},body:"{}",cache:"no-store",credentials:"omit",signal:controller.signal});
      if(token !== generation) return;
      if(!response.ok) throw new Error("example unavailable");
      const data=await response.json(); if(token !== generation) return;
      if(data.synthetic !== true || data.example_id !== identifier || !Array.isArray(data.extraction?.courses) || !Array.isArray(data.assessment?.checks) || !Array.isArray(data.transcript?.courses) || data.transcript.confirmed !== true) throw new Error("bad example response");
      reviewRows(data.transcript.courses,2026,`${data.title} · PDF ${data.extraction.courses.length}과목 인식 · 가상 전체 이수내역과 예제 조건 자동 적용`);
      byId("transcript-degree").value="single_major"; confirm.checked=true; complete.checked=true;
      review.open=false; latest=data.transcript; render(data.assessment); byId("transcript-followups").hidden=false;
      activeController=null; busy=false; enable(); notifyRevision();
      const link=document.createElement("a"); link.href=`/v1/academic/transcripts/examples/${identifier}.pdf`; link.textContent="가상 PDF 원본 보기"; link.target="_blank"; link.rel="noopener"; message.append(document.createTextNode(" · "),link);
    } catch(_error) {if(token === generation) message.textContent="가상 PDF 인식에 실패했습니다. 인식하지 못한 값을 대신 채우지 않습니다. 잠시 후 다시 시도해 주세요.";}
    finally {if(token === generation && activeController === controller) {activeController=null;busy=false;enable();}}
  }
  document.querySelectorAll("[data-transcript-example]").forEach(element=>element.addEventListener("click",()=>useExample(element.dataset.transcriptExample)));
  byId("transcript-add").addEventListener("click",()=>{invalidate();confirm.checked=false;add();});
  body.addEventListener("input",()=>{invalidate();confirm.checked=false;enable();});
  body.addEventListener("change",()=>{invalidate();confirm.checked=false;enable();});
  confirm.addEventListener("change",()=>{invalidate();enable();}); complete.addEventListener("change",invalidate);
  byId("transcript-degree").addEventListener("change",()=>{invalidate();confirm.checked=false;enable();});
  byId("transcript-assess").addEventListener("click",async()=>{
    if(!confirm.checked || detectedYear!==2026 || busy) return;
    invalidate(); const token=generation; const controller=new AbortController(); activeController=controller; busy=true; enable();
    let failureMessage="입력에 미확인·잘못된 값이 있습니다. 과목명·학점·성적을 확인한 뒤 다시 비교하세요.";
    try { const request=payload(); const response=await fetch("/v1/academic/transcripts/assess",{method:"POST",headers:{"Content-Type":"application/json"},cache:"no-store",credentials:"omit",signal:controller.signal,body:JSON.stringify(request)});
      if(token!==generation) return;
      if(!response.ok) {if(response.status===503) failureMessage="학사 근거를 확인할 수 없습니다."; throw new Error("assessment rejected");}
      const data=await response.json(); if(token!==generation) return;
      if (!Array.isArray(data.checks)) throw new Error("invalid assessment response");
      render(data); latest=request; byId("transcript-followups").hidden=data.checks.length===0; notifyRevision();
    } catch(_error) {if(token===generation) message.textContent=failureMessage;}
    finally {if(token===generation && activeController===controller) {activeController=null;busy=false;enable();}}
  });
  document.querySelectorAll("[data-transcript-question]").forEach(element=>element.addEventListener("click",()=>ask(element.dataset.transcriptQuestion)));
  const questions=byId("transcript-followups");
  questions.append(button("확인할 항목",()=>ask("확인이 필요한 항목은?")));
  const questionLabel=document.createElement("label"); questionLabel.textContent="이수내역에 직접 질문하기";
  const questionInput=document.createElement("input"); questionInput.type="text"; questionInput.maxLength=200; questionInput.placeholder="예: 남은 전공필수 과목 알려줘"; questionInput.setAttribute("aria-label","이수내역 질문"); questionLabel.append(questionInput);
  questions.append(questionLabel,button("질문",()=>ask(questionInput.value)));
  questionInput.addEventListener("keydown",event=>{if(event.key === "Enter") {event.preventDefault();ask(questionInput.value);}});
  const hostname=(window.location?.hostname || "localhost").toLowerCase();
  const localAccess=hostname==="localhost" || hostname==="::1" || hostname==="[::1]" || /^127(?:\.\d{1,3}){3}$/.test(hostname);
  if(!localAccess) {
    byId("public-demo-note").hidden=false;
    byId("transcript-privacy-note").textContent="2026학번 컴퓨터공학과만 지원하는 로그인 없는 공개 데모입니다. 성적표 PDF와 이수내역은 HTTPS 연결과 Cloudflare 중계 서비스를 거쳐 운영자의 PC로 전달되어 요청 메모리에서 처리됩니다. 서비스는 성적표를 저장하거나 LLM에 보내지 않으며, 브라우저의 영구 저장소에도 이수내역을 남기지 않습니다. 인식 결과를 확인한 뒤 계산하세요.";
    byId("feedback-privacy-note").textContent="비식별 질문과 공개 근거만 연구실 Gemma에 보냅니다. 성적표·개인 이수내역·입력 학점은 보내거나 저장하지 않습니다. 공개 데모에서는 보완 요청 저장을 제공하지 않습니다. 성적표 지우기 또는 페이지를 떠나면 이 탭의 이수내역과 대기 요청을 지웁니다.";
  }
  window.AcademicTranscript = Object.freeze({
    current: () => latest ? JSON.parse(JSON.stringify(latest)) : null,
    revision: () => generation,
    clear,
  });
  window.addEventListener("pagehide",clear);
})();
