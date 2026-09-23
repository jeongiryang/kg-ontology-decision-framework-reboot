# 하네스 워크플로

## 최초 설치

Windows의 심볼릭 링크 제약을 피하기 위해 WSL에서 업스트림을 체크아웃하고 Windows 프로젝트 경로(`/mnt/c/...`)를 대상으로 설치한다.

```bash
git clone https://github.com/jeongiryang/codex-harness.git
cd codex-harness
git checkout 79b82281d305c89181fbb216499d5f1e962c14ed
python3 scripts/install.py --target /mnt/c/path/to/kg-ontology-decision-framework-reboot --dry-run
python3 scripts/install.py --target /mnt/c/path/to/kg-ontology-decision-framework-reboot
```

설치 뒤 `AGENTS.md`, `.codex/config.toml`, `.codex/agents/`, `.agents/skills/harness/`를 확인한다. 설치본을 단순 링크로 호출하는 것이 아니라 프로젝트에 복사해 버전 관리한다. 재설치는 프로젝트 도메인 블록과 맞춤 파일을 덮어쓰지 않는지 dry-run diff로 먼저 확인한다.

## 실행 초기화와 배정

1. 메인은 목표, 작업, 의존성, `ownership`, `inputs`, 결정, 스킬과 완료 기준이 담긴 계획 파일을 작성한다.
2. `run.py init`으로 새 실행을 만든다. 과거 실행 장부는 수정하지 않는다.
3. `run.py ready`로 의존성이 충족된 작업만 확인한다.
4. 첫 자식은 제품 파일을 읽거나 쓰지 않는 대기 지시로 생성한다.
5. 실제 agent ID가 반환되면 `run.py start`로 해당 작업과 연결한다.
6. 갱신된 `input.md`를 실제 후속 메시지로 전달한다. 필요한 경우에만 추가 자식을 만들며 최대 3개를 넘지 않는다.

```bash
python .agents/skills/harness/scripts/run.py --project . init \
  --plan-file /absolute/path/to/plan.json --run-id example-v1
python .agents/skills/harness/scripts/run.py --project . ready --run example-v1
python .agents/skills/harness/scripts/run.py --project . start \
  --run example-v1 --task source-audit --agent-id ACTUAL_AGENT_ID
```

`run.py`는 로컬 장부만 갱신한다. 자식 생성·메시지 전송·중단에는 현재 런타임의 네이티브 도구가 별도로 필요하다.

## 결과·리뷰·QA

작업자는 산출물, 실제 검사와 문제를 `TaskResult` 형식으로 반환한다. 메인은 결과 파일을 보존하고 `run.py result`로 등록한다. 완료 결과에는 최소 하나의 필수 검사가 실제 근거와 함께 통과해야 한다.

구현 산출물은 생산자와 다른 관점에서 검토한다. 리뷰어는 제품 파일을 고치지 않고 재현 가능한 결함을 반환한다. QA는 실제 사용자 경계의 출력과 오류 동작을 확인하며, 제품 수정은 워커에게 되돌린다. 수정 후 영향받는 검사를 다시 실행한다.

## 사용자 질문형 학사 검수

1. 출처 감사와 규칙 모델링 결과에서 원문으로 해결되지 않는 모호성만 `academic_review_facilitator`에 전달한다.
2. 촉진자는 읽기 전용으로 근거, 2~3개 선택지, 권장안, 영향과 차단 범위를 만들며 사용자에게 직접 묻지 않는다.
3. 메인은 세션 시작 시 이번 세션의 명시적 답변을 `department_confirmation`으로 기록할지 한 번 확인한다.
4. 권한이 부여된 뒤 한 배치 최대 3개 질문을 제시하고 사용자 원문과 선택을 함께 기록한다. 질문 생성 당시 검수대장·SourceEntry·RuleFact 해시와 subject별 권위·정확한 locator·규칙 문장을 고정하며, 변경되면 기존 답변을 적용하지 않고 질문을 재생성한다.
5. 모델러는 유효한 같은 세션 권한 사건과 응답이 있는 대상만 반영한다. 무응답·모호한 답변·보류는 해당 규칙만 `needs_review`로 남긴다.
6. 상위 근거 충돌은 사용자 답변으로 덮어쓰지 않고 관련 subject와 양쪽의 등록 출처·locator·claim을 연결한 `conflict`로 보존한다.
7. 질문 제시와 응답 기록은 질문·응답 ID가 있는 세션 내 감사 사건으로 남기고, `draft`를 포함한 패킷·배치·질문 상태가 실제 권한과 응답 수에 일치하는지 검사한다.
8. 세션 종료 시 권한을 만료하고 새 대화나 일반 발언에 재사용하지 않는다.
9. 적용이 끝나면 질문 당시 `input_snapshots`를 보존하고 승인 반영 뒤 객체 해시를
   `application.output_snapshots`에 별도로 기록한다.
