# DSW 디스크 비율 차단 제거

- 실행 ID: `20261001-dsw-policy`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0 / upstream 79b82281d305c89181fbb216499d5f1e962c14ed`
- 브랜치: `codex/remove-dsw-disk-threshold`
- 기준 커밋: `5acc838700e17f2b8e59fc0ccd4c6f1e865df330`
- 결과 커밋: `783e44a785585bd39a192bc2be621e604272630b`

## 요청

DSW 기존 운영 지침의 고정 디스크 여유 비율 차단 조건 제거

## 요약

프로젝트 에이전트·스킬·설정·문서에서 고정 비율 차단을 영구 제거했다. 디스크 관찰, 나루 보호와 GPU 안전 경계는 유지한다. 서버 실행은 이번 변경 범위가 아니다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| changed | `.agents/skills/dsw-server-operations/SKILL.md` | 비율 차단 제거; 디스크 관찰·나루 보호·실행 오류 기록 유지. |
| changed | `.codex/agents/dsw-compute-operator.toml` | 동일 정책으로 실행 에이전트 지침 동기화. |
| removed | `config/dsw.example.toml` | 최소 디스크 여유 비율 설정 제거. 비공개 로컬 설정도 해당 키만 제거했고 다른 값은 보존. |
| documentation | `docs/harness/dsw-operations.md` | 현행 운영 기준·영구 변경 범위·유지되는 안전 조건 명시. |
| added | `docs/harness/decisions/0017-remove-fixed-dsw-disk-threshold.md` | 정책 결정, 과거 기록 보존과 후속 작업 입력 갱신 기록. |
| documentation | `docs/harness/decisions/README.md` | ADR0017 색인 추가. |
| documentation | `docs/harness/decisions/0015-private-transcript-partial-assessment.md` | 과거 디스크 차단 문단에 ADR0017 대체 표시; 원래 기록 보존. |
| documentation | `docs/harness/decisions/0016-hash-bound-pdf-evidence-preview.md` | 과거 디스크 예외 승인 조건의 대체 표시; 실제 추론과 목록 확인 구분 유지. |
| documentation | `docs/operations/kg-llm-prototype.md` | 현재 정책과 변경 전 차단 기록을 분리; 실제 추론은 여전히 미실행. |
| documentation | `docs/operations/transcript-prototype.md` | 현재 DSW 조건 동기화; 개발원 직접 평가 생략 유지. |
| added | `tests/test_academic_contracts.py` | 명시적 GPU 선택과 상주 승인 필수 조건 테스트 2건 추가. |
| documentation | `README.md` | 정책 변경과 실제 모델 실행 상태 구분. |
| documentation | `CHANGELOG.md` | 제거·유지·검증·작업 범위 기록; 과거 보고서는 보존. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| main_session | policy-integration | completed | 지침·공개/비공개 설정·현재 문서를 동기화하고 전체 회귀 검사를 수행. |
| harness_architect | policy-review | completed | 기존 읽기 전용 역할을 독립 리뷰/목표 QA로 재사용. 옛 ADR 잔존 문구를 발견하여 대체 표시 후 재검증. 입력 지문 11개 일치. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 전체 회귀 | yes | passed | python -m unittest discover -s tests -p test_*.py | 325건 통과, 12.411초. 서버·GPU 실 실행 아님. |
| 계약 회귀 | yes | passed | python -m unittest discover -s tests -p test_academic_contracts.py -v | 23건 통과. GPU 수·장치 명시·상주 승인 경계 보존. |
| 스킬 검사 | yes | passed | - | UTF-8 모드로 quick_validate 통과. 최초 Windows 기본 인코딩 실패는 모드 지정으로 해결. |
| 하네스 동기화 | yes | passed | - | 9개 에이전트·8개 스킬 정적 검사와 manifest/TOML/스킬/계약/문서 동기화 통과. |
| 현재 정책 확인 | yes | passed | - | 공개·비공개 설정의 비율 키 제거 확인. 과거 차단 문구는 당시 기록으로만 보존. 계약과 실행 코드에는 기존 수치 게이트가 없어 새 게이트를 만들지 않음. |
| 독립 리뷰/QA | yes | passed | - | 계약 23건·정적 검사·동기화·diff 검사 독립 통과. ADR0015/0016의 대체 표시와 추가 README 검토 완료. 입력 지문 11개 유지. |
| 공개 보고·PDF 검사 | yes | passed | - | MD/JSON/PDF 재생성 일치·공개 정제·문서 링크/갱신 검사 통과. PDF 전2페이지 렌더 확인: 한글·표·잘림·겹침·페이지 번호 정상. |
| 실제 GPU 추론 | no | not_run | - | 정책 변경 요청 범위 밖. 모델 다운로드·서버 작업·나루 변경 없음. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **info**: 후속 LLM 작업은 갱신된 지침과 새 입력 패킷으로 빈 GPU를 확인해 실행한다. 디스크 비율 예외 승인을 요구하지 않는다.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
