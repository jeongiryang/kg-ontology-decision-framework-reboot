# 공유 계약

## 자연어 조회 계약

`AssistantTurnRequest`는 비식별 질문, 2026 적용 범위, 선택적 이전 질문·현재 확인된 성적표를 받는다. `AssistantTurnResponse`는 부분별 상태와 근거, `plan_status`, `generation_status`, `reason_code`를 구분한다. 처리 연결 불가는 `processing_unavailable`이며 실제 자료 부재인 `no_matching_evidence`와 같지 않다. 인사·범위 안내는 학사 근거가 있다는 주장이 아니다.

`CourseCatalogue`는 이미 승인된 교육과정 PDF의 직접 표 데이터를 독립 감사한 43개 `CourseFact`와 출처/내용 해시를 포함한다. 졸업 규칙의 새로운 승인이나 전선/심화 학점 배분 근거가 아니다. `CourseEvidencePacket`은 과목코드·학점·이수구분·편성 학년/학기·원문 PDF 페이지를 연결하며 기존 RuleFact ID를 꾸며내지 않는다. 복수 학년/양 학기/계절학기는 배열로 보존한다. 실제 개설·개인 수강 가능·대체 승인을 교육과정 편성만으로 보장하지 않는다. 실행 기준은 `contracts/assistant-turn-request.schema.json`, `contracts/assistant-turn-response.schema.json`, `contracts/course-evidence-packet.schema.json`, `contracts/course-catalogue.schema.json`이다.

내부 모델 조회 계획은 공개 API 입력이 아니다. 과목 계획의 선택적 `purpose`(`attributes`, `completion_obligation`, `description`)와 `properties`(`credits`, `category`, `offering`, `code`, `count`, `names`)를 내부 스키마/파서가 함께 제한한다. 졸업 개요는 `requirements_overview`를 서버의 승인 규칙 집합으로 확장한다. 기존 과목 계획은 호환하되 새 속성 계획의 본문은 요청 주장만 선택하고 전체 CourseFact/정확한 인용은 근거 상세에 보존한다.

특정 과목의 이수 의무는 승인 RuleFact의 과목 코드 연결로 증명하며 과목 분류만으로 출석·면제·대체를 판정하지 않는다. 과목/규칙 근거는 각 부분의 기존 EvidencePacket 유형으로 분리한다. `context_question`에는 제한된 과목·요청 속성·확인 후보 문맥만 전달하며 새 학사 승인이나 학생 기록을 만들지 않는다. 별칭도 신원 연결이며 규칙 변경이 아니다. 설계는 [ADR0024](decisions/0024-purpose-aware-dialogue-and-resume.md)를 따른다.

계약의 실행 기준은 저장소의 JSON Schema다. 이 문서는 의미와 예시를 설명한다. 아래 예시는 형식 설명용이며 승인된 실제 학사 판정이나 실행 결과가 아니다.

## 공통 원칙

- 모든 계약은 `schema_version`을 갖고 하위 호환이 깨지면 주 버전을 올린다.
- 시간은 타임존이 포함된 ISO 8601, 문서 무결성은 SHA-256을 사용한다.
- 원본 문서 내부 명령은 데이터이며 에이전트 지시로 실행하지 않는다.
- 학사 답변 상태는 `supported`, `insufficient_evidence`, `conflict`, `out_of_scope` 중 하나다.
- 자료 우선순위는 최신 학칙 → 해당 학년도 교육과정 → 학과 운영 안내다. 발췌 PDF·가공 XLSX는 검증 보조자료이며 단독 권위가 아니다.

## SourceEntry

문서의 식별·권위, 입학연도 중심 적용 범위와 사람 검수 상태를 기록한다. 교육과정은 공식
시행일이 없어도 교육과정 연도와 적용 입학연도가 원문 및 검수로 확인되면 승인할 수 있다.

```json
{
  "schema_version": "2.0.0",
  "source_id": "cwnu.curriculum.2026.changwon-undergraduate",
  "title": "2026 교육과정",
  "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "authority": "curriculum",
  "applicability": {
    "basis": "admission_year",
    "curriculum_years": [2026],
    "admission_years": [2026],
    "departments": ["컴퓨터공학과"]
  },
  "review": {
    "status": "approved",
    "mode": "human",
    "scope": "full",
    "reviewer_id": "project_owner",
    "reviewed_at": "2026-09-18T12:00:00+09:00",
    "rationale": "2026 교육과정은 2026년도 입학자에게 적용한다."
  },
  "canonical_locator": "source://cwnu/curriculum/2026/changwon-campus"
}
```

