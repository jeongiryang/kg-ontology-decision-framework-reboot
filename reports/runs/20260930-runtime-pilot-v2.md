# 학사 검증 결과 - DSW 추론 보류

- 실행 ID: `20260930-runtime-pilot-v2`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0+codex-harness.79b82281`
- 브랜치: `codex/academic-runtime-pilot`
- 기준 커밋: `c274ed7f6bb65cb782f6b179eddba7671a3b2b56`
- 결과 커밋: `91e47a6aa97fc366155d29ea34f8466a99ec1f76`

## 요청

남은 프로토타입 작업을 진행하고 GPU 대상은 교수님 PC가 아닌 연구실 DSW 서버로 변경한다.

## 요약

승인 출처 2건·규칙 26건을 유지하면서 네 규칙의 PDF 위치를 정정하고 정확 일치 Neo4j 읽기 경로, 안전한 원자적 적재, 선택적 LLM 저부하 보호를 구현했다. 실제 전용 Neo4j에서 68노드·115관계와 CLI/API 근거 일치, 동시 적재, 롤백, 변조 거절을 확인했다. 단위 테스트 242건, 학사 평가 265건, 사용성 30건, 별도 합성 15건과 독립 리뷰·QA가 통과했다. 최신 웹은 로컬 Registry 기반·LLM 비활성으로 실행한다. DSW 저장 공간이 최소 기준에 못 미쳐 실제 GPU 추론은 실행하지 않았다. 사람 UAT와 미승인 운영관계도 완료로 주장하지 않는다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| added | `src/academic_assistant/neo4j_evidence.py` | 전용 로컬 그래프의 제한된 읽기와 승인 Registry 전체 일치 검사. 누락·변조·연결 오류는 실패 폐쇄한다. |
| changed | `src/academic_assistant/core.py` | 기존 판정·규칙 선택은 유지하고 선택적 그래프에서 검증한 동일 인용만 읽는다. |
| fixed | `src/academic_assistant/api.py` | 그래프 연결과 준비 상태 검사, 단일 엔진 초기화 및 종료 오류에도 연결·캐시 정리 보장. |
| changed | `src/academic_assistant/cli.py` | API와 동일한 선택적 그래프 경로, 실패 시 종료 코드 3 및 연결 정리. |
| security | `src/academic_assistant/llm.py` | 프로세스당 동시 1건, 최소 간격·실패 휴지기·소규모 캐시. 원문 질문 전달 없이 고정 의도 제안만 허용. |
| fixed | `scripts/knowledge/build_graph.py` | 비어 있는 전용 DB만 적재. 유일 제약·원자적 트랜잭션으로 동시 적재 중 하나만 커밋한다. |
| changed | `scripts/operations/check_pilot_readiness.py` | 선택한 그래프의 정확 일치와 모델 설정을 검사하되 준비 검사에서 추론하지 않는다. |
| changed | `config/pilot.env.example` | 그래프 선택, 제한된 시간·모델 요청 보호 설정 예시. 기본 Registry·추론 비활성. |
| fixed | `knowledge/rules/cwnu.cs.2026.major-required-course-set.json` | 9개 과목 근거를 PDF 262-263쪽(인쇄 254-255쪽) 교과목 행으로 정정. |
| fixed | `knowledge/rules/cwnu.cs.2026.major-counseling-completion.json` | 심층상담 근거를 PDF 263쪽(인쇄 255쪽), CDA0088 행으로 정정. |
| fixed | `knowledge/rules/cwnu.cs.2026.general-recommended-courses.json` | 권장 교양 근거를 PDF 261쪽(인쇄 253쪽), GEA7260/7261/7301 행으로 정정. |
| fixed | `knowledge/rules/cwnu.cs.2026.graduation.thesis-completion-result.json` | 논문 0학점 근거를 PDF 263쪽 교과목 행과 565쪽(인쇄 557쪽) 의무 조항으로 연결. |
| added | `knowledge/evidence-corrections.json` | 위치·발췌만 정정한 네 규칙의 이전/현재 해시와 감사 자료를 연결. 기존 승인 결정은 불변. |
| changed | `config/academic-registry-pins.json` | 정정된 네 규칙의 canonical SHA256을 실제 현재 파일과 일치시킴. |
| security | `scripts/validation/validate_academic_clarifications.py` | 이전 원문 위치를 복원한 전체 규칙 해시로 정정 범위를 제한하고 닫힌 검수 세션을 보존. |
| changed | `tests/test_academic_clarifications.py` | 정정 전 검수 fixture와 이후 정정 대장을 분리. |
| added | `tests/test_neo4j_evidence.py` | 그래프 변조·오류·CLI/API 일치 및 초기화 경쟁·종료 실패 회귀 7건. |
| changed | `tests/test_academic_kg.py` | 유일 제약 확인과 비어 있지 않은 DB 거절 검사 동기화. |
| added | `tests/test_llm_budget.py` | 동시 호출·캐시·실패 휴지기·만료·유한 설정값 회귀 5건. |
| changed | `tests/test_academic_grounded_chat.py` | 전송 계약 모의 검사를 캐시 영향과 분리. |
| added | `evaluations/academic-reference-patterns.json` | 교수님 챗봇 평가 요약의 유형만 참고한 별도 합성 질문 15건. 실제 UAT 로그나 승인 근거가 아님. |
| added | `scripts/validation/validate_reference_patterns.py` | 합성 질문의 상태·규칙·현재 인용·학점 계산·출처 성격을 검증. |
| added | `tests/test_reference_patterns.py` | 합성 15건에서 코어·CLI·API 응답 동일성 검사. |
| changed | `.github/workflows/harness-ci.yml` | 별도 합성 질문 검증을 기존 전체 CI에 추가. |
| changed | `harness-manifest.yaml` | 265건 평가와 그래프·모델 보호·합성 질문 검사 색인 동기화. |
| documentation | `docs/harness/decisions/0014-exact-graph-runtime-and-evidence-lineage.md` | 정확 그래프와 근거 정정 계보, 주 구현·독립 검증 대체 절차를 ADR로 기록. |
| documentation | `docs/harness/decisions/README.md` | 새 ADR 색인 연결. |
| documentation | `docs/harness/workflows.md` | 네이티브 worker 생성 실패와 실제 메인 구현·독립 검증을 구분. |
| documentation | `docs/operations/kg-llm-prototype.md` | 현재 그래프 모드와 검사, DSW 전환·공간 차단, 과거 Mint 기록과 현재 미실행 구분. |
| documentation | `docs/operations/human-uat.md` | 새 참가자·비식별 절차·고정 버전·평가 기준 정의. 사람 세션은 미실행. |
| documentation | `reports/evaluations/20260930-reference-patterns.md` | 합성 15건 결과와 사람 UAT 미실행을 별도로 명시. |
| documentation | `reports/source-audits/2026-department-followup-20260930.md` | 공식 게시물·원문 위치와 보류 관계의 확인 한계 기록. |
| documentation | `README.md` | 현재 기능·DSW 미실행·사람 UAT 상태와 운영 문서 연결. |
| documentation | `CHANGELOG.md` | 구현·정정·검증·미완료 사항을 누적 이력에 기록. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| academic_source_auditor | citation-correction-review | completed | 네 원문 위치와 해시 계보, 기존 승인 내용 및 닫힌 검수 세션 불변을 독립 확인. |
| harness_architect (read-only review fallback) | runtime-review | completed | 이전 P2 초기화 경쟁·종료 정리 결함을 발견한 뒤 수정본을 재검토. 최신 9개 지문 일치, 새 결함 없음. |
| harness_explorer (execution QA fallback) | runtime-qa | completed | 전체 242개 테스트·7개 검증기 및 독립 초기화·오류 탐침 실행. 입력 지문 8개 불변. |
| main_orchestrator | runtime-implementation-integration | completed | 새 worker 생성의 thread limit 실패 후 메인이 구현. 실제 Neo4j·웹 검증과 정제 보고를 수행. 미시작 native 구현 작업은 pending으로 보존. |
| dsw_compute_operator (main safety preflight) | dsw-model-inference | blocked | 사용자 지정 DSW 읽기 전용 사전 확인 완료. 여유 공간 최소 20% 미달·공용 NAS 접근 오류로 추론·다운로드·서비스 시작 미실행. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 전체 단위 테스트 | yes | passed | python -B -m unittest discover -s tests | 독립 QA Python 3.12.14: 242건 통과. 초기화 경쟁·종료 오류 회귀 포함. |
| 학사 답변·사용성·합성 평가 | yes | passed | - | 265건 답변 평가, 30건 사용성, 별도 합성 15건 통과. 합성은 실제 사람 UAT가 아님. |
| 학사 근거·검수 권한·미검증 분리 | yes | passed | - | 학사 지식·질문 패킷·조사 대기 검증 통과. 정정된 네 위치와 이전/현재 전체 해시 일치. 기존 승인 2출처·26규칙 유지. |
| 실 Neo4j 동시 적재·롤백 | yes | passed | - | 실 프로세스 2개를 동일 빈 전용 DB에 동시 적재: 한 건 커밋, 한 건 거절. 중단 트랜잭션 롤백 확인. 68노드·115관계. |
| 실 그래프 인용·CLI/API 일치·변조 거절 | yes | passed | - | 최신 코드로 동일 graph SHA256 fba58ba45ce810a427cda6af7b0b131b237f8fdd9f8bb45c06ecd697c69c1425 확인. 코어·CLI·API canonical 응답 일치; 변조 거절 및 시험 값 복원. |
| 초기화 경쟁·실패 폐쇄·정리 | yes | passed | - | 독립 탐침: 엔진/reader 각 하나, close 한 번, 캐시 비움. 모의 그래프 장애에서 CLI exit 3·chat 503 및 정제 오류. 실제 시험 Neo4j 정상 종료, 포트 종료 확인, 시험 데이터 보존. |
| 로컬 최신 웹 | yes | passed | - | 브라우저에서 9개 과목과 PDF 262-263쪽 표시, II PASS→졸업작품 질문 근거 부족 거절 확인. 로컬 Registry·추론 비활성·1 worker·질문 접근 로그 비활성. |
| 하네스·독립 리뷰·QA | yes | passed | python .agents/skills/harness/scripts/validate.py --project . --run 20260930-runtime-verification-v2 --complete | 실제 에이전트 결과·지문 확인: 완료 오류 0. 구조 9 agent·8 skill, manifest 동기화 통과. 구현 pending 런과는 구분. |
| 문서·Mermaid·공개 안전 검사 | yes | passed | - | Mermaid 2개 렌더링, 변경 공백·문서 링크·공개 정보 CI 통과. 상대 Windows 도구 경로 첫 실패 후 절대 경로로 재실행 성공. |
| PDF 전 페이지 시각 검사 | yes | passed | - | 보고서 전 페이지를 PNG로 렌더링해 한글·표·잘림·겹침·페이지 번호·고아 페이지를 확인했다. Markdown·JSON·PDF는 동일 정제 입력에서 생성하고 공개 보고 CI로 일치 검사. |
| GitHub Actions | yes | passed | - | PR #3 코드 커밋 91e47a6의 Harness CI validate 성공. 실행 36712481305에서 전체 검사·wheel 웹 자산·Mermaid·공개 문서 검사 모두 성공. |
| DSW 실 GPU 추론 | yes | not_run | - | 약 3.6TB 작업 볼륨의 여유 약 15GB로 1% 미만, 최소 20% 기준 미달. 공용 NAS 접근도 오류. 안전 기준 때문에 모델 다운로드·추론·상주 실행을 하지 않음. |
| 실제 사람 UAT | no | not_run | - | 새 사람 평가 절차만 작성. 교수님 챗봇 JSON은 평가 요약이며 질문/답변 원문이 없어 실제 재실행·승인 근거로 쓰지 않음. |
| 새 native worker 생성 | no | failed | - | Agent thread limit reached. 실제 ID가 반환되지 않은 구현 작업은 pending으로 보존; 메인의 실 구현과 기존 독립 검증 에이전트 재사용을 별도로 기록. |

## 학사 근거 변경

- 출처 신원·문서 해시·규칙의 승인 상태·판정·범위는 변경하지 않음. 네 RuleFact의 근거 위치/발췌와 pin 해시만 원문에 맞게 정정.
- none_listed는 현재 PDF 기재 없음으로 유지. 2026년 시범 PCCP 400점의 기존 학과 확인은 미래 학번 조건으로 확장하지 않음.

## 이슈 및 남은 작업

- **warning**: DSW 실 추론은 미완료다. 승인된 공용 공간의 접근 복구 또는 안전한 여유 공간 확보 후 사전 확인과 합성 1건 저부하 시험이 필요하다. 임의 삭제·Naru GPU 점유·운영 서비스 변경은 하지 않았다.
- **warning**: II PASS→졸업작품, 공모전 자동 대체, 정확한 우수자 면제 범위는 계속 보류다. 사용자 일반 진행 요청으로 승인하지 않았다.
- **info**: LLM 보호는 1프로세스의 요청 제한이다. Naru 전체 부하 통제나 공유 서버 배포 승인을 대신하지 않는다.
- **info**: 사람 UAT 미실행. 합성 평가 통과는 실제 참가자의 사용성 평가 통과가 아니다.
- **info**: Thread limit으로 native 구현은 미시작. 성공한 독립 검증 런과 pending 구현 런을 구분한다.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
