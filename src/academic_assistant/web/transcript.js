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
  const categories = {unknown:"미확인", foundation:"기초교양", balanced:"균형교양", expanded:"확대교양", major_required:"전공필수", major_elective:"전공선택", free:"자유선택"};
  const areas = {"":"미확인/해당 없음", "digital-communication":"디지털커뮤니케이션", "humanities-arts":"인문예술", "society-culture":"사회와문화", "nature-science-technology":"자연·과학·기술의이해"};
  const grades = {"":"미확인", "A+":"A+", A0:"A0", "B+":"B+", B0:"B0", "C+":"C+", C0:"C0", "D+":"D+", D0:"D0", F:"F", F0:"F0", S:"S", U:"U", P:"P", PASS:"PASS", W:"W"};
  function invalidate() {
    generation += 1;
    followupSequence += 1;
    latest = null;
    output.hidden = true;
    byId("transcript-followups").hidden = true;
    byId("transcript-chat").textContent = "";
  }
  function enable() { byId("transcript-assess").disabled = busy || !confirm.checked || detectedYear !== 2026 || body.children.length === 0; }
  function clear() {
    invalidate(); body.replaceChildren(); review.hidden = true; confirm.checked = false; complete.checked = false;
    detectedYear = null; byId("transcript-file").value = ""; message.textContent = "이수내역을 메모리에서 지웠습니다."; enable();
    byId("transcript-degree").value="unknown";
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
    inputCell(row, "course_name", course.course_name); inputCell(row,"course_code",course.course_code);
    inputCell(row,"credits",course.credits,"number"); selectCell(row,"grade",grades,course.grade);
    selectCell(row,"category",categories,course.category || "unknown"); selectCell(row,"balanced_area",areas,course.balanced_area);
    const td = document.createElement("td"); const excluded = document.createElement("input"); excluded.type = "checkbox"; excluded.dataset.field = "excluded"; excluded.checked = !!course.excluded; excluded.setAttribute("aria-label","학점 계산 제외"); td.append(excluded); row.append(td);
    const remove = document.createElement("button"); remove.type="button"; remove.textContent="삭제";
    remove.addEventListener("click",()=>{ row.remove(); confirm.checked=false; invalidate(); enable(); });
    const rmCell = document.createElement("td"); rmCell.append(remove); row.append(rmCell); body.append(row); enable();
  }
  function reviewRows(courses, year, note) {
    invalidate(); body.replaceChildren(); confirm.checked=false; complete.checked=false; detectedYear=year; review.hidden=false;
    byId("transcript-degree").value="unknown";
    byId("transcript-scope").textContent = year === 2026 ? "2026학번 적용: 과목 내용을 확인해 주세요." : year ? `${year}학번 감지: 과목 인식은 확인할 수 있지만 2026 규칙으로 계산할 수 없습니다.` : "입학년도 미확인: 원문을 확인하세요. 2026 자료일 때 새 이수내역을 직접 입력할 수 있습니다.";
    courses.forEach(add); message.textContent=note; enable();
  }
  function payload() {
    const courses = Array.from(body.children).map((row,index)=>{
      const val = (field)=>row.querySelector(`[data-field='${field}']`).value;
      const creditText = val("credits"); const credit = Number(creditText);
      if(creditText === "" || !Number.isInteger(credit) || credit < 0 || credit > 30 || !val("grade") || !val("course_name").trim()) throw new Error("학점·과목명·성적을 모두 확인하세요.");
      return {row_id:`row-${index+1}`,course_name:val("course_name").trim(),course_code:val("course_code").trim() || null,credits:credit,grade:val("grade"),category:val("category"),balanced_area:val("balanced_area") || null,excluded:row.querySelector("[data-field='excluded']").checked};
    });
    return {schema_version:"1.0.0",admission_year:2026,matched_curriculum_year:2026,department:"컴퓨터공학과",degree_track:byId("transcript-degree").value,confirmed:true,record_complete:complete.checked,courses};
  }
  function render(data) {
    output.replaceChildren(); output.hidden=false;
    const title=document.createElement("h3"); title.textContent=data.answer; output.append(title);
    const summary=document.createElement("p"); summary.textContent=`입력상 PASS학점 합계 ${data.raw_earned_credits} · 졸업인정학점 ${data.recognized_graduation_credits ?? "확인 필요"} · 최종 졸업 인증 아님`; output.append(summary);
    for(const check of data.checks) {
      const section=document.createElement("section"); section.className="transcript-check";
      const heading=document.createElement("h4"); heading.textContent=`${check.label}: ${{met:"충족",not_met:"미충족",needs_review:"확인 필요"}[check.result]}`; section.append(heading);
      const unit=check.check_id === "general.balanced.area_coverage" ? "영역" : "학점";
      const detail=document.createElement("p"); detail.textContent=check.gap !== null ? `${check.required}${unit} 기준 / ${check.earned}${unit} 인정 / ${check.gap}${unit} 부족` : check.missing_courses.length ? `남은 과목: ${check.missing_courses.join(", ")}` : check.note; section.append(detail);
      const supported=check.evidence_packet.status === "supported";
      const packet=supported ? check.evidence_packet : check.policy_packet;
      if(packet) {
        const evidence=document.createElement("details"); const cap=document.createElement("summary"); cap.textContent=supported ? "규칙 · PDF 근거 보기" : "정책 참고 · PDF 근거 보기 (개인 판정 근거 아님)"; evidence.append(cap);
        const indices=new Map();
        for(const ref of packet.evidence) { const p=document.createElement("p"); p.textContent=`${ref.rule_id} · ${ref.locator} · ${ref.claim}`; const index=indices.get(ref.rule_id)||0; indices.set(ref.rule_id,index+1); if(window.AcademicEvidence)p.append(window.AcademicEvidence.button(ref,index)); evidence.append(p); }
        for(const rule of packet.applied_rules) { const p=document.createElement("p"); p.className="packet-id"; p.textContent=`${rule.rule_id} · SHA-256 ${rule.rule_sha256}`; evidence.append(p); }
        section.append(evidence);
      }
      output.append(section);
    }
    for(const text of data.issues) { const p=document.createElement("p"); p.textContent=text; output.append(p); }
  }
  byId("transcript-extract").addEventListener("click", async()=>{
    const file=byId("transcript-file").files[0]; if(!file || file.size > 10*1024*1024) { message.textContent="10MB 이하 PDF를 선택하세요."; return; }
    const page=byId("transcript-page").value;
    if(page && (!Number.isInteger(Number(page)) || Number(page)<1 || Number(page)>10)) { message.textContent="페이지는 1~10 정수입니다."; return; }
    invalidate(); const token=generation; busy=true; enable(); byId("transcript-extract").disabled=true;
    message.textContent="로컬에서 과목을 인식하는 중입니다. 스캔 PDF는 시간이 걸릴 수 있습니다.";
    try {
      const response=await fetch(`/v1/academic/transcripts/extract${page ? `?page_number=${page}` : ""}`,{method:"POST",headers:{"Content-Type":"application/pdf"},body:file});
      if(!response.ok) throw new Error("성적표를 읽을 수 없습니다. PDF 크기·페이지 또는 잠금 여부를 확인하고 직접 입력할 수 있습니다.");
      const data=await response.json(); if(token !== generation) return;
      reviewRows(data.courses,data.detected_admission_year,`${data.courses.length}과목 인식. ${data.issues.join(" ")}`);
    } catch(error) { if(token===generation) message.textContent=error.message; }
    finally { busy=false; byId("transcript-extract").disabled=false; enable(); }
  });
  byId("transcript-file").addEventListener("change",()=>{invalidate(); body.replaceChildren(); review.hidden=true; confirm.checked=false; detectedYear=null; enable();});
  byId("transcript-page").addEventListener("input",()=>{invalidate(); confirm.checked=false; enable();});
  byId("transcript-manual").addEventListener("click",()=>{reviewRows([],2026,"새 2026 이수내역입니다. 다른 학번 기록을 바꿔서 입력하지 마세요.");add();});
  byId("transcript-demo").addEventListener("click",()=>reviewRows([
    {course_name:"고급자료구조",course_code:"CDA0143",credits:3,grade:"A0",category:"major_required"},
    {course_name:"가상 기초교양",credits:3,grade:"B+",category:"foundation"},
    {course_name:"심층상담",course_code:"CDA0088",credits:0,grade:"S",category:"major_required"},
  ],2026,"가상 예제입니다. 실제 학생 성적표가 아닙니다."));
  byId("transcript-clear").addEventListener("click",clear);
  byId("transcript-add").addEventListener("click",()=>{invalidate();confirm.checked=false;add();});
  body.addEventListener("input",()=>{invalidate();confirm.checked=false;enable();});
  body.addEventListener("change",()=>{invalidate();confirm.checked=false;enable();});
  confirm.addEventListener("change",()=>{invalidate();enable();}); complete.addEventListener("change",invalidate);
  byId("transcript-degree").addEventListener("change",()=>{invalidate();confirm.checked=false;enable();});
  byId("transcript-assess").addEventListener("click",async()=>{
    if(!confirm.checked || detectedYear!==2026 || busy) return;
    invalidate(); const token=generation; busy=true; enable();
    try { const request=payload(); const response=await fetch("/v1/academic/transcripts/assess",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(request)});
      if(!response.ok) throw new Error(response.status===503 ? "학사 근거를 확인할 수 없습니다." : "입력에 미확인·잘못된 값이 있습니다.");
      const data=await response.json(); if(token!==generation) return; latest=request; render(data); byId("transcript-followups").hidden=data.checks.length===0;
    } catch(error) {if(token===generation) message.textContent=error.message;} finally {busy=false;enable();}
  });
  document.querySelectorAll("[data-transcript-question]").forEach(button=>button.addEventListener("click",async()=>{
    if(!latest) return; const token=generation; const sequence=++followupSequence;
    try { const response=await fetch("/v1/academic/transcripts/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({question:button.dataset.transcriptQuestion,transcript:latest})});
      if(!response.ok) throw new Error("이수내역 질문을 처리할 수 없습니다."); const data=await response.json(); if(token===generation && sequence===followupSequence) byId("transcript-chat").textContent=data.answer;
    } catch(error) {if(token===generation && sequence===followupSequence) byId("transcript-chat").textContent=error.message;}
  }));
  window.addEventListener("pagehide",clear);
})();
