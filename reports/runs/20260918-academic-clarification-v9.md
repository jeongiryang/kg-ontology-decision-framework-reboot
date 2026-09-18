# 사용자 질문형 학사 검수 에이전트 구축 보고서

- 실행 ID: `20260918-academic-clarification-v9`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0+codex-harness.79b82281`
- 브랜치: `main`
- 기준 커밋: `41b63513e104448c870e03c973fda2e581a0ef4b`
- 결과 커밋: `b34f2f808055d94184b6027071008d22e5b8037f`

## 요청

JSON이나 스키마를 직접 검토하지 않아도 근거와 선택지가 있는 질문으로 학사 규칙을 승인·수정·보류할 수 있는 읽기 전용 검수 촉진자, 세션 권한 계약, 최초 14개 검수 질문과 검증·문서·보고 체계를 구현한다.

## 요약

읽기 전용 academic_review_facilitator와 academic-clarification 스킬, AcademicClarificationPacket 계약 및 2026학번 컴퓨터공학과 14개 검수 대상을 3개 묶음·6개 질문으로 만든 초기 패킷을 추가했다. 세션 권한 전 답변, 미응답, 모호한 답변, 종료 사건 중복, 조작된 근거, 충돌 승인 우회를 모두 실패 폐쇄한다. 출처별 원문 발췌 충돌은 표현할 수 있지만 상위 근거 충돌은 사용자 답변으로 해소하지 않는다. 현재 14개 대상은 승인되지 않았으며 검수 세션 시작을 기다린다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| added | `.codex/agents/academic-review-facilitator.toml` | 근거·선택지·영향이 있는 질문 패킷만 제안하는 읽기 전용 학사 검수 촉진자를 등록했다. |
| added | `.agents/skills/academic-clarification/SKILL.md` | 질문 생성, 세션 권한, 응답 정규화, 부분 보류와 모델러 전달 절차를 정의했다. |
| added | `contracts/academic-clarification-packet.schema.json` | 질문·선택지·출처별 근거·세션 권한·응답·충돌·감사 사건의 공개 계약을 추가했다. |
| added | `reviews/academic/clarifications/2026-initial-review-questions.json` | 출처 1건과 규칙 13건을 출처·학점·졸업논문 3개 묶음의 6개 질문으로 구성했다. |
| documentation | `reviews/academic/clarifications/2026-initial-review-questions.md` | 사용자가 스키마 대신 읽을 수 있는 검수 시작 안내와 질문 묶음 요약을 추가했다. |
| security | `scripts/validation/validate_academic_clarifications.py` | 14개 정확한 범위, 객체 해시, 원문 근거, 권한 전이, 응답·감사 연결, 충돌 보존과 승인 우회를 실패 폐쇄로 검증한다. |
| added | `tests/test_academic_clarifications.py` | 권한·근거·응답·상태·충돌에 대한 정상 및 적대 회귀검사 34개를 추가했다. |
| changed | `harness-manifest.yaml` | 새 에이전트, 스킬, 계약, 검증 명령과 입출력 계약을 구조화된 색인에 등록했다. |
| changed | `.github/workflows/harness-ci.yml` | 학사 검수 질문 의미 검증을 GitHub CI 필수 단계에 추가했다. |
| documentation | `docs/harness/` | 아키텍처, 권한, 워크플로, 계약과 ADR 0009에 질문형 검수 흐름 및 안전 경계를 문서화했다. |
| changed | `CHANGELOG.md` | 검수 촉진자, 세션 계약, 충돌 표현과 보안 차단 변경을 공개 변경 이력에 기록했다. |
| fixed | `scripts/reporting/reporting.py` | PDF 공개정보 제목과 두 값을 같은 페이지에 묶어 고아 마지막 페이지를 방지했다. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| academic_review_facilitator | facilitator-smoke | completed | 14개 대상, 3개 묶음, 6개 질문과 원문 발췌 결합 및 실패 폐쇄 경계를 확인했다. |
| harness_reviewer | independent-review | completed | 세 차례 적대 리뷰에서 권한 종료 중복, 충돌 표현 불가, 충돌 승인 우회를 찾아 수정 후 새 차단 결함 없음을 확인했다. |
| harness_qa | clarification-qa | completed | 고정된 v9 입력에서 전체 100개 테스트와 구조·동기화·문서·Mermaid 검증을 통과시켰다. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 전체 단위 테스트 | yes | passed | .venv/Scripts/python.exe -m unittest discover -s tests -p test_*.py -v | 100개 테스트가 통과했다. |
| 하네스 실행 완료 | yes | passed | .venv/Scripts/python.exe .agents/skills/harness/scripts/validate.py --project . --run 20260918-academic-clarification-v9 --complete | 필수 3개 작업과 실제 agent lifecycle이 0개 completion error로 완료됐다. |
| 학사 검수 질문 의미 검증 | yes | passed | .venv/Scripts/python.exe scripts/validation/validate_academic_clarifications.py --project . | 14개 범위, 세션 권한, 출처 발췌, 충돌과 응답 정합성이 통과했다. |
| 하네스 구조 및 동기화 | yes | passed | .venv/Scripts/python.exe scripts/validation/validate_harness_sync.py --project . | 9개 에이전트, 8개 스킬, manifest, schema, config와 문서가 동기화되었다. |
| 독립 적대적 리뷰 | yes | passed | - | 권한 종료 중복, 충돌 표현, 표기 동등성, 충돌 승인 우회를 재검증하고 새 차단 결함이 없음을 확인했다. |
| Mermaid 실제 렌더 | yes | passed | .venv/Scripts/python.exe scripts/validation/render_mermaid.py --project . --mmdc .local/mermaid/node_modules/.bin/mmdc.cmd | 문서의 Mermaid 블록 2개를 SVG 2개, 총 84,458 bytes로 렌더링했다. |
| PDF 시각 검수 | yes | passed | pdftoppm -png -r 150 reports/pdf/20260918-academic-clarification-v9.pdf tmp/pdfs/20260918-academic-clarification-v9/page | 2개 전 페이지를 렌더링해 한글, 표, 여백, 잘림, 겹침, 고아 페이지와 페이지 번호를 확인했다. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **warning**: SourceEntry 1건과 RuleFact 13건은 모두 사용자 검수 전까지 미승인 상태이며 학생 대상 supported 답변에 사용할 수 없다.
- **info**: 챗봇, PDF 파서, 검색, Neo4j와 모델 서빙은 이번 단계 범위에 포함하지 않았다.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
