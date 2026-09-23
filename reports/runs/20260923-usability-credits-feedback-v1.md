# 학사조교 사용성·학점계산·피드백 기능 완료 보고서

- 실행 ID: `20260923-usability-credits-feedback-v1`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0+codex-harness.79b82281`
- 브랜치: `main`
- 기준 커밋: `58739b456ebcfb38b4e4ba370e59ca9b24523272`
- 결과 커밋: `b61bc9bd3f15ea7d90d7036c85ee824094aa3b82`

## 요청

초기 프로토타입에 대해 실제 학생 질문 형태 20~30개를 검증하고, 학점 입력 및 부족 학점 계산 UI와 지원하지 못한 질문을 수집하는 피드백 기능을 추가한다.

## 요약

학생이 실제로 물을 법한 비식별 질문 30개를 고정 파일럿으로 구성해 22개 근거 지원, 6개 근거 부족, 2개 범위 밖 상태와 학점 부족분 계산 4개를 검증했다. 웹 화면에는 승인된 11개 학점 metric의 선택 입력을 추가해 기존 결정적 코어가 부족분을 계산하도록 했다. 근거 부족·충돌 답변은 개인정보 부재 확인과 저장 버튼을 명시적으로 누른 경우에만 Git 제외 로컬 JSONL에 기록하며, 서버가 질문을 다시 판정해 packet ID와 상태가 일치할 때만 저장한다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| added | `evaluations/academic-usability-pilot.json` | 학점·논문·전과·재수강·보류 주제·개인 판정·범위 밖을 포함하는 비식별 학생 질문 시나리오 30개를 추가했다. |
| added | `scripts/validation/validate_academic_usability.py` | 30개 시나리오의 상태·의도·근거 유무와 4개 학점 부족분을 결정적으로 검증한다. |
| changed | `src/academic_assistant/web/` | 11개 비식별 이수학점 입력, 부족분 표시와 근거 부족·충돌 답변의 명시적 피드백 동의 화면을 추가했다. |
| added | `src/academic_assistant/feedback.py` | 사용자가 동의한 보완 질문만 Git 제외 로컬 JSONL에 append-only 방식으로 저장한다. |
| security | `src/academic_assistant/api.py` | 피드백 저장 전에 현재 엔진으로 질문을 다시 판정해 packet ID와 비지원 상태를 검증하고 PII·동의 누락·supported·범위 밖 요청을 거절한다. |
| added | `contracts/academic-feedback-request.schema.json` | 동의, 질문, 당시 학점 합계, packet ID, 비지원 상태와 보완 유형을 제한하는 피드백 요청 계약을 추가했다. |
| added | `contracts/academic-feedback-response.schema.json` | 질문을 반향하지 않고 feedback ID와 저장 여부만 반환하는 응답 계약을 추가했다. |
| documentation | `reports/evaluations/2026-web-usability-pilot.md` | 30개 질문 파일럿의 구성, 결과 분포와 재현 명령을 공개용으로 정리했다. |
| documentation | `docs/harness/decisions/0011-opt-in-local-feedback.md` | 자동 수집을 금지하고 명시적 동의가 있는 근거 부족·충돌 질문만 로컬 저장하는 결정을 기록했다. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| main_orchestrator | sequential-implementation-and-verification | completed | 서브에이전트 스레드 한도로 새 자식을 만들 수 없어 메인 세션이 탐색, 구현, 브라우저 검증과 전체 회귀 검사를 순차 수행했다. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 전체 단위·통합 테스트 | yes | passed | python -m unittest discover -s tests -p test_*.py -q | 172개 테스트가 통과했다. |
| 기존 학사 답변 회귀 | yes | passed | python scripts/validation/validate_academic_answer_engine.py --project . | 기존 184개 평가가 모두 통과했다. |
| 30개 질문 사용성 파일럿 | yes | passed | python scripts/validation/validate_academic_usability.py --project . | 22개 supported, 6개 insufficient_evidence, 2개 out_of_scope와 학점 계산 4개가 모두 예상 결과와 일치했다. |
| 실제 브라우저 학점 계산 | yes | passed | - | 졸업 총학점 120을 입력한 질문에서 기준 130, 부족 10학점과 승인 규칙·PDF 근거가 표시됐다. |
| 실제 브라우저 피드백 동의 | yes | passed | - | PCCP 근거 부족 답변에서 피드백 영역이 표시됐고 동의 전 저장 버튼은 비활성, 동의 뒤에만 활성화됐다. |
| 피드백 개인정보·무결성 | yes | passed | - | 임시 경로 통합 테스트에서 정상 요청만 한 줄 저장되고 동의 누락, supported, PII와 변조 packet ID는 422로 거절됐다. |
| 하네스·계약·문서 동기화 | yes | passed | - | 하네스 구조, manifest와 JSON Schema, 학사 지식·검수·연구 격리와 공개 보고 검사가 모두 통과했다. |
| 패키징과 JavaScript | yes | passed | - | academic-assistant 0.3.0 wheel에 피드백 모듈과 웹 자산이 포함됐고 JavaScript 문법 검사가 통과했다. |
| 새 네이티브 독립 리뷰 | no | not_run | - | 현재 부모 작업이 보유한 기존 자식 스레드 수가 런타임 한도에 도달해 새 리뷰·QA 서브에이전트를 생성할 수 없었다. 메인 순차 검증 결과와 구분해 기록한다. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **info**: 30개 질문은 실제 학생 대화 로그가 아니라 학생 표현을 재현한 비식별 초기 시나리오이며, 명시적 피드백이 축적되면 별도 검수 후 평가셋을 갱신해야 한다.
- **warning**: 공모전·캡스톤·졸업작품·PCCP는 계속 공식 근거가 없어 insufficient_evidence로 유지하며 피드백 기록 자체는 학사 근거가 아니다.
- **info**: 범위 밖 응답은 원래 학과 값을 반향하지 않는 보안 정책 때문에 피드백 저장 대상으로 받지 않는다.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