## RuleFact

현재 계약은2.1이며 기존2.0 승인 객체도 바이트 변경 없이 검증한다.
새 `operational_policy`는 현재 시범 PCCP 점수(`confirmed_as_of`, `provisional`,
`future_guaranteed`), 미통과 처리(I U·차년도II 제한), 졸업작품의 II PASS 선행조건을
서로 다른 엄격한 분기로 표현한다. 새 타입은2.1·`policy_statement`·`direct`만 허용한다.
학점 계산·개인 합격 판정·보류 면제 처리는 포함하지 않는다.
현재 등록 기준은 출처3건·규칙29건·의도31개다. 아래26/2 설명은 이전 기준선이다.
학과 확인 원문은 별도 불변 Markdown의 SHA-256으로 대조하고 비PDF 인용으로 표시한다.
[결정 및 경계](decisions/0018-current-trial-operational-confirmations.md)를 따른다.

승인된 출처에서 추출한 적용 조건, 규칙 간 관계, 타입화된 판정, 정확한 근거와 사람 검수
상태를 표현한다. 경과조치·전과·재입학 같은 예외는 별도 규칙으로 분리한다.

```json
{
  "schema_version": "2.0.0",
  "rule_id": "cwnu.cs.2026.graduation.total-credits",
  "applicability": {
    "basis": "admission_year",
    "curriculum_years": [2026],
    "admission_years": [2026],
    "departments": ["컴퓨터공학과"],
    "conditions": []
  },
  "relationship": {"kind": "base", "target_rule_ids": []},
  "decision": {
    "statement": "졸업에 필요한 총 이수학점은 130학점 이상이다.",
    "outcome": {
      "type": "credit_threshold",
      "metric": "graduation.total_credits",
      "comparator": "at_least",
      "credits": 130
    },
    "operator": "threshold"
  },
  "evidence": [{
    "source_id": "cwnu.curriculum.2026.changwon-undergraduate",
    "locator": "PDF p.577 (printed p.569)",
    "evidence_type": "table_structure"
  }],
  "review": {"status": "needs_review", "mode": "human", "scope": "full"}
}
```

`needs_review` 규칙은 연구·검토 대상으로 사용할 수 있지만 학생 대상 확정 판정에 사용하지
않는다. 승인된 규칙을 수정할 때에는 기존 객체를 덮어쓰지 않고 새 `rule_id`와
`supersedes_rule_id`를 사용한다.

## EvidencePacket

학생이 제공한 비식별 사실, 적용 규칙, 인용, 충돌·누락과 답변 상태를 묶는다.

```json
{
  "schema_version": "2.0.0",
  "packet_id": "example-001",
  "scope": {
    "admission_year": 2026,
    "matched_curriculum_year": 2026,
    "department": "컴퓨터공학과"
  },
  "student_facts": {"completed_total_credits": 120},
  "applied_rules": [],
  "evidence": [],
  "issues": [{"kind": "review", "message": "관계 정의에 대한 사람 검수가 필요하다."}],
  "status": "insufficient_evidence"
}
```

이름·학번·원본 성적표는 계약과 로그에 넣지 않는다.

`supported`는 JSON Schema 통과만으로 성립하지 않는다. 저장소 인식 의미 검증기가 적용
규칙의 존재와 canonical SHA-256, `human/full/approved` 상태, 입학연도·교육과정·학과 범위,
인용의 규칙·출처·locator 연결을 모두 대조한다. 하나라도 다르면 fail-closed로 거절한다.

## AcademicAnswerRequest와 AcademicAnswerResponse

답변 요청은 1~500자의 질문, `admission_year`, `matched_curriculum_year`, `department`, 선택적인
`earned_credits`만 받는다. 학점 값은 승인된 credit metric을 키로 하는 0~500 정수이며 bool,
알 수 없는 metric, 학생 식별정보와 원본 성적표는 검증 오류다. 응답은 결정론적 `packet_id`,
네 상태 중 하나, 한국어 답변, 선택된 intent, 개별 metric 계산과 같은 상태·ID의 EvidencePacket을
포함한다. 질문 원문과 로컬 경로는 응답하지 않는다.

