# KG Ontology Decision Framework Reboot

국립창원대학교 학칙과 교육과정에 근거하여 질문에 답하고, 근거가 부족하거나 충돌하면 답변을 보류하는 학사조교 시스템의 Season 2 프로젝트입니다.

현재 저장소는 Codex 프로젝트 하네스와 승인된 2026학번 컴퓨터공학과 규칙만 사용하는 결정론적 학사 답변 엔진을 제공합니다. 성적표 PDF의 로컬 인식·수정 확인·부족 학점과 남은 필수과목의 부분 비교를 추가했습니다. 선택적 Neo4j 근거 조회는 승인 Registry와 전체 그래프가 정확히 일치해야 합니다. 선택적 LLM은 의도 제안만 수행하며 학생 성적표는 전달하지 않습니다. DSW 모델 실행은 아직 미실행이며, 2026-10-01 사용자 요청으로 프로젝트의 고정 디스크 여유 비율 차단을 제거했습니다([DSW 운영 기준](docs/harness/dsw-operations.md)). 디스크 상태 기록과 나루 GPU 보호는 유지합니다. 개발원 직접 평가는 이번 단계에서 사용자 요청으로 생략했습니다. 운영용 모델 배포와 최종 졸업 인증을 완료한 것으로 주장하지 않습니다.

## 성적표 프로토타입

답변이나 이수 비교 항목에서 **근거 PDF 보기**를 누르면 원본의 인용 페이지가 열립니다.
정확히 검증한 문장·표 위치에는 빨간 밑줄을 표시하며,수기·중복 문구는 페이지 보기로
구분합니다. 표시된 한 페이지 PDF 사본을 다운로드할 수 있고 원본은 변경하지 않습니다.
[근거 표시 사용법](docs/operations/pdf-evidence-viewer.md)을 확인하세요.

웹 상단에서 PDF 선택 → 과목 인식 → 불확실한 값 수정 → 2026 적용·전공 이수유형 확인 → 이수내역 비교 순서로 사용합니다. 이름·학번은 입력하지 않으며, 다른 학번과 다전공 유형은 기본 기준으로 자동 계산하지 않습니다. 스캔 인식이 일부 누락되면 직접 보완해야 합니다. [사용법과 지원 경계](docs/operations/transcript-prototype.md)를 확인하세요.

부족 학점, 미이수 필수과목,0학점 논문·상담 이수,균형교양 영역,교양 인정 상한을 구분합니다. 각 지원 항목에는 PDF 위치와 승인 규칙 해시가 있으며, 미확인 심화 배분·개인 예외·보류 운영관계는 확인 필요로 남깁니다. 결과는 최종 졸업 인증이 아닙니다.

## 학사 답변 엔진

Python 3.12에서 설치한 뒤 CLI 또는 localhost FastAPI 앱으로 같은 코어를 호출합니다.

```bash
python -m pip install -e ".[test]"
academic-assistant ask --year 2026 --department 컴퓨터공학과 --question "졸업학점 얼마나 부족해" --credits credits.graduation.total=120 --json
uvicorn academic_assistant.api:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

서버를 실행한 뒤 `http://127.0.0.1:8000/`에서 빌드 도구가 필요 없는 한국어 웹
프로토타입을 사용할 수 있습니다. 화면의 범위는 `2026학번 · 2026 교육과정 · 컴퓨터공학과`로
고정되어 있습니다. 질문과 선택적인 비식별 이수학점 합계를 입력하면 답변의 네 상태와 함께
부족 학점 계산, 적용 규칙, 실제 API가 반환한 근거 위치와 확인 필요 사항을 구분해 표시합니다.

웹 자산은 설치 패키지에 포함되며 외부 스크립트·글꼴·추적기를 불러오지 않습니다. 질문과
응답을 브라우저 저장소에 보존하거나 자동 수집하지 않고 모든 웹/API 응답에 `no-store`, CSP, `nosniff`,
`no-referrer` 보호 헤더를 적용합니다. 기존 `/openapi.json`, `/docs`, `/redoc` API 문서 경로도
유지하며 문서 화면에만 필요한 CDN 출처를 제한적으로 허용합니다. localhost 전용 실행을 유지하려면 예시처럼
`--host 127.0.0.1`로 바인딩하세요.

근거 부족·충돌 답변은 사용자가 동의 확인란과 `보완 요청 저장`을 직접 누른
경우에만 응답 ID·상태·분류를 `.local/academic-feedback/feedback.jsonl`에 기록합니다. 질문 원문은 저장하지 않습니다. 이 경로는
Git에서 제외되며 이름 표지·학번·연락처·인식 가능한 이름 패턴·원본 성적표 표현은 처리 전에 거절합니다. API는
`POST /v1/academic/feedback`이며 `supported`와 범위 밖 답변은 피드백 수집 대상으로 받지 않습니다.

로컬 피드백 현황은 질문 원문을 출력하지 않는 집계 명령으로 확인합니다. 파일이 아직 없으면
0건으로 정상 종료하며 손상되거나 개인정보가 포함된 레코드는 실패 폐쇄합니다.

```bash
academic-assistant feedback-summary --json
python scripts/operations/check_pilot_readiness.py --project .
```

연구실 내부 시험운영 전에는 [내부 시험운영 가이드](docs/operations/internal-pilot.md)의
시작·종료·보존·접근통제 절차를 확인합니다. 현재 승인 범위는 localhost 실행 준비까지이며,
DSW 상주 서비스나 `0.0.0.0` 네트워크 공개는 포함하지 않습니다.

