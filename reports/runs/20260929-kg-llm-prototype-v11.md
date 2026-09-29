# 2026학번 학사 KG·선택적 LLM 초기 프로토타입 완료 보고

- 실행 ID: `20260929-kg-llm-prototype-v11`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0+codex-harness.79b82281`
- 브랜치: `codex/kg-llm-prototype`
- 기준 커밋: `b44c9a9395e28fe913b0a079414b17a88d75b6ed`
- 결과 커밋: `5453e025a374701cb733761c5111f999b82dd717`

## 요청

학사 답변 범위 오류 수정, 학과 규정 원본 조사, 기존 교수님 PC LLM·지식그래프 구현 조사, 승인 근거 기반 KG와 선택적 LLM 초기 프로토타입을 구축한다.

## 요약

승인된 2개 출처와 26개 규칙을 68개 노드·115개 관계의 로컬 그래프 번들로 투영했다. 한국어 학사 답변의 범위·보류 경계를 강화하고, 선택적 로컬 LLM은 비식별 주제 코드로 의도 표현만 제안하도록 제한했다. 새 피드백은 질문 원문 없이 메타데이터만 보존한다. 실제 교수님 모델과 Neo4j 서버 연결은 검증하지 않았다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| added | `src/academic_assistant/kg.py` | 승인 Registry만 그래프 노드·관계와 근거 해시로 결정적으로 투영하고 변조·미승인 자료를 거절한다. |
| added | `scripts/knowledge/build_graph.py` | Git 제외 로컬 그래프 번들을 만들고 전용 빈 DB가 명시된 경우에만 Neo4j 적재를 허용한다. |
| added | `src/academic_assistant/llm.py` | 기본 비활성 루프백 LLM 어댑터에 고정 신호·승인 의도 ID만 보내고 응답을 단일 ID로 제한한다. |
| added | `src/academic_assistant/grounded_chat.py` | LLM 제안이 결정적 학사 답변의 상태·문장·규칙·인용을 변경하지 못하는 채팅 경계를 추가한다. |
| changed | `src/academic_assistant/api.py` | 기존 답변 API와 공통 코어를 쓰는 선택적 채팅 API를 추가하고 개인정보·범위 검증을 공유한다. |
| fixed | `src/academic_assistant/core.py` | 타전공·미검증 운영요건·포괄 졸업 판정·재수강 소급 질문을 근거 없이 지원하지 않고 정상 학사 표현의 이름 오탐을 줄인다. |
| security | `src/academic_assistant/feedback.py` | 동의한 새 피드백도 질문 원문을 저장하지 않는 기록 2.0.0으로 바꾸고 구형 기록의 안전한 집계 읽기를 유지한다. |
| changed | `src/academic_assistant/web/index.html` | 웹 피드백 안내를 원문 비보존 정책과 일치시킨다. |
| added | `contracts/academic-chat-response.schema.json` | 결정적 답변과 비권위 LLM 제안의 분리된 응답 계약을 정의한다. |
| changed | `evaluations/academic-answer-mvp.json` | 범위·보류 요건·개인정보·일반 학사어·소급 질문을 포함해 고정 평가를 244건으로 확장한다. |
| documentation | `reports/source-audits/2026-department-regulations-pending.md` | 학과 원본 게시물을 대조하고 PCCP 점수·캡스톤 선후관계·면제 해석을 사람 검수 전까지 보류한다. |
| documentation | `docs/operations/kg-llm-prototype.md` | 로컬 그래프 생성, 선택적 LLM 설정, 실 연결 미검증과 학사 근거 경계를 설명한다. |
| documentation | `docs/harness/decisions/0012-kg-and-llm-advisory-boundary.md` | 승인 근거 그래프와 비권위 LLM 의도 제안의 역할 경계를 기록한다. |
| documentation | `docs/harness/decisions/0013-no-raw-feedback-retention.md` | 자유형 이름 탐지의 한계와 피드백 원문 비보존 결정을 기록한다. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| harness_explorer | existing-impl-audit | completed | 이전 KG 저장소와 나루 프로젝트의 교수님 PC 모델 연결 기록을 읽기 전용으로 조사했다. |
| academic_source_auditor | source-audit | completed | 학과 코딩 테스트·캡스톤·논문 게시물의 신원과 미확인 적용 관계를 분리했다. |
| harness_worker | answer-boundary-kg-llm | completed | 결정적 답변 경계, 승인 근거 KG, 비권위 LLM 제안과 웹/API 통합을 구현했다. |
| harness_reviewer | independent-review | completed | 학사 근거·개인정보·피드백·그래프·모델 경계를 독립 검토하고 발견된 회귀를 재검증했다. |
| harness_qa | qa | completed | 220개 단위 테스트와 244개 고정 평가 및 synthetic API·피드백·KG·모의 LLM 경계를 검증했다. |
| main_orchestrator | integration-and-publication | completed | 계약·설계 문서·개인정보 최소화·하네스 통합과 공개 보고를 맡았다. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 전체 단위 테스트 | yes | passed | .venv/Scripts/python.exe -m unittest discover -s tests -p test_*.py -q | 220개 테스트 통과. |
| 고정 학사 답변 평가 | yes | passed | .venv/Scripts/python.exe scripts/validation/validate_academic_answer_engine.py --project . | 244개 평가 통과; 지원 답변의 승인 규칙과 인용 연결 및 비지원 경계 확인. |
| 학사 지식·조사 격리·사용성 | yes | passed | - | 지식·질문 패킷·미검증 조사 격리와 비식별 웹 파일럿 30건 검사가 통과했다. |
| 하네스·독립 리뷰·QA | yes | passed | .venv/Scripts/python.exe .agents/skills/harness/scripts/validate.py --project . --run 20260929-kg-llm-prototype-v11 --complete | 실제 agent ID가 등록된 구현·독립 리뷰·QA 장부에서 완료 오류 0건; manifest·계약·문서 동기화 통과. |
| 피드백 원문 비보존 | yes | passed | - | 격리된 synthetic 요청에서 새 2.0.0 로컬 기록에 질문 필드가 없고 구형 1.0.0 기록 8건의 집계 호환을 확인했다. |
| 그래프와 모의 LLM | yes | passed | - | 승인 출처 2건·규칙 26건의 로컬 그래프 68노드·115관계 일치; 모의 LLM은 원문 없이 고정 주제 코드만 받았다. |
| GitHub Actions PR 검사 | yes | passed | - | PR #2의 Harness CI validate가 코드 커밋 5453e025에 대해 성공했다. |
| 실제 교수님 모델 연결 | no | not_run | - | 서버 상태만 읽기 전용으로 점검했고 별도 학사 모델 호출은 로컬 실행 정책에 막혀 수행하지 않았다. 기존 나루 서비스는 변경하지 않았다. |
| 실제 Neo4j 적재 | no | not_run | - | 전용 빈 DB와 승인된 접속 대상이 없어 로컬 번들 및 모의 적재 검증까지만 수행했다. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **warning**: PCCP 400점·캡스톤 선후관계·졸업작품·공모전과 학과 내규 면제 해석은 사람 검수 전까지 답변 근거로 사용하지 않는다.
- **warning**: 실제 교수님 Gemma와 Neo4j 서버 연결은 아직 검증되지 않았다. 로컬 그래프와 모의 모델 검증은 이를 대신하지 않는다.
- **info**: 자유형 이름 탐지는 완전하지 않으며 명시적 식별자 차단, 질문 원문 비보존과 모델 비전송을 함께 적용한다.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