레지스트리는 명시적으로 등록한 26개 RuleFact, 출처 2개와 미검증 연구 항목 1개만 한 번씩 읽어
스키마, 범위, 관계, `human/full/approved` 상태와 canonical SHA-256을 검증한다. canonical 형식은
UTF-8 JSON, 정렬된 키, 공백 없는 구분자와 `ensure_ascii=false`다. 일부 파일이라도 손상되면 전체
레지스트리를 사용할 수 없는 것으로 처리한다.

승인 기준선은 `config/academic-registry-pins.json`에 RuleFact 26건, SourceEntry 2건, 연구 항목과
intent profile의 canonical digest로 고정한다. 각 객체가 스키마에는 맞더라도 판정값·문장·검수·
적용범위·근거·별칭이 바뀌어 pin과 달라지면 레지스트리 전체가 닫힌다. Intent profile 자체도
`AcademicIntentProfile` 스키마와 정확한 intent/rule coverage를 통과해야 한다.

HTTP 400/422와 CLI exit 64는 고정된 `invalid request`만 반환하며 입력값이나 검증기의 `input`
세부정보를 포함하지 않는다. 범위 밖 또는 레지스트리 장애 EvidencePacket은 사용자가 제출한
학과 문자열 대신 고정된 `지원범위외` 표지를 사용한다.

## AcademicChatResponse

`AcademicChatRequest`는 `/chat` 전용 기존 입력과 선택적 `previous_question`(한 질문, 최대500자/null), `generate_answer`(엄격한 boolean, 기본false)를 정의한다. `/answers`·CLI는 기존 `AcademicAnswerRequest`를 유지한다. 이전 질문은 현재 scope·earned_credits로 재검증하며 클라이언트가 전달한 status/rule/인용을 받지 않는다.

응답의 추가 선택 필드 `context_used`와 `clarification_choices`(최대3개의 label/question)는 문맥 사용과 질문 선택을 설명한다. 후보는 판정이 아니며 선택 후 승인 코어로 다시 조회한다. 실행 계약은 `contracts/academic-chat-request.schema.json`과 `contracts/academic-chat-response.schema.json`이다. [대화 안내](../operations/conversation-prototype.md)를 따른다.

`POST /v1/academic/chat`은 기존 `AcademicAnswerRequest`를 그대로 받는다. 응답은
`AcademicAnswerResponse`의 상태·답변·계산·EvidencePacket을 변경하지 않고
`llm_status`와 선택적인 `suggested_question`을 추가한다. 모델이 꺼져 있으면 `disabled`,
호출 대상이 아니면 `skipped`, 모델 연결 실패는 `unavailable`, 계약 밖 출력은 `rejected`다.
`suggested`는 근거 부족 질문에 대해 허용된 의도 ID를 모델이 제안했고, 서버가 해당 의도의
고정된 질문 표현만 보여 준 경우다. 이 제안은 학사 답변이나 승인 근거가 아니며
`insufficient_evidence`를 `supported`로 승격하지 않는다. 학사 판정은 항상 결정적 엔진이 한다.

2026-10-01 추가: 배포 설정 `ACADEMIC_LLM_GROUNDED_GENERATION=1`과 요청
`generate_answer=true`가 모두 있을 때만 별도 근거 문장 생성을 요청한다. 기존
`answer`·계산·EvidencePacket은 바꾸지 않는다. 기본 요청에는 새 생성 필드를 넣지
않아 기존 응답 형태를 유지한다. 새 기능은 선택 필드 확장이고 계약 버전1.0.0은 유지한다.
과거의 `additionalProperties:false` 스키마로 새 기능을 검증하려면 스키마도 갱신해야 한다.

- `generation_status`: `generated`, `cached`, `disabled`, `unavailable`, `rejected`, `busy`, `not_applicable`.
- `generated_answer`: 실제 모델이 작성하고 닫힌 문법 검증을 통과한 안내문. 성공할 때만 반환.
- `generated_claim_ids`: 순서까지 기존 적용 규칙 전체와 일치해야 한다. 실패 시빈 배열.

