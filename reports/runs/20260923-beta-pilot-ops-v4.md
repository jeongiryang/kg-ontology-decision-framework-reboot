# 2026학번 학사조교 내부 파일럿 운영 준비 완료 보고서

- 실행 ID: `20260923-beta-pilot-ops-v4`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0+codex-harness.79b82281`
- 브랜치: `main`
- 기준 커밋: `6f46b6a8deced556511d46fc023dd2ef4fe58b40`
- 결과 커밋: `481a8ac58780b7697b30c2914c161fa6404a9728`

## 요청

초기 프로토타입의 남은 작업으로 실제 피드백 검토 체계, 내부 파일럿 준비 검사, 독립 리뷰와 QA를 진행하되 공모전·캡스톤·졸업작품·PCCP는 보류 상태로 유지한다.

## 요약

학생 질문을 영구 저장하지 않는 기존 프로토타입에 동의 기반 비식별 피드백 저장과 집계 전용 CLI를 추가하고, localhost 내부 파일럿의 경로·Git 제외·근거 연결·보류 주제·HTTP 경계를 한 번에 검사하는 준비 도구를 구축했다. 개인정보 또는 손상된 JSONL은 실패 폐쇄하며 원문 질문은 집계 출력에 포함하지 않는다. 184개 테스트, 184개 학사 평가, 30개 사용성 파일럿과 독립 리뷰·QA를 통과했다. 실제 학생 피드백은 아직 수집되지 않았고 공모전·캡스톤·졸업작품·PCCP는 계속 근거 부족으로 보류한다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| added | `src/academic_assistant/feedback.py` | 비식별·동의 기반 피드백 JSONL을 전부 검증한 뒤 상태·범주·중복 수만 집계하고 원문 질문은 노출하지 않는 운영 기능을 추가했다. |
| added | `src/academic_assistant/cli.py` | 피드백 파일이 없으면 0건을 반환하고 손상·개인정보·허용 경로 위반에는 실패 폐쇄하는 feedback-summary 명령을 추가했다. |
| security | `tests/test_academic_feedback_ops.py` | 중복 키, 비표준 JSON, 잘못된 식별자·시각·범위·상태, 한글 이름, 전각 학번, 외부 경로를 거절하고 정상 학사 용어는 허용하는 회귀 검사를 추가했다. |
| added | `scripts/operations/check_pilot_readiness.py` | Python 버전, 비공개 저장 경로, Git 제외, 쓰기 가능성, 승인 근거, 보류 주제, 실제 HTTP 경계를 읽기 전용으로 검사하는 내부 파일럿 준비 도구를 추가했다. |
| added | `config/pilot.env.example` | 프로젝트의 Git 제외 .local 하위만 사용하는 파일럿 피드백 저장 설정 예시를 추가했다. |
| documentation | `docs/operations/internal-pilot.md` | localhost 전용 시작·종료, 비식별 입력, 피드백 검토, 보존·사고 대응과 네트워크 배포 금지 조건을 문서화했다. |
| changed | `.github/workflows/harness-ci.yml` | 내부 파일럿 준비 검사를 지속 통합 완료 조건에 추가했다. |
| documentation | `README.md` | 피드백 집계, 파일럿 준비 검사, 개인정보 경계와 내부 운영 문서의 사용법을 추가했다. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| harness_worker | feedback-ops | completed | 비공개 피드백 저장·집계 기능과 실패 폐쇄 검증을 구현했다. |
| harness_worker | pilot-ops | completed | localhost 내부 파일럿 준비 검사, 설정 예시, 운영 문서와 CI 연결을 구현했다. |
| harness_reviewer | independent-review | completed | 개인정보 정규화, 저장 경로 경계, HTTP 검사와 정상 학사 용어 오탐을 독립 검토하고 보완 결과를 확인했다. |
| harness_qa | qa | completed | 184개 테스트, 184개 학사 평가, 30개 사용성 파일럿, 준비 검사, 하네스·보고·패키징과 보류 주제를 재검증했다. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 전체 단위·통합 테스트 | yes | passed | python -m unittest discover -s tests -p test_*.py -q | 184개 테스트가 통과했다. |
| 학사 답변 평가 | yes | passed | python scripts/validation/validate_academic_answer_engine.py --project . | 184개 질문 평가가 통과했다. |
| 사용성 파일럿 평가 | yes | passed | python scripts/validation/validate_academic_usability.py --project . | 30개 사례가 통과했으며 supported 22건, insufficient_evidence 6건, out_of_scope 2건이었다. |
| 내부 파일럿 준비 | yes | passed | python scripts/operations/check_pilot_readiness.py --project . --json | 비공개 경로, Git 제외, 쓰기 가능성, 승인 근거, 보류 주제와 HTTP 경계 7개 검사가 통과했다. |
| 보류 주제 격리 | yes | passed | - | PCCP·캡스톤·공모전·졸업작품 질문은 모두 insufficient_evidence이며 적용 규칙과 근거가 각각 0건이었다. |
| 하네스 완료 | yes | passed | python .agents/skills/harness/scripts/validate.py --project . --run 20260923-beta-pilot-ops-v4 --complete | 최종 실행 상태가 0개 completion error로 완료됐다. |
| 독립 리뷰와 QA | yes | passed | - | 최종 파일 지문이 일치했고 전체 회귀, 학사·연구 격리, 보고와 패키징 검사가 통과했다. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **warning**: 공모전 수상, 캡스톤디자인, 졸업작품과 PCCP 조건은 공식 학과 근거가 없어 답변 근거로 사용하지 않고 insufficient_evidence로 유지한다.
- **info**: 실제 학생 질문과 피드백은 아직 수집되지 않아 현재 집계는 0건이며, 이번 결과는 운영 준비와 고정 평가 통과를 뜻한다.
- **info**: 현재 시험운영은 localhost 전용이다. 다중 사용자 또는 외부 네트워크 운영에는 인증, 접근 통제, 파일 권한과 용량 제한에 대한 별도 승인이 필요하다.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
