# 2026학번 학사조교 웹 프로토타입 완료 보고서

- 실행 ID: `20260923-web-prototype-final2`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0+codex-harness.79b82281`
- 브랜치: `main`
- 기준 커밋: `df81579`
- 결과 커밋: `c6585b830ba4e69cfc06536641ee13978fdc9768`

## 요청

공모전·캡스톤·졸업작품·PCCP 운영요건은 보류하고 지원 범위를 2026학번 컴퓨터공학과로 제한한 상태에서, 승인된 학사 근거만 사용하는 초기 웹 챗봇 프로토타입을 구축한다.

## 요약

기존 결정적 학사 답변 엔진을 사용하는 한국어 localhost 웹 프로토타입을 추가했다. 질문만 입력하면 supported, insufficient_evidence, conflict, out_of_scope 상태와 적용 규칙·근거 위치·계산·확인 필요 사항을 안전하게 표시한다. 개인별 학석사연계과정 논문 면제 판정은 공식 학적 확인 없이는 근거 없이 거절하며, 공모전·캡스톤·졸업작품·PCCP는 계속 보류한다. 독립 리뷰와 QA를 거쳐 169개 테스트와 184개 고정 평가가 통과했다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| added | `src/academic_assistant/web/` | 2026학번·2026 교육과정·컴퓨터공학과 범위를 고정 표시하고 질문, 답변 상태, 계산, 규칙과 근거를 보여 주는 반응형 한국어 no-build UI를 추가했다. |
| changed | `src/academic_assistant/api.py` | 웹 화면과 정적 자산을 같은 FastAPI 프로세스에서 제공하고 no-store, CSP, nosniff, no-referrer 헤더를 적용했으며 OpenAPI·Swagger·ReDoc 경로를 유지했다. |
| security | `src/academic_assistant/core.py` | 축약된 연계과정 표현을 사용한 개인 졸업논문 면제 질문도 공식 학적 확인 없이는 규칙과 인용 없이 insufficient_evidence로 종료하도록 강화했다. |
| added | `tests/test_academic_web_prototype.py` | 고정 범위, 접근성, 예시 질문, 네 상태 표시, 안전한 DOM 렌더링, 비저장, 보안 헤더, 보류 주제, 문서 호환성과 패키지 자산을 검증한다. |
| changed | `evaluations/academic-answer-mvp.json` | 개인 연계과정 축약 질문과 일반 정책 대조 사례를 추가해 고정 학사 평가를 184건으로 확장했다. |
| changed | `.github/workflows/harness-ci.yml` | 웹 자산 wheel 포함과 184건 학사 평가를 CI 완료 조건에 추가했다. |
| documentation | `README.md` | localhost 웹 프로토타입 실행법, 지원 범위, 비저장·보안 정책과 API 문서 경로를 문서화했다. |
| documentation | `docs/harness/workflows.md` | 웹 프로토타입의 요청 흐름, 근거 표시, 실패 폐쇄, 정적 자산, 보안 헤더와 검증 절차를 하네스 설계에 동기화했다. |
| changed | `harness-manifest.yaml` | 웹 프로토타입 검증 항목과 184건 평가 기준을 구조화된 하네스 색인에 등록했다. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| harness_explorer | exploration | completed | 기존 API, 패키징, 테스트 연결 지점과 개인 면제·예시·API 문서 회귀를 조사했다. |
| harness_architect | architecture | completed | 고정 범위 UI, 상태·근거 표시, 실패 폐쇄, 문서 호환성과 테스트 완료 기준을 설계했다. |
| harness_worker | implementation | completed | 웹 UI, API 연결, 보안 헤더, 패키징, 평가·테스트·문서와 CI를 구현하고 확인된 회귀를 보완했다. |
| harness_reviewer | independent-review | completed | 권한 과장, 인용, 개인정보, 안전한 렌더링, 보류 경계와 API 호환성을 독립 검토해 차단 결함이 없음을 확인했다. |
| harness_qa | qa | completed | 169개 테스트, 184개 평가, 실 API 경계, 패키징, 하네스·학사·보고 검증과 Mermaid 렌더링을 확인했다. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 전체 단위·통합 테스트 | yes | passed | python -m unittest discover -s tests -p test_*.py -q | 169개 테스트가 통과했다. |
| 고정 학사 답변 평가 | yes | passed | python scripts/validation/validate_academic_answer_engine.py --project . | 184개 질문 평가가 통과했다. |
| 실제 브라우저 동작 | yes | passed | - | localhost 화면에서 승인된 졸업학점 답변과 PDF 근거 위치가 표시됐고, 개인 연계과정 논문 면제 질문은 규칙·인용 없이 근거 부족으로 표시됐다. |
| 보류 주제와 개인 판정 경계 | yes | passed | - | PCCP·캡스톤·공모전·졸업작품과 개인 면제 판정은 evidence 없는 insufficient_evidence를 유지했다. |
| API 문서와 패키징 | yes | passed | - | Swagger UI를 실제 브라우저에서 확인했고 /docs, /redoc, /openapi.json과 wheel 내 웹 자산 3개가 검증됐다. |
| 하네스 완료 | yes | passed | python .agents/skills/harness/scripts/validate.py --project . --run 20260923-web-prototype-final2 --complete | 최종 상태 확인 실행이 0개 completion error로 완료됐다. |
| 독립 리뷰와 QA | yes | passed | - | 독립 리뷰에서 차단 결함이 없었고 QA에서 테스트·평가·실 API·문서·패키징·Mermaid 검사가 모두 통과했다. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **warning**: 공모전 수상, 캡스톤디자인, 졸업작품과 PCCP 조건은 공식 학과 근거가 없어 답변 근거로 사용하지 않고 insufficient_evidence로 유지한다.
- **info**: 현재 프로토타입은 2026학번 컴퓨터공학과와 등록된 한국어 질문 유형으로 범위를 제한하며 개인 학적 판정을 수행하지 않는다.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