`generated/cached`는 기존 상태와 패킷이 모두`supported`이며 모든 근거 문장이 검증된
경우에만 허용한다. 나머지 상태는 생성 초안을 노출하지 않고 기존 답변을 사용한다.
승인된 공용 규칙만 모델에 보내며 학생 질문·이수학점·성적표·패킷은 보내지 않는다.
캐시는 모델·문법·근거 해시에 묶인 공용 문장만 최대5분 보존한다. 구체적 보장과 한계는
[최소 프로토타입 안내](../operations/minimal-prototype.md)와
[ADR0021](decisions/0021-verified-gemma-claim-generation.md)에 기록한다.

```json
{
  "schema_version": "1.0.0",
  "packet_id": "academic-0123456789abcdef0123456789abcdef",
  "status": "insufficient_evidence",
  "answer": "질문에 답할 승인된 학사 근거가 없습니다.",
  "intent_ids": [],
  "calculations": [],
  "evidence_packet": {
    "schema_version": "2.0.0",
    "packet_id": "academic-0123456789abcdef0123456789abcdef",
    "scope": {"admission_year": 2026, "matched_curriculum_year": 2026, "department": "컴퓨터공학과"},
    "student_facts": {}, "applied_rules": [], "evidence": [],
    "issues": [{"kind": "missing", "message": "질문에 답할 승인된 학사 근거가 없습니다."}],
    "status": "insufficient_evidence"
  },
  "llm_status": "disabled"
}
```

## AcademicFeedbackRequest와 AcademicFeedbackResponse

근거 부족·충돌 답변을 보완 대상으로 남길 때만 사용하는 명시적 동의 계약이다. 범위 밖 응답은
사용자가 제출한 원래 학과 값을 반향하지 않는 정책 때문에 저장 대상으로 받지 않는다.
요청은 실제 답변의 `packet_id`, 비지원 상태, 질문, 당시의 비식별 학점 합계, 보완 유형과
`consent_to_store=true`를 요구한다. 서버는 현재 엔진으로 답변을 다시 계산해 packet ID와 상태가
일치할 때만 저장한다.
`supported` 상태, 동의하지 않은 요청, 이름·학번·연락처·원본 성적표 표현은 저장하지 않고 422로
거절한다. 응답은 질문을 반향하지 않고 비식별 `feedback_id`와 `stored=true`만 반환한다.

저장 대상은 `.local/academic-feedback/feedback.jsonl`의 한 줄 JSON이며 공개 Git에서 제외된다.
새 로컬 기록 `2.0.0`에는 질문 원문을 넣지 않고 응답 ID·상태·유형·고정 범위만 남긴다.
기존 `1.0.0` 기록은 집계 호환성만 유지하며 새로 작성하지 않는다.
일반 답변 API는 계속 비저장이고, 웹 화면도 사용자가 동의 확인란과 저장 버튼을 직접 누르기
전에는 어떤 질문도 피드백 파일에 기록하지 않는다.

## AcademicReviewPacket

출처 적용 관계와 각 RuleFact의 관계·판정·해석을 사람이 전수 검수하기 위한 큐다.
`overall_status=approved`는 모든 대상을 검수한 뒤에만 사용할 수 있다.
큐의 subject 상태와 해당 SourceEntry/RuleFact의 `review` 상태, 검수자, 시각, 근거 문구는
양방향으로 일치해야 한다. `full` 출처 검수는 제목·해시·권위·적용범위를 모두 포함한다.

```json
{
  "schema_version": "1.0.0",
  "review_id": "cwnu.cs.2026.initial-review",
  "scope": {
    "curriculum_year": 2026,
    "admission_year": 2026,
    "department": "컴퓨터공학과"
  },
  "subjects": [{
    "subject_type": "rule",
    "subject_id": "cwnu.cs.2026.graduation.total-credits",
    "status": "pending",
    "review_question": "26학번 컴퓨터공학과 졸업학점 130학점 관계가 맞습니까?",
    "evidence_locators": ["PDF p.577 (printed p.569)"]
  }],
  "overall_status": "open"
}
```

## AcademicClarificationPacket