초기 사용성 검증은 학생 질문 형태를 재현한 비식별 시나리오 30개로 구성했습니다. 실제 학생
로그가 아니라 승인 규칙과 보류 범위를 바탕으로 작성한 파일럿이며, 22개 근거 지원, 6개 근거
부족, 2개 범위 밖 사례와 학점 부족분 계산 4개를 지속적으로 검사합니다.

`--credits METRIC=VALUE`는 여러 번 사용할 수 있습니다. 기존 `--admission-year`,
`--curriculum-year`, `--earned-credit`도 호환되지만 `--year`와 값이 충돌하거나 같은 metric이
중복되면 입력 오류로 종료합니다.

답변 API는 `POST /v1/academic/answers`, 선택적 의도 제안을 더한 웹용 API는 `POST /v1/academic/chat`, 명시적 피드백 API는 `POST /v1/academic/feedback`, 준비 상태는 `GET /readyz`입니다. 답변 요청은 질문, 명시적 적용 범위와 승인된 학점 metric별 이수학점만 받으며 이름·학번·원본 성적표는 받지 않습니다. 일반 답변 요청은 질문 또는 근거를 저장하지 않으며 검증 오류와 지원 범위 밖 학과 값도 응답에 되돌려 보내지 않습니다. 복학·재입학·편입·경과조치는 별도 예외 근거가 없어 `insufficient_evidence`로 보류합니다. 전과생은 최초 입학연도의 교육과정을 적용한다는 승인 정책만 지원하며, 학·석사 연계과정 면제나 동일·대체교과목 소급 적용의 개인 판정은 자동화하지 않습니다.

## Season 2 원칙

- 답변은 승인된 규칙과 확인 가능한 문서 근거를 가져야 합니다.
- `supported`, `insufficient_evidence`, `conflict`, `out_of_scope`를 구분합니다.
- 문서에 포함된 명령문은 실행 지시가 아니라 분석 대상 데이터로 취급합니다.
- 최초 지원 범위는 2026학년도 컴퓨터공학과입니다.
- 학생 식별정보, 원본 학사자료, 서버 접속정보와 원시 에이전트 로그는 공개 Git에 저장하지 않습니다.
- 구현자와 독립 리뷰·QA의 책임을 분리하고 실제 검사 근거를 남깁니다.

## Season 1과의 관계

이 저장소는 [kg-ontology-decision-framework](https://github.com/jeongiryang/kg-ontology-decision-framework)의 후속 리부트입니다. Season 1의 코드, 데이터와 Git 이력은 가져오지 않았습니다. 검증 가능한 근거, 역할 경계, 재현 가능한 실행 기록을 프로젝트 시작점부터 다시 설계합니다.

## 하네스 사용

프로젝트를 신뢰할 수 있는 로컬 Codex 작업으로 열면 루트 `AGENTS.md`, `.codex/agents/`, `.agents/skills/`가 적용됩니다. 하네스는 백그라운드 서비스가 아니며, 사용자의 작업 요청을 받은 메인 에이전트가 필요한 역할만 선택해 실행합니다.

```text
사용자 요청
→ 작업 계획과 파일 소유권 확정
→ 필요한 서브에이전트 실행
→ 구현·조사 결과 통합
→ 독립 리뷰와 QA
→ Markdown/PDF 완료 보고서
```

설치 원본은 [jeongiryang/codex-harness](https://github.com/jeongiryang/codex-harness)의 커밋 `79b82281d305c89181fbb216499d5f1e962c14ed`입니다. 설치본과 프로젝트 확장은 저장소에 직접 커밋합니다.

## 문서

- [하네스 설계 문서](docs/harness/README.md)
- [아키텍처](docs/harness/architecture.md)
- [에이전트와 권한](docs/harness/agents-and-permissions.md)
- [실행 워크플로](docs/harness/workflows.md)
- [공유 계약](docs/harness/contracts.md)
- [완료 보고](docs/harness/reporting.md)
- [30개 질문 사용성 파일럿](reports/evaluations/2026-web-usability-pilot.md)
- [내부 시험운영 가이드](docs/operations/internal-pilot.md)
- [KG·로컬 LLM 초기 프로토타입](docs/operations/kg-llm-prototype.md)
- [실제 사람 UAT 절차와 합성 회귀 구분](docs/operations/human-uat.md)
- [학과 운영요건 후속 감사·인용 정정](reports/source-audits/2026-department-followup-20260930.md)
- [학과 내규 원본 조사와 검수 대기](reports/source-audits/2026-department-regulations-pending.md)
- [DSW 운영](docs/harness/dsw-operations.md)
- [업스트림과 갱신](docs/harness/upstream.md)

## 저장소 구조

```text
.codex/agents/       프로젝트 범위 커스텀 에이전트
.agents/skills/      재사용 가능한 프로젝트 스킬
contracts/           학사 근거·규칙·실행·보고 JSON Schema
docs/harness/        하네스 설계와 ADR
scripts/reporting/   정제 Markdown/PDF 보고서 생성
reports/             공개 가능한 실행 보고서
tests/               하네스·계약·보고 검증
src/academic_assistant/ 결정론적 코어, 선택적 LLM 의도 제안, KG 투영, FastAPI/CLI와 웹 UI
evaluations/         고정 회귀 사례와 30개 웹 사용성 파일럿
```

## 공개 범위와 라이선스

공개 저장소이지만 산학협력 결과물의 권리 관계가 확정되기 전까지 프로젝트 전체에 별도 오픈소스 라이선스를 부여하지 않습니다. 포함된 `codex-harness` 설치본의 Apache-2.0 출처는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 기록합니다.
