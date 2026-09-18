# 2026학번 학사 규칙 승인 및 미확인 운영요건 조사 등록 보고서

- 실행 ID: `20260918-academic-review-approval-v5`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0+codex-harness.79b82281`
- 브랜치: `main`
- 기준 커밋: `47636a190515f8fef8764dbf8e76f281fcb5d8a0`
- 결과 커밋: `60367ef4dca6cf855892d6e273c99cb6a16b1d2c`

## 요청

현재 대화에서 확인한 교육과정 2026 출처 1건과 RuleFact 13건을 사람 검수 완료로 반영하고, 검수 세션의 권한·질문 6건·사용자 원문 응답·적용·종료를 감사 기록으로 남긴다. 공모전·캡스톤·졸업작품·PCCP 후보는 답변 근거에서 제외한 조사 항목과 GitHub Issue로 관리한다.

## 요약

교육과정 2026 출처 1건과 2026학번 컴퓨터공학과 학점·졸업논문 RuleFact 13건을 project_owner 검수 승인으로 전환했다. 질문 6건의 원문 응답과 적용 전후 스냅샷, 대상별 증명을 기록하고 세션을 closed/expired로 종료했다. 졸업논문 대체요건은 현재 PDF에 기재가 없다는 none_listed 의미로만 승인했다. 공모전·캡스톤·졸업작품·PCCP 후보 3건은 AcademicResearchItem으로 격리해 공식 근거가 생길 때까지 insufficient_evidence만 반환하도록 검증한다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| changed | `knowledge/sources/cwnu.curriculum.2026.changwon-undergraduate.json` | 교육과정 2026 출처의 2026학번 컴퓨터공학과 적용 범위를 project_owner 승인 상태로 전환하고 질문·응답 근거를 연결했다. |
| changed | `knowledge/rules/` | 교양 4건, 전공 5건, 졸업학점 2건, 졸업논문 2건 등 RuleFact 13건을 승인하고 none_listed 의미를 현재 PDF 기재 여부로 제한했다. |
| changed | `reviews/academic/2026-curriculum-initial-rules.json` | 출처 1건과 규칙 13건의 검수대장 상태·검수자·시각·응답 참조를 승인 결과와 동기화했다. |
| changed | `reviews/academic/clarifications/2026-initial-review-questions.json` | 세션 권한, 질문 6건의 원문 응답, 적용 전후 스냅샷, 대상별 증명과 종료 사건을 기록하고 모든 배치를 applied로 닫았다. |
| added | `contracts/academic-research-item.schema.json` | 미확인 학사 운영요건을 답변 근거와 분리하는 AcademicResearchItem 계약을 추가했다. |
| added | `reviews/academic/research/cwnu.cs.2026.graduation-practices.json` | 공모전 수상, 캡스톤 선후관계, PCCP 400점 후보를 unverified_user_recollection과 awaiting_official_source로 등록했다. |
| security | `scripts/validation/validate_academic_clarifications.py` | 세션 권한과 응답뿐 아니라 승인 전 의미 스냅샷, 응답 스냅샷, 14개 대상 증명의 변조를 실패 폐쇄로 검사한다. |
| security | `scripts/validation/validate_academic_research.py` | 미검증 후보가 SourceEntry, RuleFact, 학생 답변용 EvidencePacket에 유입되는 것을 차단한다. |
| changed | `contracts/evidence-packet.schema.json` | insufficient_evidence 답변은 적용 규칙과 인용 근거를 비우고 최소 한 개의 미해결 사유를 요구한다. |
| changed | `harness-manifest.yaml` | AcademicResearchItem 계약, 조사 검증기와 문서 참조를 하네스 단일 색인에 동기화했다. |
| documentation | `docs/harness/decisions/0010-isolate-unverified-academic-practices.md` | 미검증 운영요건 격리 결정과 내부 해시의 위협 모델 한계를 ADR로 기록했다. |
| changed | `.github/workflows/harness-ci.yml` | 학사 조사 항목의 스키마·의미·답변 격리 검증을 GitHub Actions 필수 검사에 추가했다. |
| changed | `CHANGELOG.md` | 학사 규칙 승인, 검수 감사 기록, 미확인 운영요건 격리와 보안 검증 변경을 기록했다. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| academic_rule_modeler | apply-decisions | completed | 출처 1건과 RuleFact 13건을 승인하고 질문 6건의 감사 기록을 적용했으며 운영요건 후보 3건을 조사 항목으로 격리했다. |
| harness_reviewer | independent-review | completed | 승인 메타데이터, 사용자 원문, 학점 값과 미검증 주장 변조 공격이 모두 차단되는지 독립 검토했다. |
| harness_qa | qa | completed | 전체 111개 테스트와 하네스·학사·보고·Mermaid 검증을 고정된 입력과 의존성에서 통과시켰다. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 전체 단위 테스트 | yes | passed | .venv/Scripts/python.exe -m unittest discover -s tests -p test_*.py -v | 111개 테스트가 통과했다. |
| 하네스 실행 완료 | yes | passed | .venv/Scripts/python.exe .agents/skills/harness/scripts/validate.py --project . --run 20260918-academic-review-approval-v5 --complete | 적용, 독립 리뷰와 QA 작업이 0개 completion error로 완료됐다. |
| 학사 지식 및 승인 정합성 | yes | passed | .venv/Scripts/python.exe scripts/validation/validate_academic_knowledge.py --project . | SourceEntry 1건, RuleFact 13건과 검수대장 14개 대상의 승인 상태가 일치했다. |
| 검수 세션 감사 정합성 | yes | passed | .venv/Scripts/python.exe scripts/validation/validate_academic_clarifications.py --project . | 세션 권한, 질문·응답 6건, 원문, 적용 스냅샷, 대상별 증명과 종료 사건이 통과했다. |
| 미검증 운영요건 격리 | yes | passed | .venv/Scripts/python.exe scripts/validation/validate_academic_research.py --project . | 후보 3건이 답변 근거에서 제외되고 insufficient_evidence 상태로 유지됐다. |
| 하네스 구조 및 동기화 | yes | passed | .venv/Scripts/python.exe scripts/validation/validate_harness_sync.py --project . | manifest, 계약, 검증기, 문서와 CI 참조가 동기화됐다. |
| 독립 적대적 리뷰 | yes | passed | - | 승인 메타데이터, exact_text, 학점 9→99, 미검증 주장 패러프레이즈 변조가 모두 거절됐고 새 차단 결함은 없었다. |
| Mermaid 실제 렌더 | yes | passed | .venv/Scripts/python.exe scripts/validation/render_mermaid.py --project . --output-dir tmp/mermaid-final-v5 --mmdc <resolved-mmdc> | 하네스 문서의 Mermaid 블록 2개가 실제 렌더링됐다. |
| PDF 전 페이지 시각 검수 | yes | passed | pdftoppm -png -r 150 reports/pdf/20260918-academic-review-approval-v5.pdf tmp/pdfs/20260918-academic-review-approval-v5/page | A4 3개 전 페이지를 렌더링해 한글, 표, 여백, 잘림, 겹침, 내용 없는 고아 페이지와 페이지 번호를 확인했다. |

## 학사 근거 변경

- cwnu.curriculum.2026.changwon-undergraduate

## 이슈 및 남은 작업

- **warning**: 공모전 수상, 캡스톤디자인 선후관계와 PCCP 400점 후보는 공식 학과 근거가 없어 학사 답변에 사용할 수 없다. GitHub Issue #1에서 근거 확보를 추적한다.
- **info**: 승인 감사 해시는 저장소 내부 드리프트와 우발적 변조를 탐지하지만, 데이터·해시·검증기를 동시에 수정할 수 있는 악의적 작성자에 대한 암호학적 부인방지를 제공하지 않는다.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
