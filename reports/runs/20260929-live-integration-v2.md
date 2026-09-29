# 2026학번 학사 프로토타입 실연결·근거 경계 완료 보고

- 실행 ID: `20260929-live-integration-v2`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0+codex-harness.79b82281`
- 브랜치: `codex/kg-llm-prototype`
- 기준 커밋: `5f26e6edbda81736e7e58aaef02904def039ea39`
- 결과 커밋: `e01c6c8e16629b9cc6afcf21db63073c8b86a837`

## 요청

PR 검토·병합, 최신 웹 화면 확인, 교수님 PC Gemma 저부하 연결 시험, 전용 Neo4j 실제 적재·조회, 학과 운영조건의 확인·보류 기록을 완료한다.

## 요약

승인된 출처 2건과 규칙 26건의 그래프 68노드·115관계를 전용 빈 로컬 Neo4j에 적재·조회했다. 교수님 PC Gemma에는 비식별 합성 의도 요청 1건만 보냈고 모델은 학사 판정을 바꾸지 않는다. 2026년 코딩 테스트 400점은 현재 시범 운영 확인으로만 기록했으며 캡스톤 II와 졸업작품의 관계는 보류했다. 질문 전체가 승인 범위에 없는 학석사 연계과정 혼합 질문은 근거 부족으로 거절한다. 웹 프로토타입과 독립 검증을 통과했다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| fixed | `src/academic_assistant/core.py` | 타학과·보류 운영관계 및 미확인 혼합 혜택 질문을 실패 폐쇄하고 전공필수 9과목의 EvidencePacket 문장을 승인 규칙과 일치시켰다. |
| security | `src/academic_assistant/feedback.py` | 동의한 새 피드백에서 학번 표현을 안전하게 허용하되 질문 원문은 저장하지 않는다. |
| changed | `src/academic_assistant/llm.py` | LM Studio용 엄격 JSON 스키마와 추론 비활성 의도 제안 어댑터를 추가했다. |
| changed | `evaluations/academic-answer-mvp.json` | 2026학번·26학번, 근거 없는 운영조건, 혼합 면제·혜택 등 고정 평가를 265건으로 늘렸다. |
| changed | `scripts/validation/validate_academic_answer_engine.py` | 265건 평가의 분류별 기대 개수를 동기화했다. |
| changed | `tests/test_academic_answer_engine.py` | 승인 규칙 문장과 EvidencePacket 대조 및 혼합 질문 실패 폐쇄 회귀를 추가했다. |
| changed | `tests/test_academic_chat_api.py` | 범위 밖·미확인 운영관계 채팅 경계를 검증했다. |
| changed | `tests/test_academic_feedback_ops.py` | 비식별 학번 표현과 원문 비보존 피드백을 검증했다. |
| changed | `tests/test_academic_grounded_chat.py` | LM Studio 엄격 스키마 어댑터의 모델 요청을 모의 검증했다. |
| documentation | `reviews/academic/clarifications/2026-department-practices-20260929.md` | 검수 세션 권한, 2026년 시범 400점 확인, 캡스톤 I U·차년도 II 불가 확인 및 II→졸업작품 판단 보류를 원답변과 함께 기록했다. |
| documentation | `docs/operations/kg-llm-prototype.md` | 실 Neo4j·저부하 Gemma 연결의 확인 범위, 제한적 질문 해석, 보류 규칙과 후속 운영 안전 조건을 설명했다. |
| documentation | `README.md` | 초기 프로토타입의 현재 연결 상태와 운영 문서를 연결했다. |
| documentation | `CHANGELOG.md` | 구현·검증·보류 결정을 누적 변경 이력에 기록했다. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| academic_source_auditor | official-department-source-audit | completed | 공식 학과 게시물 신원·해시·조항을 대조하고 PCCP·캡스톤·졸업작품 관계의 확인 범위를 분리했다. |
| harness_worker | answer-boundary-and-evidence-fixes | completed | 학번 범위·근거 문장·혼합 면제 질문을 수정하고 회귀 평가를 추가했다. |
| harness_reviewer | final-review | completed | 차단 결함을 발견·재수정시킨 뒤 최신본에서 근거 없는 지원 답변과 경계 오류가 없는지 독립 검토했다. |
| harness_qa | final-qa | completed | 전체 테스트, 265건 학사 평가, 지원 근거 전수 검사와 보류 경계를 독립 검증했다. |
| main_orchestrator | integration-and-publication | completed | 전용 로컬 Neo4j 적재·조회와 합성 Gemma 1건 연결, 웹 확인, 통합·공개 보고를 담당했다. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 전체 단위 테스트 | yes | passed | python -m unittest discover -s tests -p test_*.py -q | 229건 통과. |
| 학사 답변·사용성 평가 | yes | passed | - | 공식 평가 265건과 사용성 30건 통과. |
| 지원 근거 전수 대조 | yes | passed | - | 유효 249건 중 지원 93건의 RuleFact 문장·해시·출처·위치가 일치하고 근거 부족 138건은 규칙·인용이 비어 있다. |
| 보류 운영조건·개인정보 경계 | yes | passed | - | PCCP·캡스톤·졸업작품·공모전 질문과 혼합 면제·혜택 질문은 근거 없이 거절했다. |
| 실 Neo4j 적재·조회 | yes | passed | - | 전용 빈 로컬 DB에 승인 KG 68노드·115관계를 적재하고 수량·단일 그래프 해시를 재조회했다; 시험 서버는 종료했다. |
| 실 Gemma 저부하 연결 | yes | passed | - | 나루 게이트웨이 없이 단기 터널로 비식별 합성 의도 1건을 약 1.45초에 처리했다. GPU 이용률은 전후 0%; 터널은 종료했다. |
| 최신 웹·API | yes | passed | - | localhost 웹·API에서 학점·전공필수 지원 답변과 보류 질문의 근거 없는 거절, 준비 상태를 확인했다. |
| 하네스·독립 리뷰·QA | yes | passed | python .agents/skills/harness/scripts/validate.py --project . --run 20260929-prototype-final-v5 --complete | 실제 agent ID 등록 및 작업 입력 지문 일치; 최종 리뷰·QA 완료 오류 0건. |
| 보고·문서 검사 | yes | passed | - | 공개 보고 사전 검사, Markdown 링크 16개, Mermaid 2개 렌더링, 변경 공백 검사가 통과했다. |
| GitHub Actions PR 검사 | yes | passed | - | PR #2의 Harness CI validate가 코드 커밋 e01c6c8에 대해 성공했다. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **warning**: 2026년 시범 PCCP 400점은 사용자의 학과 확인으로만 기록했고 미래 학번 고정 기준으로 승인하지 않았다. 캡스톤 II→졸업작품과 공모전 졸업작품 대체는 판단 보류다.
- **warning**: 질문 표현 인식은 제한적이다. 실제 학생정보·상시 모델 서빙·외부 공개 전에 추가 평가와 운영 승인이 필요하다.
- **warning**: 로컬 Neo4j 적재는 단일 운영자 시험이다. 동시 적재 전 데이터베이스 제약 또는 직렬화가 필요하다.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
