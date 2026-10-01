# 2026 학사조교 초기 프로토타입 통합 결과

- 실행 ID: `20261001-prototype-complete`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0 / upstream 79b82281 / RuleFact 2.1.0`
- 브랜치: `codex/complete-prototype-integration`
- 기준 커밋: `e4ea5d30579a8509ac3912521f816a7551916fd2`
- 결과 커밋: `79ba33aac44ae0d0d5a3461e17737a1258d38c9f`

## 요청

남은 초기 프로토타입 작업을 진행하고 새 검수 세션으로 세 운영 조건을 반영한다. 실제 Gemma 연결·지식그래프·성적표 부분 비교·PDF 표시를 통합하며 사람 UAT는 생략한다.

## 요약

새 세션의 학과 확인 1건과 운영 규칙 3건을 추가하여 승인 규칙 29건·출처 3건을 연결했다. 실제 Neo4j 75노드·124관계와 DSW 나루와 다른 GPU 0의 Gemma 추론을 검증했다. 학생 원문은 모델로 보내지 않고 답변은 승인 규칙의 결정적 코어가 책임진다. localhost 웹·성적표 부분 비교·원본 PDF 빨간 밑줄이 동작한다. 독립 347개 테스트와 실제 로컬 요청 24개가 통과했다. 외부 공개 운영이나 최종 졸업 인증은 이번 완료 범위가 아니다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| documentation | `CHANGELOG.md` | 현재 구현·검증·보류 범위 문서화 |
| documentation | `README.md` | 현재 구현·검증·보류 범위 문서화 |
| changed | `config/academic-intents.json` | 31개 의도와 승인 객체 지문 동기화 |
| changed | `config/academic-registry-pins.json` | 31개 의도와 승인 객체 지문 동기화 |
| changed | `contracts/academic-intent-profile.schema.json` | 운영정책 2.1 계약과 필수 필드 |
| changed | `contracts/rule-fact.schema.json` | 운영정책 2.1 계약과 필수 필드 |
| documentation | `docs/harness/contracts.md` | 현재 구현·검증·보류 범위 문서화 |
| added | `docs/harness/decisions/0018-current-trial-operational-confirmations.md` | 현재 구현·검증·보류 범위 문서화 |
| documentation | `docs/harness/decisions/README.md` | 현재 구현·검증·보류 범위 문서화 |
| documentation | `docs/harness/workflows.md` | 현재 구현·검증·보류 범위 문서화 |
| documentation | `docs/operations/internal-pilot.md` | 현재 구현·검증·보류 범위 문서화 |
| documentation | `docs/operations/kg-llm-prototype.md` | 현재 구현·검증·보류 범위 문서화 |
| changed | `evaluations/academic-reference-patterns.json` | 실행·평가·계약 색인 동기화 |
| changed | `harness-manifest.yaml` | 실행·평가·계약 색인 동기화 |
| added | `knowledge/relationship-corrections.json` | 확인된 운영 조건·출처·관계 교정 계보 등록 |
| added | `knowledge/rules/cwnu.cs.2026.operations.coding-test-failure.json` | 확인된 운영 조건·출처·관계 교정 계보 등록 |
| added | `knowledge/rules/cwnu.cs.2026.operations.graduation-work-prerequisite.json` | 확인된 운영 조건·출처·관계 교정 계보 등록 |
| added | `knowledge/rules/cwnu.cs.2026.operations.pccp-current-trial.json` | 확인된 운영 조건·출처·관계 교정 계보 등록 |
| added | `knowledge/sources/cwnu.cs.2026.department-confirmation-20261001.json` | 확인된 운영 조건·출처·관계 교정 계보 등록 |
| added | `reports/source-audits/2026-operational-relationships-20261001.md` | 현재 구현·검증·보류 범위 문서화 |
| added | `reviews/academic/2026-operational-confirmation-20261001.json` | 새 검수 세션과 기존 이력 보존 |
| changed | `reviews/academic/clarifications/2026-department-practices-20260929.md` | 새 검수 세션과 기존 이력 보존 |
| added | `reviews/academic/clarifications/2026-operational-confirmation-20261001.json` | 새 검수 세션과 기존 이력 보존 |
| added | `reviews/academic/clarifications/2026-operational-confirmation-20261001.md` | 새 검수 세션과 기존 이력 보존 |
| fixed | `scripts/validation/validate_academic_clarifications.py` | 닫힌 승인 계보 및 버전 검증 |
| changed | `scripts/validation/validate_harness_sync.py` | 닫힌 승인 계보 및 버전 검증 |
| changed | `src/academic_assistant/core.py` | 실행·평가·계약 색인 동기화 |
| fixed | `src/academic_assistant/llm.py` | 제한된 냉간 시간·엄격한 카탈로그 출력 |
| changed | `src/academic_assistant/registry.py` | 실행·평가·계약 색인 동기화 |
| changed | `src/academic_assistant/web/evidence.js` | 확인 범위와 비PDF 근거 표시 |
| changed | `src/academic_assistant/web/index.html` | 확인 범위와 비PDF 근거 표시 |
| changed | `tests/test_academic_answer_engine.py` | 승인·보류·인용·모델 경계 회귀 검증 |
| changed | `tests/test_academic_clarifications.py` | 승인·보류·인용·모델 경계 회귀 검증 |
| changed | `tests/test_academic_grounded_chat.py` | 승인·보류·인용·모델 경계 회귀 검증 |
| changed | `tests/test_academic_kg.py` | 승인·보류·인용·모델 경계 회귀 검증 |
| changed | `tests/test_llm_budget.py` | 승인·보류·인용·모델 경계 회귀 검증 |
| added | `tests/test_operational_lineage.py` | 승인·보류·인용·모델 경계 회귀 검증 |
| added | `tests/test_operational_rules.py` | 승인·보류·인용·모델 경계 회귀 검증 |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| dsw_compute_operator (harness_worker fallback) | lab-inference | completed | Gemma GPU 0 실제 추론·본인 프로세스 정리·유휴 반환·로컬 연결. 기존 모델 재사용, 다운로드 없음. |
| harness_worker | implementation | completed | 세 정책 질문 경계·비PDF 인용·승인 지문 및 엄격한 LLM 카탈로그/시간 제한 구현. |
| harness_reviewer (harness_architect fallback) | review | completed | 초기 근거 없는 연결 3개를 발견해 수정 후 재검수. 닫힌 권한·교정 계보·19개 지문 검증; 중대 결함 없음. |
| harness_qa (harness_explorer fallback) | qa | completed | 347개 독립 테스트, 권한/변조 거절, 승인 인용 및 실제 localhost 합성 요청 24개 검증. |
| harness_worker | ui-copy | completed | 세 승인 조건과 보류 경계를 화면에서 구분. 웹 회귀 14개 통과. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 전체 단위 테스트 | yes | passed | .venv/Scripts/python.exe -B -m unittest discover -s tests -p test_*.py | 독립 QA: 347 tests, 15.045 seconds, OK. |
| 학사·성적표·권한 평가 | yes | passed | - | 265 답변, 30 사용성, 15 참고 패턴, 성적표 계약 5개·95 중첩 근거 검증; 미검증 정보 격리 통과. |
| 검수 계보와 관계 교정 | yes | passed | - | 새 세션 종료·권한 만료·원문/응답 지문 검증. 이전 관계만 복원한 3개 객체가 원승인 해시와 일치. 독립 변조 10건 거절, 미승인 졸업논문 연결 0개. |
| 실제 Neo4j 적재·조회 | yes | passed | - | 별도 빈 localhost DB에 75노드·124관계 적재. 그래프 SHA256=859d87392f3c58e95984565c31d6d81087bfd0d4125917184e759716129a90df. 승인 Registry 전체와 실제 웹 조회 일치. |
| 실제 로컬 API | yes | passed | - | 독립 합성 요청 24건: 200 응답 20건, 기대 422 1건·비PDF 404 3건. 정책/보류/성적표 8사례와 104개 정규 근거 패킷 검증. |
| DSW GPU 추론·유휴 반환 | yes | passed | - | 나루 GPU 1과 분리된 GPU 0 실제 추론 성공. 본인 모델만 재사용. 60초 유휴 뒤 적재 모델 0개·GPU 기본 메모리 복귀, 나루 프로세스 보존 확인. |
| 제품 LLM 연결 | yes | passed | - | 최신 웹의 모호한 합성 요청은 원답변·근거 불변, 유효한 null/skipped. 추가 고정 학점/졸업 코드 요청은 11.687초에 credits.graduation.total 반환. 총 2번의 소량 시험이며 정확도 평가가 아님. |
| PDF·화면·성적표 | yes | passed | - | 실제 UI의 원본 PDF 577/인쇄569 페이지에서 컴퓨터공학과·2026·졸업130 교차 위치 빨간 밑줄 확인. 새 확인 기록에는 PDF 버튼 없음. 합성 성적표 회귀·응답 순서·표시 안전 검사 통과. |
| 하네스·Mermaid | yes | passed | - | 9개 역할·8개 스킬 및 manifest/schema 동기화 통과. V3와 UI 실행 완료 게이트 통과; Mermaid 2블록 실제 렌더링. |
| 개발원 직접 UAT | no | not_run | - | 사용자 지시로 생략. 교수님 챗봇의 기존 JSON 평가 자료를 이 제품의 UAT 통과로 취급하지 않음. |
| PDF 정제·전 페이지 검수 | yes | passed | - | CompletionReport에서 MD/JSON/PDF 생성·재생성 일치 및 공개 검사 통과. Poppler로 PDF 4페이지 모두 렌더링해 한글, 표, 줄바꿈, 잘림·겹침·페이지 번호를 확인. |
| GitHub CI | yes | passed | gh pr checks 8 --repo jeongiryang/kg-ontology-decision-framework-reboot | PR #8 코드·보고 커밋 d54f783 검증 성공(1분8초). 실행 36798489718의 단위 테스트·계약·Mermaid·공개 보고 검사 모두 통과. 보고서 상태 갱신 후 최종 커밋도 별도 재검사한다. |

## 학사 근거 변경

- 학과 확인 출처 cwnu.cs.2026.department-confirmation-20261001 추가: 새 세션의 명시적 재확인, 텍스트 원문 SHA-256 고정. PDF 출처로 위장하지 않음.
- 현재 시범 PCCP 400점 이상, 미통과 I U·다음 연도 II 수강 불가, II PASS 후 졸업작품 수강 선행조건만 추가.
- 학사 결정·원응답·닫힌 승인 기록은 보존. 독립 검수로 근거 없는 졸업논문 보완 관계 3개만 삭제하고 이전/현재 지문 연결.

## 이슈 및 남은 작업

- **warning**: 공모전·우수자 면제의 수강/학점 처리, 고정600점·미래 점수 기준, 정확한 제출 학기는 보류. 개인 최종 졸업 인증은 자동화하지 않음.
- **info**: 초기 Vulkan 경로가 잘못된 GPU를 선택한 시도는 본인 프로세스만 중지해 정리하고 CUDA 전용으로 교정. 나루 프로세스 보존을 확인했지만 영향이 전혀 없었다고 보장하지 않음.
- **info**: 초기 리뷰 실패·오래된 QA 완료 등록 거절은 이전 실행에 남겨두고 수정된 V3에서 재검증했다. timeout이 원격 추론 중지를 보장하지 않으며 모델 정답률·운영 부하 검증과 외부 서비스화는 후속 범위.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
