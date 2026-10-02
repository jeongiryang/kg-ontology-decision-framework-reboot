# 개발 방향·남은 작업 기록 완료 — 제품은 미완료

- 실행 ID: `20261002-b-first-direction-record`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0 / upstream 79b82281d305c89181fbb216499d5f1e962c14ed / prototype 0.11.0 unchanged`
- 브랜치: `codex/semantic-prototype`
- 기준 커밋: `0083e767507a3e724920ed60248665540c46ec54`
- 결과 커밋: `24dbc2bffa61c97c380020a5d06694f9c73ec485`

## 요청

남은 해야 할 사항과 합의한 개발 방향의 기존 기록을 확인하고 빠진 내용을 먼저 기록한다.

## 요약

기존 재개·평가 기록에는 66발화 결과와 필수6결함, 누적5회 이력이 있었다. 새로 합의한 질문·답변(B) → 성적표 비교(A) → 연동 순서와 입력/출력 개선, 단계별 완료 기준을 단일 작업 목록과 ADR0025에 추가했다. README와 보고서 색인·기존 재개 기록을 연결했다. 문서화만 완료했으며 제품 수정·학사 승인·서버 변경·신규 모델 평가를 수행하지 않았다. 작은 문서 작업을 메인이 단독 수행했고 서브에이전트 실행이나 독립 제품 QA를 주장하지 않는다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| added | `docs/operations/development-roadmap.md` | B → A → 연동의 단일 작업 목록, 기존/신규 기록 구분, 입력 의미 해석·출력 사실 검증·6결함·독립 평가와 완료 기준. |
| added | `docs/harness/decisions/0025-separate-dialogue-and-transcript-tracks.md` | 두 개발 트랙 분리와 순서, 기존 승인 재사용과 새 실행 경계를 채택. 구현·검증 완료 또는 신규 학사 승인으로 오인하지 않도록 명시. |
| documentation | `docs/harness/decisions/README.md` | ADR0025를 설계 결정 색인에 연결. |
| documentation | `docs/harness/README.md` | 문서 지도에 개발 방향·남은 작업 링크 추가. |
| documentation | `README.md` | 처음 읽는 사용자가 B 우선 작업 목록을 찾을 수 있도록 연결. |
| documentation | `reports/README.md` | 향후 작업 기준과 과거 검증 기록을 구분하는 진입점 추가. |
| documentation | `reports/presentation-handoff-20261001.md` | 최신 방향 합의를 상단에 추가하고 과거 관찰·실패·재개 순서는 이력으로 보존. |
| documentation | `CHANGELOG.md` | 2026-10-02 문서만 변경한 항목과 제품/사실/서버 불변을 기록. |

## 에이전트 결과

참여 에이전트 없음.

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 기존 기록 및 누락 방향 감사 | yes | passed | - | 기존 보고서 색인·발표 재개 기록·ADR0024·변경이력·실제 평가 결과를 확인했다. 기존 실패를 보존하고 B/A 분리와 입력/출력 방향을 신규 문서에 기록했다. |
| 하네스 정적 구조 | yes | passed | .venv/Scripts/python.exe -X utf8 -B .agents/skills/harness/scripts/validate.py --project . | PASS: 10 agent(s), 9 skill(s), 0 error(s). Static validation only. 실제 서브에이전트 실행 검증은 아님. |
| manifest·설정·스킬·계약·문서 동기화 | yes | passed | .venv/Scripts/python.exe -X utf8 -B scripts/validation/validate_harness_sync.py --project . | harness-sync: manifest, agents, skills, schemas, config, and docs are synchronized. |
| 공개 보고·문서 링크 검사 | yes | passed | .venv/Scripts/python.exe -X utf8 -B scripts/reporting/ci_checks.py --project . --changed-from 0083e767507a3e724920ed60248665540c46ec54 | ci-check: public reports and harness documentation passed. 문서 링크와 공개 보고 정제·동일 입력 재생성 검사이며 제품 회귀 통과가 아님. |
| 문서만 변경한 범위 | yes | passed | git diff --name-only 0083e767507a3e724920ed60248665540c46ec54; git ls-files --others --exclude-standard | 명시한 문서8경로만 변경됐으며 제품·학사 지식·계약·설정은 기준점과 같다. 원시 실행 기록과 입력 보고서는 Git 제외 위치에 둔다. |
| 공백·패치 검사 | yes | passed | git diff --check; git diff --cached --check | 추적 문서와 스테이징된 신규 문서 검사에서 오류 없음. |
| 전체 제품 회귀·실제 모델·브라우저 재검증 | no | not_run | - | 이번 요청은 기록부터 하는 문서 작업이다. 기존 제품 필수6실패와 실제66발화의36통과·29실패·1대기를 유지하며 재검증 성공으로 표시하지 않는다. |
| 독립 제품 리뷰·QA 및 원격 CI | no | not_run | - | 이번에는 에이전트 실행·새 제품 QA·GitHub Actions 결과 판정을 수행하지 않았다. 기존 Draft PR12와 제품 CI 실패를 문서 검사 성공으로 대체하지 않는다. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **warning**: 제품0.11.0의 R5-F1~R5-F6 및 실제 질문 평가 실패는 미해결이다. 이번 보고서의 완료는 문서 기록에만 해당한다.
- **info**: 다음 구현은 B부터 새 범위·실행 ID로 시작하며 이전 누적5회 이력을 보존한다. 기본 수정 상한2회, 예외는 실행 전에 명시한다. 이미 공개된 평가 질문은 새 미사용 질문으로 취급하지 않는다.
- **info**: 현재 B 결함 수정에 추가 학사 설명·재승인은 필요하지 않다. 기존 사용자 확인과 보류는 그대로 유지하며 정말 새로운 모호성만 해당 항목에 질문한다.

## 공개 정보

- 주요 작업: `no`
- PDF 필요: `no`