10. 적용 검증은 현재 SourceEntry·RuleFact의 `review`만 승인 전 상태로 복원한 해시를 입력
    스냅샷과 대조한다. 승인 뒤 판정·수치·적용범위·관계·근거를 바꾸면 출력 해시를 함께
    갱신해도 실패한다.
11. 사용자 응답 전체와 각 대상의 검수 메타데이터는 별도 적용 attestation으로 고정한다.
    사용자 원문, 검수자, 시각 또는 승인 근거가 바뀌면 해당 attestation과 불일치해 실패한다.

## 미확인 운영요건 조사

공식 자료가 없는 사용자 기억은 `AcademicResearchItem`으로만 기록한다. 해당 claim에는
`unverified_user_recollection`, `awaiting_official_source`, `eligible_for_academic_answer=false`를
고정하고 공개 GitHub 조사 이슈와 상호 연결한다. 이 상태에서는 RuleFact·SourceEntry·인용으로
승격하지 않으며 질문에는 `insufficient_evidence`를 반환한다. 공식 학과 안내, 교과목 운영계획
또는 승인된 내부 규정을 확보하면 별도 출처 감사와 새 검수 세션을 시작한다.
기존 승인 RuleFact에 같은 주장을 패러프레이즈해 넣는 변경도 승인 전 의미 해시 잠금으로
차단한다. 새 규칙은 기존 질문 패킷에 없는 대상이므로 새 검수 세션 없이 승인될 수 없다.

## 결정론적 학사 답변

FastAPI와 CLI는 `academic_assistant` 코어를 공동 사용한다. NFKC와 제한된 문자·공백 정규화 뒤
등록된 alias만 일치시키며 fuzzy matching, stemming, 모델 추론은 사용하지 않는다. 안전 검증,
범위 불일치, PCCP·캡스톤·공모전·졸업작품 보호 주제, 포괄적 졸업 인증, 승인 alias 순서로
판정한다. 부족 학점은 선택된 credit metric마다 `max(0, required-earned)`만 계산하고 합계를
추론하지 않는다. `python scripts/validation/validate_academic_answer_engine.py --project .`로 정확히
184개 회귀 사례와 응답 계약을 검사한다. 이 중 새 사례는 조교 확인을 거친 교양·전공·0학점
필수·전과·졸업논문·재수강·동일/대체교과목 정책과 개인 판정 실패 폐쇄 경계를 포함한다.

CLI의 MVP 단축 범위는 `--year` 하나를 입학연도와 매칭 교육과정 연도에 함께 적용한다.
학생별 학점은 반복 가능한 `--credits METRIC=VALUE`로 받는다. 호환용 장기 옵션을 함께 쓰는
경우 연도 값 충돌이나 metric 중복은 구체 값을 노출하지 않는 입력 오류로 종료한다.

레지스트리는 서비스 과정에서 한 번 적재하며 모든 canonical digest를 신뢰 pin과 대조한다.
출처 적용 조건에서 아직 해결되지 않은 복학·재입학·편입·경과조치와 등록된 변형은 일반 규칙을
적용하지 않고 `insufficient_evidence`로 종료한다. 전과는 승인된 최초 입학연도 정책만 답하고,
전과 완료 연도를 교육과정 연도로 사용하지 않는다. 부정·대비 또는 alias의 어휘 내부 삽입도 보류하되,
명시적인 `및` 연결은 여러 구체 intent를 함께 선택할 수 있다. 보호된 미검증 주제는 알려진
학점 metric의 관련성 검사보다 먼저 격리한다.

Alias는 독립 token, 공백으로 나뉜 승인 구문 또는 허용된 조사 경계에서만 일치한다. 따라서
`전선풍기`, `전공필수품` 같은 무관한 어휘 내부에서는 intent를 선택하지 않는다. `또는`,
`혹은`, `중 하나` 같은 선택형 질문은 전용 승인 규칙이 없으므로 `insufficient_evidence`로
보류하고, `및`으로 명시한 결합 질문만 각 구체 규칙을 함께 적용한다.

