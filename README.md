# KG Ontology Decision Framework Reboot

국립창원대학교 학칙과 교육과정에 근거하여 질문에 답하고, 근거가 부족하거나 충돌하면 답변을 보류하는 학사조교 시스템의 Season 2 프로젝트입니다.

현재 저장소는 Codex 프로젝트 하네스와 승인된 2026학번 컴퓨터공학과 규칙만 사용하는 결정론적 학사 답변 엔진을 제공합니다. Neo4j, PDF 파서, 로컬 LLM 서빙은 구현 범위에 포함되지 않습니다.

## 학사 답변 엔진

Python 3.12에서 설치한 뒤 CLI 또는 localhost FastAPI 앱으로 같은 코어를 호출합니다.

```bash
python -m pip install -e ".[test]"
academic-assistant ask --year 2026 --department 컴퓨터공학과 --question "졸업학점 얼마나 부족해" --credits credits.graduation.total=120 --json
uvicorn academic_assistant.api:app --host 127.0.0.1 --port 8000
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
경우에만 질문과 응답 ID를 `.local/academic-feedback/feedback.jsonl`에 기록합니다. 이 경로는
Git에서 제외되며 이름·학번·연락처·원본 성적표 표현은 저장 전에 거절합니다. API는
`POST /v1/academic/feedback`이며 `supported`와 범위 밖 답변은 피드백 수집 대상으로 받지 않습니다.

초기 사용성 검증은 학생 질문 형태를 재현한 비식별 시나리오 30개로 구성했습니다. 실제 학생
로그가 아니라 승인 규칙과 보류 범위를 바탕으로 작성한 파일럿이며, 22개 근거 지원, 6개 근거
부족, 2개 범위 밖 사례와 학점 부족분 계산 4개를 지속적으로 검사합니다.

`--credits METRIC=VALUE`는 여러 번 사용할 수 있습니다. 기존 `--admission-year`,
`--curriculum-year`, `--earned-credit`도 호환되지만 `--year`와 값이 충돌하거나 같은 metric이
중복되면 입력 오류로 종료합니다.

답변 API는 `POST /v1/academic/answers`, 명시적 피드백 API는 `POST /v1/academic/feedback`, 준비 상태는 `GET /readyz`입니다. 답변 요청은 질문, 명시적 적용 범위와 승인된 학점 metric별 이수학점만 받으며 이름·학번·원본 성적표는 받지 않습니다. 일반 답변 요청은 질문 또는 근거를 저장하지 않으며 검증 오류와 지원 범위 밖 학과 값도 응답에 되돌려 보내지 않습니다. 복학·재입학·편입·경과조치는 별도 예외 근거가 없어 `insufficient_evidence`로 보류합니다. 전과생은 최초 입학연도의 교육과정을 적용한다는 승인 정책만 지원하며, 학·석사 연계과정 면제나 동일·대체교과목 소급 적용의 개인 판정은 자동화하지 않습니다.

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
src/academic_assistant/ 결정론적 코어, FastAPI/CLI 어댑터와 no-build 웹 UI
evaluations/         184개 회귀 사례와 30개 웹 사용성 파일럿
```

## 공개 범위와 라이선스

공개 저장소이지만 산학협력 결과물의 권리 관계가 확정되기 전까지 프로젝트 전체에 별도 오픈소스 라이선스를 부여하지 않습니다. 포함된 `codex-harness` 설치본의 Apache-2.0 출처는 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)에 기록합니다.