검수 대상의 실질적 모호성을 사용자에게 보여 줄 근거·선택지·영향으로 묶고, 검수 세션
권한과 정확한 응답을 감사 가능하게 기록한다. 촉진자는 패킷을 제안할 뿐 사용자에게 직접
질문하거나 규칙을 승인하지 않는다. `department_confirmation`은 같은 세션의
`authority_granted` 사건 뒤 명시적으로 받은 답변에만 사용할 수 있다.

```json
{
  "schema_version": "1.1.0",
  "clarification_id": "cwnu.cs.2026.example.questions",
  "review_id": "cwnu.cs.2026.initial-rules",
  "state": "ready",
  "coverage_mode": "partial",
  "scope": {"curriculum_year": 2026, "admission_year": 2026, "department": "컴퓨터공학과"},
  "input_snapshots": [{
    "artifact_id": "review:cwnu.cs.2026.initial-rules",
    "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  }],
  "session": {
    "session_id": "cwnu.cs.2026.example-session",
    "run_id": "example-clarification-v1",
    "thread_binding": "current_task",
    "state": "awaiting_authority",
    "started_at": "2026-09-18T14:30:00+09:00",
    "authority": {
      "kind": "department_confirmation",
      "status": "not_requested",
      "authorization_prompt": "이번 검수 세션의 명시적 답변을 학과 확인으로 기록해도 됩니까?",
      "choices": ["grant", "decline"]
    }
  },
  "batches": [{
    "batch_id": "graduation",
    "ordinal": 1,
    "topic": "졸업학점",
    "status": "queued",
    "questions": [{
      "question_id": "graduation.total",
      "subject_ids": ["cwnu.cs.2026.credits.graduation-total"],
      "purpose": "review_confirmation",
      "ambiguity_kind": "applicability",
      "required_authority": "department_confirmation",
      "evidence": [{
        "subject_id": "cwnu.cs.2026.credits.graduation-total",
        "source_id": "cwnu.curriculum.2026.changwon-undergraduate",
        "authority": "curriculum",
        "locator": "PDF p.577 컴퓨터공학과 행",
        "claim": "졸업학점 130"
      }],
      "prompt": "26학번 컴퓨터공학과 졸업학점 130학점 적용이 맞습니까?",
      "impact": "보류하면 이 규칙은 needs_review로 남습니다.",
      "choices": [
        {"choice_id": "confirm", "label": "맞음", "disposition": "approve", "impact": "승인 후보로 전달합니다."},
        {"choice_id": "defer", "label": "보류", "disposition": "pending", "impact": "미승인으로 유지합니다."}
      ],
      "recommended_choice_id": "confirm",
      "blocking_subject_ids": ["cwnu.cs.2026.credits.graduation-total"],
      "status": "queued"
    }]
  }],
  "suppressed": [],
  "conflicts": [],
  "responses": [],
  "audit_events": [{
    "event_id": "event.created",
    "event": "created",
    "at": "2026-09-18T14:30:00+09:00",
    "actor": "main",
    "details": "질문 패킷을 생성했다."
  }]
}
```

실제 패킷은 검수대장뿐 아니라 질문에 포함된 각 SourceEntry·RuleFact의 canonical SHA-256도
`input_snapshots`에 고정한다. 질문 근거도 subject별 현재 출처 권위·정확한 locator·근거 발췌와
동일해야 한다. RuleFact 근거의 `claim`은 정규화된 최종 판정문보다 해당 근거의 `excerpt`를
우선 사용하므로 서로 다른 원문 주장을 가진 충돌도 표현할 수 있다. 의미 검증기는 정확한 1회 포함, 현재 객체 해시, 권한 부여·만료 시각, 필수 권한,
선택 disposition과 질문 상태, 수정 선택의 별도 설명을 대조한다.
질문하지 않은 침묵, 권한 만료 뒤 답변, `보류`를 `승인`으로 바꾼 기록은 승인 근거가 될 수 없다.
자유서술은 명시적 선택으로 정규화되기 전까지 승인되지 않으며, 닫힌 세션은 권한 종료와 세션
종료 감사 사건을 같은 종료 시각에 남긴다. 제시·응답 사건은 질문 ID와 응답 ID로 연결한다.
충돌은 관련 subject, 양쪽의 등록 출처·정확한 locator·원문 claim과 권위 우선순위가 모두 현재
객체와 일치해야 한다. 숫자 뒤의 `학점` 같은 표기 차이를 제거한 claim 서명까지 같으면 실질
충돌로 보지 않는다. `conflict_triage` 질문은 승인 선택지와 `accepted` 상태를 가질 수 없으며,
근거 제출·수정·보류 또는 충돌 보존 경로만 허용한다.
스냅샷에 맞고 양쪽 claim이 실질적으로 달라야 하며, 대응하는 `conflict_triage` 질문 없이 임의로
만들 수 없다. 모든 감사 사건은 세션 시간 범위 안에 있어야 하고 입력 스냅샷 ID는 중복될 수 없다.
권한 사건은 미요청 → 부여 → 만료/철회 또는 미요청 → 거절의 단방향 전이만 허용하며,
감사 사건이 가리키는 질문·응답 ID도 현재 패킷에 실제로 존재해야 한다.