짧은 별칭 `전선`은 `학점`, `전공`, `이수` 중 하나가 같은 질문에 있을 때만
전공선택으로 해석한다. 둘 이상의 구체 intent가 보이면 인접한 모든 별칭 사이에 `및`,
정확한 token `그리고` 또는 받침에 맞는 `와/과`가 있어야 한다. 공백·구두점·`vs`·비교 표현이나 끝나지
않은 목록은 규칙을 적용하지 않고 `insufficient_evidence`로 보류한다.
인접한 두 별칭 사이의 공백 제거 문자열 전체가 정확히 승인 접속사여야 하므로 접속사 앞뒤에
임의 문구가 붙은 경우도 승인하지 않는다. 세 개 이상을 연결할 때도 모든 인접 구간이 각각
이 조건을 충족해야 한다.

### localhost 웹 프로토타입

동일 FastAPI 프로세스가 `/`, `/assets/app.css`, `/assets/app.js`의 고정 UI 경로로 no-build
웹 UI를 제공한다. 화면은 2026학번·2026 교육과정·컴퓨터공학과를 읽기 전용 범위로 표시하고
질문만 입력받아 기존 `POST /v1/academic/answers`에 같은 출처의 JSON으로 전달한다. 학점이나
개인 사실을 임의로 받거나 저장하지 않으며 브라우저 저장소, 쿠키, 분석기, 외부 자산을 쓰지 않는다.
기존 `/openapi.json`, `/docs`, `/redoc`은 API 호환성을 위해 유지하되 UI·정적 자산 경로는
OpenAPI `paths`에서 제외한다.

UI는 `supported`, `insufficient_evidence`, `conflict`, `out_of_scope`를 서로 다른 상태로
표시한다. 계산, 적용 규칙, 문제와 근거 locator는 API 응답에 실제로 존재할 때만 DOM 노드를
생성해 보여 주며 API 값을 HTML로 해석하지 않는다. PCCP·캡스톤·공모전·졸업작품 및 개인별
면제·소급 판정은 기존 실패 폐쇄 경계를 유지한다. 모든 페이지·자산·API 응답에는 CSP,
`Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`를
적용한다. 프로토타입과 API는 self-only CSP를 유지하고 Swagger/ReDoc 문서 응답에만 해당
문서가 사용하는 고정 CDN과 inline 초기화 스크립트를 제한적으로 허용한다. 배포 서버는 공개
인터페이스가 아닌 `127.0.0.1`에 바인딩한다.

웹 회귀 검사는 접근 가능한 입력 label과 live region, 고정 요청 범위, 네 상태 표현, 안전한
`textContent` 렌더링, 근거 표시, 선택적 학점 입력, 명시적 피드백 동의와 wheel 자산 포함을 확인한다.

학점 입력은 승인된 11개 credit metric의 비식별 합계만 선택적으로 받는다. 빈 항목은 전송하지
않고 질문에서 선택된 규칙과 관계없는 metric은 기존 엔진이 422로 거절한다. 부족분은 기존
결정적 코어의 `max(0, required-earned)` 계산을 그대로 사용하며 UI가 별도 합계를 추론하지 않는다.

근거 부족·충돌 답변의 보완 요청은 자동 수집하지 않는다. 사용자가 개인정보가 없음을 확인하고 저장
버튼을 누를 때만 `POST /v1/academic/feedback`이 호출되며, 서버는 PII 패턴과 상태·동의 계약을
검증하고 현재 엔진으로 packet ID와 비지원 상태를 재현한 뒤 Git 제외 경로
`.local/academic-feedback/feedback.jsonl`에 append-only JSONL로 남긴다.
응답에는 질문을 되돌려 보내지 않는다. `supported` 답변은 피드백 수집 대상이 아니다.
저장 함수는 해석된 경로가 `ACADEMIC_FEEDBACK_PRIVATE_ROOT` 아래의 파일인지 다시 검사하며,
기본 내부 파일럿은 프로젝트 `.local` 밖의 목적지를 허용하지 않는다. 운영자는
`academic-assistant feedback-summary --json`으로 질문 원문 없이 상태·분류·중복 건수를
확인한다. 손상·식별정보 포함 레코드가 하나라도 있으면 부분 집계를 출력하지 않는다.

학생 질문 형태를 재현한 30개 비식별 파일럿은
`python scripts/validation/validate_academic_usability.py --project .`로 검증한다. 22개 supported는
승인 규칙과 근거를 필수로 갖고, 6개 insufficient_evidence와 2개 out_of_scope는 규칙·인용이
비어 있어야 한다. 이는 실제 학생 로그 분석 결과가 아니라 초기 사용성 시나리오다.

