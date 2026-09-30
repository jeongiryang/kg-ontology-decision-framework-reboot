# 원본 PDF 근거 표시와 로컬 연결 점검 — 부분 완료

- 실행 ID: `20261001-integration-v4`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0 / upstream 79b82281d305c89181fbb216499d5f1e962c14ed`
- 브랜치: `codex/pdf-evidence-live-integration`
- 기준 커밋: `2489928de78ee8de5c1ea5f0ec2f7dd4adb9b72b`
- 결과 커밋: `516a8d299c4014c31b4dae8a6ef1417c2e14d8f3`

## 요청

남은 초기 프로토타입 작업을 진행하고 답변의 원본 PDF 근거 위치를 빨간 줄로 표시한다.

## 요약

원본 해시가 검증된 PDF 근거 표시, 표시본 다운로드, 답변·성적표 화면 연결과 localhost 실행 상태 점검을 구현했다. 기존 학사 Neo4j에서 현재 근거를 실제 검증했고 연구실 SSH 모델 목록 조회도 성공했다. 실제 GPU 추론은 디스크 게이트 예외 확인 전까지 미실행이다. 전체 작업이 끝났다고 주장하지 않는다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| added | `src/academic_assistant/evidence_pdf.py` | 승인·원본 해시·인용 페이지를 검증하는 PNG/PDF 근거 표시 API. 유일한 위치만 빨간 밑줄로 표시. |
| added | `src/academic_assistant/web/evidence.js` | 인용 대화상자, 표시본 내려받기, 안전한 URL 검증과 오래된 응답 차단. |
| added | `src/academic_assistant/web/evidence.css` | 접근성 있는 근거 대화상자와 반응형 이미지 배치. |
| changed | `src/academic_assistant/web/app.js` | 학사 답변의 인용 버튼 및 실제 근거·모델 준비 상태 표시. |
| changed | `src/academic_assistant/web/transcript.js` | 성적표 항목별 규칙·정책 참고 근거에 PDF 버튼 연결. 개인 판정 범위는 변경하지 않음. |
| changed | `src/academic_assistant/web/index.html` | 외부 근거 자산과 실행 상태 영역 등록. 엄격한 보안 정책 유지. |
| changed | `src/academic_assistant/api.py` | 근거 라우터·정적 자산·비공개 주소가 노출되지 않는 준비 상태 응답 등록. |
| changed | `src/academic_assistant/llm.py` | 모델을 적재하거나 추론하지 않는 제한된 읽기 전용 모델 목록 점검. |
| added | `scripts/operations/start_prototype.py` | Git 제외 비공개 설정을 검사하고 localhost8000·단일 프로세스·접근 로그 없이 실행. |
| added | `scripts/operations/ssh_loopback_bridge.py` | Windows와 WSL의 기존 자기 계정 SSH를 통한 loopback 전용 제한 중계. 새 권한이나 공개 포트 없음. |
| added | `contracts/evidence-preview.schema.json` | EvidencePreview1.0.0 계약: 원본 검증값·PDF/인쇄 페이지·정확도·표시 URL. |
| changed | `harness-manifest.yaml` | 새 근거 계약과 원본 불변·인용 페이지 제한·위치 미확인 검증 기준 동기화. |
| changed | `pyproject.toml` | 프로토타입0.6.0 및 PNG/PDF 표시용 Pillow 고정 의존성. |
| added | `tests/test_evidence_pdf.py` | 24개 PDF 경계 테스트: 정확한 표시, page_only, 해시 변조, 제한 페이지, 동시성, 원본 보존. |
| added | `tests/test_evidence_ui.js` | 9그룹 UI 회귀: 악성 URL, 메타데이터 불일치, 오래된 응답·오류, 닫기와 초점 복원. |
| added | `tests/test_private_startup.py` | 비공개 설정의 허용목록·Git 제외·중복 키 검증. |
| added | `tests/test_runtime_inventory.py` | 준비 검사의 읽기 전용 경계와 서비스 주소 비노출 검증. |
| changed | `.github/workflows/harness-ci.yml` | Node 근거 UI 테스트와 배포 wheel의 근거 자산 포함 검사 추가. |
| documentation | `docs/operations/pdf-evidence-viewer.md` | 빨간 밑줄과 위치 미확인의 차이, 비공개 원본 등록, API·실행 방법. |
| documentation | `docs/operations/kg-llm-prototype.md` | 실제 Neo4j·SSH 목록 조회와 아직 미실행인 GPU 추론을 구분. 운영 인증 DB로 과장하지 않음. |
| documentation | `docs/harness/decisions/0016-hash-bound-pdf-evidence-preview.md` | 원본을 변경하지 않는 해시 연결 표시본과 불확실한 위치의 밑줄 금지 결정. |
| documentation | `docs/harness/contracts.md` | EvidencePreview 실행 계약과 공개 문서 연결. |
| documentation | `docs/harness/workflows.md` | 근거 미리보기의 검증·실패 폐쇄 절차. |
| documentation | `docs/harness/README.md` | 근거 표시 문서 색인 추가. |
| documentation | `docs/harness/decisions/README.md` | ADR0016 색인 추가. |
| documentation | `README.md` | 답변·성적표에서 원본 근거를 보는 사용자 안내. |
| documentation | `config/pilot.env.example` | 비공개 원본 매핑 환경변수의 사용 안내만 추가. |
| documentation | `CHANGELOG.md` | 구현 내용과 GPU 추론 미실행을 구분한 변경 이력. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| harness_worker | pdf-backend | completed | 승인된 규칙 기반의 원본 검증·표시본 API와 24개 경계 테스트. 실제 네이티브 구현·완료 결과 등록. |
| harness_worker | pdf-ui | completed | 동일 워커를 순차 재사용해 근거 대화상자와 9그룹 UI 테스트 구현. 쓰기 파일 소유권 분리. |
| harness_architect | review | completed | 기존 읽기 전용 architect를 독립 리뷰 대체 역할로 재사용. 입력 지문18개 일치, 미해결 중대 결함 없음. |
| harness_explorer | qa | completed | 기존 읽기 전용 explorer를 합성 실행 QA 대체 역할로 재사용. 323개 테스트·기존 학사 검증 통과, 제품 수정 없음. |
| main_session | lab-inference | blocked | 연구실 SSH·모델 목록 조회는 성공. 디스크 게이트 예외 확인 전까지 실제 GPU 추론 에이전트를 배정하거나 모델을 적재하지 않음. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 전체 단위 테스트 | yes | passed | python -B -m unittest discover -s tests -p test_*.py | 독립 QA323개 통과. 이 중 PDF 경계24개를 별도 재실행하여 통과. |
| 화면 순서·안전성 | yes | passed | node tests/test_evidence_ui.js; node tests/transcript_web_ordering.cjs | 근거 UI9개 회귀 그룹 및 기존 성적표 최신 응답 검사 통과. |
| 학사 평가·권위·격리 | yes | passed | - | 답변265건, 합성 사용성30건, 새 합성 참고15건, 성적표 계약5개와 canonical95건, 학사 검수·미검증 주장 격리 통과. |
| 하네스·문서 동기화 | yes | passed | python scripts/validation/validate_harness_sync.py --project .; python scripts/reporting/ci_checks.py --project . --changed-from 2489928de78ee8de5c1ea5f0ec2f7dd4adb9b72b | 9에이전트·8스킬 정적 검사, 새 계약과 실제 설정·문서·변경 문서 검사 통과. |
| 독립 리뷰 | yes | passed | - | 최종 공개 파일28개, PDF·인용 순서·안전한 화면 URL·비공개 실행·SSH 중계 경계 검토. 실제 합성 PDF와 준비 상태·UI 검사 통과. 미해결 중대 결함 없음. |
| 실제 로컬 Neo4j 근거 | yes | passed | - | 기존 학사 개발 DB68노드115관계, 현재 승인 Registry의 그래프 해시와 대조 성공. 웹 readyz 및 graph_verified 확인. 운영 인증 DB의 연결 성공 주장이 아님. |
| 실제 원본·브라우저·표시본 PDF | yes | passed | - | 승인 원본 해시 일치. 졸업학점 PDF577/인쇄569페이지의 컴퓨터공·2026·졸업학점130 위치 시각 확인, 브라우저 표시본 PDF 다운로드와 1페이지 렌더 검증. 수기 재수강 근거는3페이지 위치 미확인 표시. 성적표 가상 예제의 기초교양9 표시 연결 확인. 원본 불변. |
| 배포 자산 | yes | passed | python -m pip wheel . --no-deps --wheel-dir .local/wheel-20261001 | 0.6.0 wheel 빌드 및 웹·성적표·OCR·PDF 표시 자산7개 포함 검증. |
| 연구실 읽기 전용 접속 | yes | passed | - | 자기 계정 SSH와 loopback 중계를 통한 모델 목록 조회 성공, 기존 Gemma 모델 확인. 모델 적재·추론 없음, 시험용 중계 종료와 로컬 포트 닫힘 확인. 나루 모델·GPU를 변경하지 않음. |
| 연구실 별도 GPU 실제 추론 | yes | not_run | - | 기존 DSW20%디스크 여유 게이트 미충족. 모델 다운로드 없이 기존 모델·빈 GPU1장·메모리 임시공간으로 시험하는 제한 예외에 대한 사용자 답변 대기. 모델 목록 확인을 추론 성공으로 기록하지 않음. |
| 전체 요청 완료 게이트 | yes | failed | python .agents/skills/harness/scripts/validate.py --project . --run 20261001-integration-v4 --complete | 필수 lab-inference 작업이 pending이라 전체 완료를 차단함. PDF 구현·독립 리뷰·QA4개 작업은 확인된 결과를 재사용. 전체 초기 프로토타입 완료 조건은 아직 달성하지 않음. |
| GitHub Actions | yes | passed | - | 구현 커밋516a8d2의 PR5 자동 검증 전 단계 성공: 단위 테스트·계약·배포 자산·Mermaid 전체 렌더·공개 보고/문서 검사. 실행36776766182: https://github.com/jeongiryang/kg-ontology-decision-framework-reboot/actions/runs/36776766182 . GPU 추론을 검사하는 작업은 아님. |
| 보고서 PDF 사전 렌더 | yes | passed | - | 첫 생성본4페이지 전체를90dpi PNG로 렌더링해 시각 확인. 한글, 표 경계·페이지 번호, 잘림·겹침·고아 페이지 없음. 최종 생성본도 별도로 전 페이지 검증한다. |
| 개발원 직접 UAT | no | not_run | - | 사용자 지시로 이번 단계에서 생략. 합성 자동 평가를 개발원 직접 평가로 부르지 않음. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **warning**: 실제 LLM GPU 추론은 아직 미실행이며 전체 요청의 필수 잔여 작업이다. 사용자에게 제시한 제한적 디스크 기준 예외 확인 또는 기준을 만족하는 환경이 필요하다.
- **info**: 현재 Neo4j는 인증 비활성 localhost 전용 학사 시험 환경이다. 스티커 메모의 기존 계정 대상 DB는 꺼져 있어 그 계정의 인증·운영 DB 연결 성공을 주장하지 않는다.
- **info**: 수기·병합표·중복 문구의 정확한 위치는 추정하지 않고 page_only로 표시한다. 원본·성적표·접속 설정·원시 로그는 Git에 포함하지 않는다.
- **info**: 캡스톤 II PASS→졸업작품, 공모전 자동 대체, 심화전공 과목별 배분·개인 면제·소급 적용 등 보류 판단은 변경하지 않았다. 성적표 결과는 최종 졸업 인증이 아니다.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
