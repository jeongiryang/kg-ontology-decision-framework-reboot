# 친절한 학사 대화와 현재 성적표 재사용

- 실행 ID: `20261001-natural-dialogue`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0 / upstream79b82281d305c89181fbb216499d5f1e962c14ed / prototype0.10.0`
- 브랜치: `codex/natural-dialogue`
- 기준 커밋: `d52281e7134fe60269bbb5b58677ebed37de7564`
- 결과 커밋: `a712ff12c46449946289a963f916fde37e499705`

## 요청

존댓말 안내와 같은 대화의 성적표 재사용을 적용하고, 시연10개뿐 아니라 다른 승인 주제·표현·복합 질문도 지원한다.

## 요약

31승인 주제의 자연스러운 안내·이어 묻기·성적표 비교·새 대화 초기화를 통합했다. 실제 Gemma 안내와 외부 화면·PDF 밑줄을 확인했으며637개 전체 테스트와 독립 검토·QA·제품CI가 통과했다. 기존29규칙·3출처·3보류는 변경하지 않았다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| added | `src/academic_assistant/conversation.py` | 닫힌 전체 질문 문법·정중한 안내·명확한 후속 질문. |
| changed | `src/academic_assistant/grounded_chat.py` | 첫 안내와 실제 생성 문장; grounded_generation.py, llm.py의 완전한 승인 주장, 기존 제한 유지. |
| changed | `src/academic_assistant/models.py` | transcript_models.py와 친절한 안내, 문맥, 주장 필드; 기존 출력 호환. |
| changed | `src/academic_assistant/transcript_assessment.py` | 확인한 현재 기록·선택 항목 비교; 재평가는 한 턴에서만 재사용. |
| changed | `contracts/academic-chat-response.schema.json` | 학사 및 성적표의 요청, 응답 4계약: 안내, 주장 목록, 안전한 문맥, 별도 비교. |
| changed | `src/academic_assistant/web/app.js` | 빠른 첫 답변·동일 요청 다듬기·취소·최대6턴·새 대화. |
| changed | `src/academic_assistant/web/transcript.js` | 확인한 기록만 제공; evidence.js의 PDF 창과 수정, 지우기, 페이지 종료 격리. |
| changed | `src/academic_assistant/web/index.html` | app.css와 질문 중심 안내, 새 대화, 간결한 기록 비교. |
| added | `tests/test_natural_conversation_worker.py` | 33개 구현·문법·원래 입력·개인정보 회귀. |
| added | `tests/test_natural_conversation_qa.py` | 19개 독립 검사, 별도 heldout5개, fixture의31주제62표현,6복합,16거절. |
| added | `tests/web/natural_conversation_ui_test.cjs` | 실제 화면16그룹; gemma_final_ui_test.cjs의 생성문 누락, 재사용 회귀 보강. |
| changed | `pyproject.toml` | 프로토타입0.10.0. |
| changed | `harness-manifest.yaml` | 검증 색인과 .github/workflows/harness-ci.yml의 실제 대화 화면 CI. |
| documentation | `docs/harness/contracts.md` | workflows.md와 안내, 검증 주장, 휘발성 문맥의 분리 흐름. |
| added | `docs/harness/decisions/0022-friendly-volatile-conversation.md` | 추가 상주 서비스 없는 설계 결정과 decisions/README.md 연결. |
| documentation | `docs/operations/conversation-prototype.md` | 전체31주제 사용법; minimal-prototype.md와 README.md의 빠른 시작. |
| added | `docs/operations/professor-demo-questions.md` | 시연10예시와 그 밖의 질문·보류·PDF 확인 순서. |
| documentation | `CHANGELOG.md` | 실제 추가·교정·유지 사항과 검사·보고 연결. |
| added | `reports/evaluations/20261001-natural-dialogue.json` | 실제 검사·실패 보존·3추론·사용 범위의 정제 기록. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| harness_worker | backend | completed | v3 순차 보강 후270개 검증;6개 산출물 고정. |
| harness_qa | qa-fixtures | completed | v3 270개, 화면 그룹,30가상API 독립 실행. |
| harness_reviewer | review | completed | v2 재검토;26입력 일치, 추가 오류 지적 없음. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| Windows 전체 회귀 | yes | passed | .venv/Scripts/python.exe -m unittest discover -s tests -p test_*.py | 637개·생략0·74.843초 통과. |
| 독립 QA·검토 | yes | passed | - | 270개·자연 대화16/기존 Gemma9/성적표10/근거9그룹·정렬 검증.30가상API·모델0. 최종2개 실행 완료 검사 통과. |
| 31주제와 다른 표현 | yes | passed | - | 독립62표현·6복합·16거절, 별도12표현·2성적표 표현·4미지원 조건. 시연10개가 지원 한도가 아님. |
| 학사 근거·권한·격리 | yes | passed | - | 29규칙·3출처·3보류 불변.265코어·95중첩 근거·30사용성·15신규 합성 사례 통과. |
| 실제 Gemma·코어 보존 | yes | passed | - | 총3추론:계측2건12.484/12.750초와 별도 브라우저1건. 실제 검증 문장 표시, 판정·계산·인용 불변; 캐시·현재100학점 부족30 확인. |
| 실제 외부 화면·대화 | yes | passed | - | 외부HTTPS, 동일 질문창의 가상6학점·남은7과목, 이어 묻기와 현재 기록 재사용 확인. |
| 부분 기록·새 대화 | yes | passed | - | 부분 기록의 확정 보류. 새 대화에서 질문·학점·행·파일·페이지·확인·이력 삭제, 맥락 없는 후속 질문의 기준 재선택 확인. |
| Neo4j·원본 PDF | yes | passed | - | 실제 근거 연결,PDF577/인쇄569페이지 exact 위치·PNG/PDF 형식·브라우저 빨간 밑줄 확인. |
| GPU 유휴·나루 보존 | yes | passed | - | 07:07UTC GPU0 활성 모델0·16MiB. 기존 own/Naru 프로세스 보존, 나루GPU1 18510MiB. 추가 추론 없음. |
| 설정·문서·패키지 | yes | passed | - | 하네스·계약·문서 동기화,링크,실제Mermaid2블록·필수7개 패키지 자산 통과. |
| 제품 GitHub CI | yes | passed | - | 실행36827757091 success. Ubuntu637개·플랫폼별6개 생략·52.261초. 모든 단계 통과. |
| 개발원 직접 UAT | no | not_run | - | 사용자 요청으로 생략. 합성 검사를 사람 평가로 표시하지 않음. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **warning**: 심화 과목 인정·배분, 면제자의 수강/PASS/학점 처리, 정확한PCCP 제출 학기는 기존 검수 보류. 개인 졸업 가능 인증은 제공하지 않음.
- **info**: 31승인 주제의 완전한 질문 문법이며 모든 임의 질문 이해·원문 무오류를 보증하지 않음. 자유형 이름 완전 탐지를 주장하지 않고 학생 입력은 모델·영구 저장에 보내지 않음.
- **info**: 초기 실패와 재검증은 로컬 실행 기록에 보존. 빠른 요청429와 부분 기록 확인은 제품 제한을 완화하지 않고 시험 절차의 기대값을 교정.
- **info**: 임시 외부 시연은 PC·인터넷·앱·기존 연결 유지가 필요하며 재시작 시 주소 변경 가능. 보고서에는 주소·접속값·학생자료를 넣지 않음.
- **info**: Git 기준점은 검증한 제품 커밋. 전체34개 제품 변경 파일은 reports/evaluations/20261001-natural-dialogue.json의 product_changed_files에 기록. 보고서와 전 페이지 시각 검증 기록은 별도 후속 문서 커밋으로 보존.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