학·석사 연계과정의 졸업논문 면제는 가능성만 설명하고 개인의 자동 면제로 확정하지 않는다.
`저는 연계과정이라 졸업논문 면제인가요?`처럼 개인 토큰과 축약된 연계과정·논문/면제 문맥이
한 문장에 함께 있으면 공식 학적 확인이 필요한 개인 판정으로 보고 근거 없이 보류한다.
재수강은 기이수 과목 삭제로, 동일교과목은 중복 수강신청 불가로 각각 중복 계산하지 않는다.
이수 뒤 동일·대체 지정은 별개 과목으로 계산하지만 소급 적용의 개인 결과는 공식 학적 확인 전
`insufficient_evidence`로 종료하며, 비지원 응답에는 규칙이나 인용을 싣지 않는다.

## 실패와 부분 재개

하네스가 기록하는 프로젝트 상대 경로는 운영체제와 관계없이 `/` 구분자를 사용하는 POSIX 형식으로 정규화한다. 따라서 Windows에서 생성한 실행 상태도 Linux/WSL에서 재개할 수 있고, 역방향 재개도 같은 계약을 따른다.

- 일시적 작업 오류는 원인을 확인한 뒤 기본 1회 재시도한다.
- 필수 작업의 실패·미실행은 완료를 막는다. 선택 작업 생략은 이유와 영향을 기록한다.
- 첫 네이티브 생성에서 실제 ID 없이 실패하면 추가 생성을 중단하고, 순차 대체 작업과 네이티브 실행 검증을 구분한다.
- 입력 파일 내용만 바뀌면 `resume`으로 새 실행을 만들 수 있다.
- 목표, 작업 구성, 소유권, 결정 또는 완료 기준이 바뀌면 새 계획 파일과 새 실행 ID를 사용한다.
- 승인된 산출물이 후속 단계에서 변경되면 생산자와 소비자의 관련 결과를 무효화하고 재검증한다.

```bash
python .agents/skills/harness/scripts/run.py --project . resume \
  --run example-v1 --new-run example-v2
```

## 완료와 보고

1. 실제 유휴·중단·종료 응답을 에이전트 수명 상태와 함께 기록한다.
2. `validate.py --project . --run <id> --complete`로 필수 결과, 지문, 산출물, 검사 근거를 확인한다.
3. [보고 정책](reporting.md)에 따라 `CompletionReport` 기반 Markdown·JSON을 만들고, 중대 변경이면 PDF도 만든다.
4. 최종 응답에서 변경 파일, 검사 결과, 미실행 검사, 공개 보고서, 커밋 또는 PR을 연결한다.

구조 검증 통과는 실제 에이전트 발견·샌드박스 적용·병렬 실행의 증거가 아니다. 실제 네이티브 스모크 결과는 별도로 보고한다.

## 계약·문서 동기화 검사

CI는 `harness-manifest.yaml`을 구조화된 색인으로 사용해 다음 실행 기준을 대조한다.

- manifest의 역할명·경로와 `.codex/agents/*.toml`의 `name`
- manifest의 스킬명·경로와 각 `SKILL.md` frontmatter의 `name`
- manifest의 계약별 버전·경로와 `contracts/*.schema.json`의 `title`, `schema_version`, `$id`
- 모든 JSON Schema의 Draft 2020-12 meta-schema 적합성
- manifest의 동시 실행 한도와 `.codex/config.toml`
- manifest에 등록된 설계 문서의 실제 존재 여부

```bash
python scripts/validation/validate_harness_sync.py --project .
```

학사 출처·규칙·관계·검수 큐는 별도 의미 검증기로 확인한다. 스키마 통과만으로 승인된 근거,
존재하는 관계 대상 또는 전수 검수 완료를 주장하지 않는다.
또한 학생 답변을 `supported`로 공개하기 전에는 `validate_evidence_packet`으로 규칙 해시,
승인 상태, 입학연도 범위와 인용 연결을 현재 저장소 지식과 대조한다.

```bash
python scripts/validation/validate_academic_knowledge.py --project .
python scripts/validation/validate_academic_clarifications.py --project .
python scripts/validation/validate_academic_research.py --project .
```

`AGENTS.md`, `harness-manifest.yaml`, `.codex/config.toml`, 에이전트·스킬·계약, 하네스 CI workflow, `scripts/reporting/` 또는 `scripts/validation/`이 바뀌면 같은 변경에서 `docs/harness/` 설명과 ADR을 갱신해야 한다. 이 freshness 정책의 실제 판정은 CI 검사 코드가 담당한다.

GitHub용 Mermaid는 코드 펜스 개수만 세지 않는다. CI가 고정된 `@mermaid-js/mermaid-cli@11.12.0`으로 `docs/harness/`의 모든 Mermaid 블록을 SVG로 실제 렌더링한다.

```bash
python scripts/validation/render_mermaid.py \
  --project . --output-dir /tmp/harness-mermaid-rendered
```