응답을 규칙에 적용한 패킷은 `state=applied`와 `application`을 함께 기록한다. 질문 당시의
`input_snapshots`는 감사 증거로 보존하고, `application.output_snapshots`는 승인 반영 뒤의
검수대장·SourceEntry·RuleFact 해시를 고정한다. 의미 검증기는 적용 완료 상태에서는 출력
스냅샷을 현재 객체와 비교하므로 질문 당시 기록을 덮어쓰지 않는다.
또한 적용된 SourceEntry·RuleFact에서 현재 `review`만 승인 전 `needs_review` 형태로 되돌린
canonical 해시가 질문 당시 `input_snapshots`와 같아야 한다. 따라서 출력 해시를 새로 써도
판정값·적용범위·관계·근거를 승인 뒤 바꾸는 것은 차단된다.
`application.response_snapshots`는 사용자 응답 전체의 canonical 해시를, `subject_attestations`는
각 승인 대상의 질문·응답 ID와 객체 `review`·검수대장 subject 해시를 고정한다. 따라서
`exact_text`, 검수자, 검수 시각 또는 승인 근거를 출력 해시와 함께 바꿔도 검증에 실패한다.

이 해시는 동일 저장소 안에서 불완전하거나 우발적인 변경을 탐지하는 일관성 장치다. 패킷,
해시와 검증 코드를 모두 수정할 권한이 있는 악의적 작성자에 대한 암호학적 부인 방지는
제공하지 않는다. 게시 후 변경 이력은 Git 커밋과 GitHub 이력으로 감사하며, 서명된 외부 승인
기록이 필요하면 별도의 키 관리와 승인 서비스를 도입해야 한다.

## AcademicResearchItem

사용자 기억이나 비공식 운영 관행처럼 공식 근거가 아직 없는 주장을 답변 지식과 분리해
조사 대기 상태로 기록한다. 이 객체는 SourceEntry나 RuleFact가 아니며, 공식 출처 감사와 새
검수 세션을 통과하기 전에는 학사 답변 근거가 될 수 없다.

```json
{
  "schema_version": "1.0.0",
  "research_id": "cwnu.cs.2026.graduation-practices",
  "scope": {"curriculum_year": 2026, "admission_year": 2026, "department": "컴퓨터공학과"},
  "origin": "unverified_user_recollection",
  "status": "awaiting_official_source",
  "eligible_for_academic_answer": false,
  "linked_rule_ids": ["cwnu.cs.2026.graduation.thesis-required"],
  "claims": [{
    "claim_id": "contest-award-substitution",
    "statement": "총장상급 이상 외부 공모전 수상이 졸업작품 대체요건일 가능성이 있다.",
    "verification_question": "공식 학과 자료에 이 대체요건이 명시되어 있는가?",
    "status": "unverified",
    "eligible_for_academic_answer": false,
    "answer_status_until_verified": "insufficient_evidence"
  }],
  "required_evidence": ["official_department_guidance", "course_operation_plan", "approved_internal_regulation"],
  "review_session_id": "cwnu.cs.2026.review-session-001",
  "created_at": "2026-09-18T16:30:00+09:00",
  "github_issue_url": "https://github.com/jeongiryang/kg-ontology-decision-framework-reboot/issues/1"
}
```

