# 입학학번 기반 학사 계약 v2 및 2026 초기 규칙 구축 보고서

- 실행 ID: `20260918-academic-contract-v2`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0+codex-harness.79b82281`
- 브랜치: `main`
- 기준 커밋: `6bc06305f08cbd85c5f39a03e39290dbb0b643e7`
- 결과 커밋: `96a024e5da4636c23544fb9a1091848c1f698956`

## 요청

2026 교육과정은 2026학번에게 적용하고, 졸업논문 의무와 현재 규정집에 기재되지 않은 대체요건을 구분하며, 관계 정의 단계부터 사람 검수를 받는 학사조교 하네스를 구현한다.

## 요약

입학연도 적용 계약 v2, 사람 전수 검수 큐, 2026학번 컴퓨터공학과 SourceEntry 1건과 RuleFact 13건을 구축했다. 승인 규칙의 해시·범위·출처·인용과 검수 큐를 의미 검증하여 미검수 규칙으로 supported 답변을 만들 수 없게 했다. 모든 초기 지식은 사용자 전수검수 전까지 보류 상태다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| changed | `contracts/source-entry.schema.json` | 교육과정과 입학연도 적용 범위 및 사람 검수 상태를 표현하는 v2 계약으로 변경했다. |
| changed | `contracts/rule-fact.schema.json` | 규칙 관계, 타입화된 판정, 졸업논문 의무와 대체정책, 사람 검수를 표현하는 v2 계약으로 변경했다. |
| security | `contracts/evidence-packet.schema.json` | 비식별 학생 사실과 불변 규칙 해시를 사용하는 v2 답변 근거 계약으로 변경했다. |
| added | `contracts/academic-review-packet.schema.json` | 출처와 규칙 관계를 사람이 전수 검수하는 큐 계약을 추가했다. |
| added | `knowledge/sources/cwnu.curriculum.2026.changwon-undergraduate.json` | 2026 교육과정의 신원, 해시, 2026학번 적용 범위와 검수 상태를 등록했다. |
| added | `knowledge/rules/` | 컴퓨터공학과 학점 규칙 11건, 졸업논문 의무와 대체정책 규칙 2건을 추가했다. |
| added | `reviews/academic/2026-curriculum-initial-rules.json` | 출처 1건과 규칙 13건의 사람 전수검수 대장을 추가했다. |
| security | `scripts/validation/validate_academic_knowledge.py` | 승인 상태, canonical 해시, 적용 범위, 규칙별 인용과 claim, 검수대장 정합성을 fail-closed로 검사한다. |
| fixed | `.agents/skills/harness/scripts/run_state.py` | Windows 실행 상태의 의존성 경로를 POSIX 형식으로 저장해 후속 작업 시작 실패를 고쳤다. |
| documentation | `docs/harness/decisions/` | 입학연도·사람 전수검수와 운영체제 공통 실행 경로 결정을 ADR 0007·0008로 기록했다. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| academic_rule_modeler | initial-rule-modeling | completed | SourceEntry, RuleFact 13건과 사람 검수 큐를 생성하고 최신 보류 상태를 재검증했다. |
| harness_reviewer | independent-review | completed | 두 차례 적대적 리뷰로 승인 우회, 인용 누락, claim 변조와 검수 생명주기 결함을 찾아 수정 후 통과시켰다. |
| harness_qa | contract-qa | completed | 전체 66개 테스트와 하네스·지식·보고 검증을 독립 실행해 통과를 확인했다. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 전체 단위 테스트 | yes | passed | .venv/Scripts/python.exe -m unittest discover -s tests -p test_*.py -v | 66개 테스트가 통과했다. |
| 하네스 실행 완료 | yes | passed | .venv/Scripts/python.exe .agents/skills/harness/scripts/validate.py --project . --run 20260918-academic-contract-v2c --complete | 필수 3개 작업과 agent lifecycle이 0개 completion error로 완료됐다. |
| 하네스 구조 및 동기화 | yes | passed | .venv/Scripts/python.exe scripts/validation/validate_harness_sync.py --project . | manifest, agents, skills, schemas, config, docs가 동기화되었다. |
| 학사 지식 의미 검증 | yes | passed | .venv/Scripts/python.exe scripts/validation/validate_academic_knowledge.py --project . | 출처, 규칙, 관계, 검수 큐와 논문 규칙 분리가 통과했다. |
| 독립 적대적 리뷰 | yes | passed | - | 미승인 registry, 해시·범위 불일치, 규칙별 인용 누락, claim 변조와 검수대장 불일치를 모두 차단했다. |
| 공개 보고 CI | yes | passed | .venv/Scripts/python.exe scripts/reporting/ci_checks.py --project . | 기존 공개 보고서와 하네스 문서가 통과했다. |
| PDF 시각 검수 | yes | passed | pdftoppm -png -r 150 reports/pdf/20260918-academic-contract-v2.pdf tmp/pdfs/20260918-academic-contract-v2/page | 2개 전 페이지를 렌더링해 한글, 표, 여백, 잘림, 겹침과 페이지 번호를 확인했다. |

## 학사 근거 변경

- 2026 교육과정 SourceEntry를 2026학번 컴퓨터공학과 범위의 needs_review 상태로 등록했다.

## 이슈 및 남은 작업

- **warning**: SourceEntry 1건과 RuleFact 13건은 모두 사람 전수검수 대기 상태이므로 학생 대상 supported 답변에 사용할 수 없다.
- **info**: 챗봇, PDF 파서, 검색, Neo4j와 모델 서빙은 이번 단계 범위에 포함하지 않았다.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
