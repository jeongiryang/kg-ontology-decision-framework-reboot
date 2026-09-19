# 2026학번 조교 확인 학사 규칙 반영 완료 보고서

- 실행 ID: `20260919-ta-confirmation-v29`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0+codex-harness.79b82281`
- 브랜치: `main`
- 기준 커밋: `6ee0e5e2a0862574267d06db0eec0f354aacb6c8`
- 결과 커밋: `2129b9389fc690802b566684a5998374c70c5551`

## 요청

조교 확인 답변을 근거로 전과 적용연도, 졸업논문 이수와 학석사연계과정 예외, 재수강·동일교과목·이수 후 동일 또는 대체 지정 학점 계산 등 2026학번 컴퓨터공학과 규칙을 반영한다. 소급 개인 판정과 공모전·캡스톤·졸업작품·PCCP 요건은 공식 근거 확보 전까지 보류한다.

## 요약

조교 확인 자료와 사용자의 정확한 정정을 검수 세션 감사 기록으로 보존하고, 승인된 학사 규칙 13건을 추가해 답변 엔진의 승인 규칙을 26건으로 확장했다. 재수강 기이수 과목과 동일교과목은 중복 계산하지 않고, 이수 후 동일·대체 지정은 일반적으로 별개 과목으로 계산하되 소급 개인 판정은 보류한다. 개인 면제·개별 과목 동일성·소급 적용 질문이 일반 정책 문구로 우회되지 않도록 실패 폐쇄 경계를 보강했다. 독립 리뷰와 QA 후 157개 테스트와 182개 고정 평가가 통과했다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| added | `knowledge/sources/cwnu.curriculum.2026.ta-validation-response.json` | 원본 PDF를 공개하지 않고 조교 확인 자료의 해시, 범위와 validation_aid 권위를 등록했다. |
| added | `reviews/academic/clarifications/2026-ta-confirmation.json` | 검수 권한, 질문, 사용자 원문 답변, 적용 결과와 세션 종료 사건을 감사 가능한 구조로 기록했다. |
| added | `knowledge/rules/cwnu.cs.2026.course-counting.retake.json` | 재수강 시 기이수 과목을 삭제해 학점을 중복 계산하지 않는 규칙을 추가했다. |
| added | `knowledge/rules/cwnu.cs.2026.course-counting.identical-course.json` | 동일교과목은 중복 수강신청되지 않아 중복 계산하지 않는 규칙을 추가했다. |
| added | `knowledge/rules/cwnu.cs.2026.course-counting.post-completion-equivalence.json` | 이수 후 동일·대체 지정은 별개 과목으로 계산하되 소급 개인 판정은 보류하는 규칙을 추가했다. |
| added | `knowledge/rules/` | 전과 최초 입학연도, 교양 인정·배분, 균형교양 영역, 권장 교양, 0학점 논문 Fail, 학석사연계과정 예외, 심층상담과 전공필수 과목 규칙을 추가했다. |
| changed | `knowledge/rules/cwnu.cs.2026.graduation.thesis-required.json` | 졸업논문은 0학점이어도 필수이며 미이수 시 Fail이라는 조교 확인을 승인 근거로 연결했다. |
| security | `src/academic_assistant/core.py` | 개인 면제·개별 과목 동일성·소급 적용 판정을 문장 전체에서 감지하고, 순수 정책 설명만 허용하도록 정책 문형과 혼합 절 경계를 실패 폐쇄로 강화했다. |
| changed | `config/academic-registry-pins.json` | 승인 규칙 26건, 출처 2건과 의도 카탈로그의 정규 해시를 고정했다. |
| changed | `evaluations/academic-answer-mvp.json` | 조교 확인 규칙, 자연어 변형, 개인 판정 우회와 혼합 절 경계를 포함하는 고정 평가를 182건으로 확장했다. |
| documentation | `reviews/academic/2026-curriculum-ta-confirmation.md` | 사용자가 읽을 수 있는 조교 확인 반영 내역과 보류 경계를 문서화했다. |
| documentation | `README.md` | 지원 규칙 수, 평가 수, 조교 확인 규칙과 개인 판정 제한을 최신 상태로 갱신했다. |
| changed | `.github/workflows/harness-ci.yml` | 182건 평가셋과 학사 지식·검수·연구 격리 검증을 CI 완료 조건으로 유지했다. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| academic_source_auditor | source-audit | completed | 조교 답변 PDF의 신원, 해시, 페이지와 validation_aid 권위를 확인하고 원본 공개 제외를 검증했다. |
| academic_rule_modeler | rule-modeling | completed | 확인된 조교 답변과 사용자 정정을 SourceEntry, RuleFact, 검수대장과 질문 응답 기록으로 구조화했다. |
| harness_worker | implementation | completed | 승인 규칙을 답변 엔진, 의도 카탈로그, 핀, 계약, 문서, 테스트와 CI에 통합하고 리뷰 결함을 보완했다. |
| harness_reviewer | independent-review | completed | 개인 판정 우회, 정책 문형, 혼합 절, 보호 주제, 근거와 인터페이스를 독립 검토해 최종 차단 결함이 없음을 확인했다. |
| harness_qa | qa | completed | 157개 테스트, 182개 평가, 근거·거절 경계, 학사·하네스·보고 검증과 Mermaid 렌더링을 확인했다. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 전체 단위·통합 테스트 | yes | passed | python -m unittest discover -s tests -p test_*.py -q | 157개 테스트가 통과했다. |
| 고정 학사 답변 평가 | yes | passed | python scripts/validation/validate_academic_answer_engine.py --project . | 182개 질문 평가가 통과했다. |
| 근거와 거절 경계 | yes | passed | - | supported 85건은 승인·고정된 근거를 사용했고 insufficient_evidence 92건은 적용 규칙과 인용이 비어 있었다. |
| 독립 리뷰 | yes | passed | - | 순수 개인 판정, 순수 정책 설명, 명시·암시 주어 혼합 절과 문장부호 분리형을 포함한 확정 의미 클래스에서 결함이 없었다. |
| 학사·하네스·보고 검증 | yes | passed | - | 학사 지식, 검수 권한, 미검증 연구 격리, 하네스 정적·동기화와 공개 보고 검사가 통과했다. |
| Mermaid 실제 렌더 | yes | passed | - | 독립 QA에서 하네스 문서 Mermaid 2개를 SVG로 렌더링했다. |
| PDF 전 페이지 시각 검수 | yes | passed | pdftoppm -png -r 150 reports/pdf/20260919-ta-confirmation-v29.pdf tmp/pdfs/20260919-ta-confirmation-v29-v1/page | A4 3개 전 페이지를 렌더링해 한글 가독성, 표 정렬, 여백, 잘림, 겹침, 고아 페이지와 페이지 번호를 확인했다. |

## 학사 근거 변경

- cwnu.curriculum.2026.ta-validation-response

## 이슈 및 남은 작업

- **warning**: 공모전 수상, 캡스톤디자인 1·2, 졸업작품과 PCCP 400점 조건은 공식 근거가 없어 답변 근거로 사용하지 않고 insufficient_evidence로 유지한다.
- **warning**: 소급 적용 여부, 개별 과목의 동일·대체 지정과 개인 학석사연계과정 면제 자격은 학생별 공식 기록 확인이 필요해 자동 판정하지 않는다.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