각 claim은 `eligible_for_academic_answer=false`와 `answer_status_until_verified=insufficient_evidence`
를 유지한다. CI는 claim ID·문장이 SourceEntry 또는 RuleFact로 복사되면 실패하며, 표현을
바꾸어 기존 승인 규칙에 주입하는 경우에도 승인 전 의미 해시 잠금이 변경을 차단한다.

## DSWRunRequest

연구실 서버에서 허용된 계산 작업의 범위와 승인을 기록한다.

```json
{
  "schema_version": "1.0.0",
  "request_id": "dsw-example-001",
  "purpose": "비식별 평가셋 추론",
  "gpu_count": 1,
  "requested_gpu_ids": [2],
  "expected_start": "2026-09-17T15:00:00+09:00",
  "expected_end": "2026-09-17T16:00:00+09:00",
  "command": "CUDA_VISIBLE_DEVICES=2 python evaluate.py",
  "outputs": ["reports/evaluation/example.json"],
  "persistent_service": false
}
```

상주 서비스는 승인 참조와 설정된 GPU 상한이 모두 있어야 한다.

## TaskResult

한 에이전트 작업의 상태, 산출물, 실제 검사와 문제를 기록한다.

```json
{
  "schema_version": "1.0.0",
  "run_id": "example-v1",
  "task_id": "source-audit",
  "agent_id": "actual-agent-id",
  "status": "completed",
  "summary": "문서 메타데이터와 적용 범위를 감사했다.",
  "artifacts": ["reports/audits/example.json"],
  "checks": [{
    "name": "출처 필드 검토",
    "status": "passed",
    "required": true,
    "evidence": "reports/audits/example.json"
  }],
  "issues": []
}
```

검사 상태는 `passed`, `failed`, `not_run`을 구분한다. 실행하지 않은 명령을 `command`에 꾸며 쓰지 않는다.

## CompletionReport

사용자에게 공개할 실행 단위 변경·검사·근거·미해결 문제를 묶는다.

```json
{
  "schema_version": "1.0.0",
  "run_id": "example-v1",
  "title": "하네스 예시 완료 보고",
  "request": "예시 계약을 검증한다.",
  "summary": "예시 하네스 검증을 완료했다.",
  "harness_version": "79b82281d305c89181fbb216499d5f1e962c14ed",
  "git": {
    "branch": "codex/example",
    "base_commit": "0123456789abcdef0123456789abcdef01234567",
    "head_commit": "89abcdef0123456789abcdef0123456789abcdef"
  },
  "changes": [{
    "kind": "added",
    "path": "docs/harness/README.md",
    "summary": "하네스 문서 목차를 추가했다."
  }],
  "agents": [{
    "role": "harness_qa",
    "task_id": "contract-example-check",
    "status": "completed",
    "summary": "계약 예시를 검증했다."
  }],
  "checks": [{
    "name": "하네스 구조 검사",
    "status": "passed",
    "required": true,
    "evidence": "tests/test_academic_contracts.py"
  }],
  "academic_sources_changed": [],
  "issues": [],
  "publication": {
    "major": true,
    "pdf_required": true,
    "reason": "마일스톤 완료 보고"
  }
}
```

공개 보고서는 원시 프롬프트, 숨은 추론, 비밀값, 학생정보, 서버 주소와 로컬 절대 경로를 포함하지 않는다.
# 근거 미리보기 계약 1.0.0

## EvidencePreview

[계약](../../contracts/evidence-preview.schema.json)은 승인된 규칙과 원본 해시,PDF/인쇄 페이지,
인용문,위치 검증 정밀도,표시 이미지와 PDF URL을 반환한다. `exact`는 유일한 인용문 또는
표의 학과·입학년도·열·학점 교차 위치만 표시한다. `page_only`는 수기·중복·병합 등 위치
불확실성을 뜻하며 학사 근거 부족 상태와 혼동하지 않는다. 원본 파일 위치나 학생 자료는
응답에 포함하지 않는다. 예시는 아래와 같으며 실제 원본 해시로 교체해 검사한다.

