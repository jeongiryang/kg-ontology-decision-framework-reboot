# 2026학번 학사 답변 엔진 MVP 완료 보고서

- 실행 ID: `20260918-academic-answer-engine-v8`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0+codex-harness.79b82281`
- 브랜치: `main`
- 기준 커밋: `332a985519fb138588688cc58a15fc8608864ed2`
- 결과 커밋: `643f7808d01a68f6b5d71bfa933defd9edbc7823`

## 요청

승인된 SourceEntry 1건과 RuleFact 13건을 사용해 한국어 질문에 근거와 제한된 답변 상태를 반환하는 결정적 공통 코어, CLI와 FastAPI API를 구축한다. LLM, 임베딩과 GPU는 사용하지 않고 48건 평가셋, 실패 폐쇄, 개인정보 거절과 근거 연결을 검증한다.

## 요약

2026학번 컴퓨터공학과의 승인 규칙만 사용하는 결정적 학사 답변 엔진을 구현했다. 공통 코어를 FastAPI POST /v1/academic/answers와 academic-assistant CLI가 공유하며, 13개 규칙과 묶음 질문, 비식별 학점 부족분 계산, 미검증 운영요건 거절, 범위 밖·충돌 상태를 지원한다. 레지스트리 해시와 승인 상태가 맞지 않으면 서비스는 실패 폐쇄한다. 독립 리뷰와 QA에서 발견한 의도 경계 문제를 보완한 뒤 141개 테스트와 고정 평가 48건을 통과했다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| added | `src/academic_assistant/` | 입력 검증, 범위 판정, 결정적 의도 매칭, 승인 규칙 조회, 학점 부족분 계산, EvidencePacket과 템플릿 답변을 구현하고 CLI·FastAPI가 같은 코어를 사용하도록 했다. |
| added | `config/academic-intents.json` | 13개 규칙과 묶음 질문을 위한 한국어 의도·동의어·보호 주제·모호성 경계를 등록했다. |
| security | `config/academic-registry-pins.json` | 규칙 13건, 출처, 미검증 조사 항목과 의도 카탈로그의 정규 해시를 고정해 내용 변경 시 실패 폐쇄한다. |
| added | `contracts/academic-answer-request.schema.json` | 질문, 입학연도, 학과와 선택적 비식별 학점 합계를 받는 엄격한 요청 계약을 추가했다. |
| added | `contracts/academic-answer-response.schema.json` | 답변, 제한된 상태, EvidencePacket과 선택적 학점 계산을 반환하는 응답 계약을 추가했다. |
| added | `contracts/academic-intent-profile.schema.json` | 의도 카탈로그 전체 구조를 검증해 누락되거나 잘못된 프로필을 서비스 준비 단계에서 거절한다. |
| added | `evaluations/academic-answer-mvp.json` | 대표·표현변형·학점계산·미검증 운영요건·범위·모호성·개인정보를 포함하는 고정 평가 48건을 추가했다. |
| added | `tests/test_academic_answer_engine.py` | API·CLI·코어 일치, 인용·해시 연결, 입력 거절, 레지스트리 변조, 의도 경계와 실패 폐쇄를 검사하는 회귀 테스트를 추가했다. |
| added | `scripts/validation/validate_academic_answer_engine.py` | 고정 48건의 분류 수와 예상 상태·규칙·계산을 검증하는 CI 검증기를 추가했다. |
| changed | `.github/workflows/harness-ci.yml` | Python 3.12 설치형 패키지 테스트와 학사 답변 엔진 평가 검증을 GitHub Actions에 추가했다. |
| documentation | `README.md` | FastAPI 실행법과 요청한 --year, --credits CLI 사용 예시, 지원 범위와 제한을 문서화했다. |
| documentation | `docs/harness/` | 답변 계약, 처리 흐름, 실패 폐쇄, 의도 매칭 경계와 검증 절차를 하네스 문서에 동기화했다. |
| changed | `harness-manifest.yaml` | 새 계약, 구성, 평가셋과 검증기를 하네스 구조화 색인에 등록했다. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| harness_architect | architecture | completed | 공통 코어, 요청·응답, 결정적 의도 매칭, 상태와 실패 모드를 설계했다. |
| harness_worker | implementation | completed | 코어, CLI, FastAPI, 계약, 평가셋, 테스트와 CI를 구현하고 리뷰에서 발견된 경계 결함을 보완했다. |
| harness_reviewer | independent-review | completed | 의도 오인, 접속사 우회, 근거 연결, 개인정보 노출, none_listed 과장과 API·CLI 불일치를 독립 검토해 최종 차단 결함이 없음을 확인했다. |
| harness_qa | qa | completed | 141개 테스트, 고정 평가 48건, 공격 경계, 근거·핀, API·CLI·코어 일치, 문서와 Mermaid 렌더를 검증했다. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 전체 단위·통합 테스트 | yes | passed | uv run --isolated --python 3.12 --with-editable ".[test]" --with-requirements scripts/reporting/requirements.txt python -m unittest discover -s tests -p "test_*.py" -q | Python 3.12에서 141개 테스트가 통과했다. |
| 고정 학사 답변 평가 | yes | passed | uv run --isolated --python 3.12 --with-editable ".[test]" python scripts/validation/validate_academic_answer_engine.py --project . | 대표 13, 표현변형 13, 학점계산 11, 미검증 운영요건 3, 범위·미지원 4, 모호성·충돌 2, 개인정보·잘못된 입력 2 등 48건이 모두 통과했다. |
| 요청된 CLI 인터페이스 | yes | passed | academic-assistant ask --year 2026 --department 컴퓨터공학과 --question "졸업학점 얼마나 부족해" --credits credits.graduation.total=120 --json | supported 상태, 졸업 기준 130학점, 이수 120학점, 부족 10학점과 승인 규칙·출처 인용을 반환했다. |
| 근거 연결과 인터페이스 일치 | yes | passed | - | 평가셋의 supported 38건이 승인된 RuleFact 해시, SourceEntry와 PDF 위치에 연결됐고 유효 입력 46건의 코어·API·CLI 응답이 일치했다. |
| 개인정보·변조·모호성 실패 폐쇄 | yes | passed | - | 개인정보와 잘못된 학점은 비반향 422 또는 일반 오류로 거절됐고, 규칙·출처·의도 변조, 예외 학적, 복합어·부정·선택·접속사 우회가 근거 없는 권위 답변을 만들지 못했다. |
| 하네스 실행 완료 | yes | passed | py -3.13 .agents/skills/harness/scripts/validate.py --project . --run 20260918-academic-answer-engine-v8 --complete | 설계, 구현, 독립 리뷰와 QA가 0개 completion error로 완료됐다. |
| 학사·하네스·보고 검증 | yes | passed | - | 하네스 동기화, 학사 지식, 검수 세션, 미검증 연구 격리, 정적 하네스와 공개 보고 검사가 모두 통과했다. |
| Mermaid 실제 렌더 | yes | passed | - | 하네스 문서의 Mermaid 블록 2개를 실제 SVG로 렌더링해 문법과 출력 생성을 확인했다. |
| PDF 전 페이지 시각 검수 | yes | passed | pdftoppm -png -r 150 reports/pdf/20260918-academic-answer-engine-v8.pdf tmp/pdfs/20260918-academic-answer-engine-v8/page | A4 3개 전 페이지를 렌더링해 한글 가독성, 표 정렬, 여백, 잘림, 겹침, 고아 페이지와 페이지 번호를 확인했다. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **warning**: 공모전 수상, 캡스톤디자인 선후관계와 PCCP 400점 후보는 공식 학과 근거가 없어 계속 insufficient_evidence로만 답한다.
- **info**: MVP는 등록된 한국어 의도만 결정적으로 처리하며 과목별 성적표, 재수강, 학점인정, 대체과목과 다른 입학연도·학과는 지원하지 않는다.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
