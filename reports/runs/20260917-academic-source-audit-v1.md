# 2026 교육과정 1차 출처 감사 보고서

- 실행 ID: `20260917-academic-source-audit-v1`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0+codex-harness.79b82281`
- 브랜치: `main`
- 기준 커밋: `7632ca1f778b3a87b852027555d0d8f79b651d5a`
- 결과 커밋: `7632ca1f778b3a87b852027555d0d8f79b651d5a`

## 요청

추천 진행 순서의 첫 단계로 2026 교육과정 PDF를 2026학년도 컴퓨터공학과 학사조교의 근거 자료로 감사한다.

## 요약

원본 PDF의 신원과 해시, 적용 원칙, 컴퓨터공학과 교육과정·학점표·졸업요건 위치를 확인하고 핵심 표를 이미지로 교차 검증했다. 명시적 시행일이 없어 SourceEntry 승격은 보류하고 needs_review 후보로 남겼다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| documentation | `reports/source-audits/2026-curriculum.md` | 자료 신원, 해시, 컴퓨터공학과 근거 페이지, 교차 확인 값, 등록 보류 사유와 다음 게이트를 기록했다. |
| documentation | `CHANGELOG.md` | 2026 교육과정 1차 출처 감사 결과를 변경 이력에 추가했다. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| main_orchestrator | source-audit-integration | completed | 감사 범위를 제한하고 원문 해시·페이지·표 렌더링을 교차 확인했으며 공개 보고를 통합했다. |
| academic_source_auditor | curriculum-source-audit | completed | 원문을 읽기 전용으로 감사하고 SourceEntry 승격 차단 사유와 규칙 모델링 전 주의사항을 반환했다. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 원본 신원·해시 확인 | yes | passed | Get-FileHash -Algorithm SHA256 <local-source> | 표지 제목·발행 주체와 SHA-256 0d800318dc367a89518f9be6be8f559092474bf00b3573e14bd37b6c44b54471을 확인했다. |
| 핵심 표 시각 검증 | yes | passed | pdftoppm relevant pages to PNG and inspect rendered tables | PDF 23, 261, 567, 572, 577쪽을 렌더링해 컴퓨터공학과 행과 열 관계를 확인했다. |
| 에이전트 실행 장부 | yes | passed | python .agents/skills/harness/scripts/validate.py --project . --run 20260917-academic-source-audit-v1 --complete | 1개 필수 작업이 완료되고 native agent lifecycle이 닫혔으며 completion error는 0개였다. |
| SourceEntry 시행일 검증 | no | not_run | - | 원문과 공식 검색에서 명시적 시행 근거를 확보하지 못해 임의 날짜를 만들지 않고 등록을 보류했다. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **warning**: 명시적 시행일을 확인할 공식 게시 공문 또는 교육과정 확정·시행 근거가 필요하다.
- **warning**: 컴퓨터공학과 졸업논문 대체요건의 해당사항 없음 표기는 면제를 뜻한다고 단정할 수 없어 학과 확인이 필요하다.

## 공개 정보

- 주요 작업: `no`
- PDF 필요: `no`