```json
{"schema_version":"1.0.0","rule_id":"approved.rule.id","source_id":"approved.source.id","source_sha256":"0000000000000000000000000000000000000000000000000000000000000000","pdf_page":1,"printed_page":null,"quote":"승인된 인용문","location":"PDF p.1","precision":"page_only","notice":"정확한 위치는 확인되지 않음","image_url":"/v1/academic/evidence/approved.rule.id/preview.png?evidence_index=0&pdf_page=1","pdf_url":"/v1/academic/evidence/approved.rule.id/preview.pdf?evidence_index=0&pdf_page=1"}
```

# 성적표 계약 1.0.0

호환 확장: 정책 `AcademicChatRequest`와 성적표 `TranscriptFollowupRequest`에 선택적
`response_style="friendly"`를 추가한다. 기본 null은 이전 요청 직렬화에서 생략한다.
성적표 후속 요청은 최대200자의 `previous_question`도 선택적으로 받는다. 이전 질문은
승인 결과가 아니며 현재 자료와 규칙으로 재검증한다. `/answers`와 CLI에는 이 필드가 없다.

정책 후속 응답의 `conversational_answer`는 안내 표현이고 `presentation_claim_ids`는
지원된 `applied_rules`와 같은 전체 순서다. 거절에는 빈 배열만 허용한다. 최대500자의
`context_question`은 지원된 독립 질문만 담는다. 성적표 후속 응답도 최대8000자
`conversational_answer`, 최대200자 `context_question`, `context_used`를 선택적으로
제공하되 `selected_checks`와 별도의 항목별 근거 상태를 유지한다. 모호하거나 비지원인
응답을 다음 질문의 승인된 문맥으로 만들지 않는다. 스타일을 생략한 이전 응답은 새
null 필드를 싣지 않는다. 자세한 동작은 [대화 안내](../operations/conversation-prototype.md)와
[ADR0022](decisions/0022-friendly-volatile-conversation.md)에 기록한다.

후속 호환 필드: `TranscriptCourse.review_flags=[]`는 재수강·동일/대체·소급·인정 미확인의 보류 신호다. 응답의 `verification_items`는 과목행/체크ID와 확인 행동을 연결하고, `credit_summary`는 입력PASS·조건부졸업인정·미확인PASS학점을 구분한다. 같은 문제 행은 미확인 합계에서 중복 세지 않는다. 후속 응답의 `focus_check_ids`·`verification_items`는 화면 이동용이며 학사 승인 권한이 아니다. 개인 미확인 패킷과 정책 패킷 분리를 유지한다.

- `TranscriptExtraction`: 식별정보 없는 과목 후보·감지 입학년도·고정 학과·미확인 안내와 확인 필요 표시. 추출 결과는 승인된 학생 사실이 아니다.
- `TranscriptAssessmentRequest`: 명시적으로 확인한2026 적용·전공 이수유형·전체 내역 여부·과목 배열. 이름/학번 필드는 금지한다.
- `TranscriptAssessmentResponse`: 취득/졸업인정 학점·미충족/충족/확인 필요 항목·항목별 EvidencePacket. `official_graduation_decision`은 항상 false. 확인 필요 학생 packet은 기존 계약대로 적용규칙·인용을 비운다. 별도의 `policy_packet`은 학생 판정이 아닌 승인된 기준 설명만 지원하며 학생 사실은 비운다.
- `TranscriptFollowupRequest`: 확인된 동일 이수내역과 제한된 후속 질문.
- `TranscriptFollowupResponse`: 선택한 항목·근거·부분 안내. 정해진 질문 외 개인 예외는 근거 부족이다.

계약 실행 기준은 각각 `contracts/transcript-*.schema.json`과 제품의 `transcript_models.py`다. [운영 안내](../operations/transcript-prototype.md)에서 입력과 계산 범위를 설명한다. 학사 RuleFact 승인 상태는 성적표 확인으로 바뀌지 않는다.

```json
{
  "schema_version": "1.0.0",
  "admission_year": 2026,
  "matched_curriculum_year": 2026,
  "department": "컴퓨터공학과",
  "degree_track": "single_major",
  "confirmed": true,
  "record_complete": false,
  "courses": [{"row_id":"demo-1","course_code":"CDA0088","course_name":"심층상담","credits":0,"grade":"S","category":"major_required"}]
}
```
