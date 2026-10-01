# 프로토타입 0.7.0 - 대화·성적표·운영 고도화

- 실행 ID: `20261001-prototype-upgrade`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0 / upstream 79b82281d305c89181fbb216499d5f1e962c14ed`
- 브랜치: `codex/prototype-upgrade`
- 기준 커밋: `17e32e3d217169d39c1ad598f870fc6d72de3d86`
- 결과 커밋: `9cc28b82e3c048a3bf6357a76f8bd2346e0c7bf5`

## 요청

남은 질문 이해, 성적표 안내, 보류 학사조건 정리, 운영 안정화 작업을 모두 진행한다.

## 요약

한 질문 후속 문맥, 한국어 표현, 성적표 확인 항목·조건부 학점 요약, 자기 웹의 유한 복구를 구현했다. 독립 검수의 오류와 Linux 디코더 차이를 교정해 Windows 433개·독립 23개와 GitHub Linux CI를 통과했다. 실제 API 14건·그래프 복구·저부하 Gemma 6건을 확인했다. 승인 29규칙/3출처는 변경하지 않았다. 사용자 답변이 필요한 과목 인정·면제 학점·정확한 마감은 보류이며 최종 졸업 인증이 아니다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| changed | `CHANGELOG.md` | 구현·교정·실측·미응답 검수 범위를 기록. |
| changed | `README.md` | 대화·성적표·운영 안내 연결. |
| changed | `config/academic-intents.json` | 승인 의도의 한국어 표현 확장; 사실 추가 없음. |
| changed | `config/academic-registry-pins.json` | 변경된 의도 카탈로그 해시 고정. |
| added | `contracts/academic-chat-request.schema.json` | 선택 필드·모델/JSON Schema 동기화. |
| changed | `contracts/academic-chat-response.schema.json` | 선택 필드·모델/JSON Schema 동기화. |
| changed | `contracts/transcript-assessment-request.schema.json` | 선택 필드·모델/JSON Schema 동기화. |
| changed | `contracts/transcript-assessment-response.schema.json` | 선택 필드·모델/JSON Schema 동기화. |
| changed | `contracts/transcript-followup-request.schema.json` | 선택 필드·모델/JSON Schema 동기화. |
| changed | `contracts/transcript-followup-response.schema.json` | 선택 필드·모델/JSON Schema 동기화. |
| documentation | `docs/harness/contracts.md` | 설계·계약·운영 문서와 ADR 동기화. |
| documentation | `docs/harness/decisions/0019-bounded-dialogue-transcript-and-supervision.md` | 설계·계약·운영 문서와 ADR 동기화. |
| documentation | `docs/harness/decisions/README.md` | 설계·계약·운영 문서와 ADR 동기화. |
| documentation | `docs/harness/workflows.md` | 설계·계약·운영 문서와 ADR 동기화. |
| documentation | `docs/operations/conversation-prototype.md` | 설계·계약·운영 문서와 ADR 동기화. |
| documentation | `docs/operations/runtime-stability.md` | 설계·계약·운영 문서와 ADR 동기화. |
| documentation | `docs/operations/transcript-prototype.md` | 설계·계약·운영 문서와 ADR 동기화. |
| changed | `harness-manifest.yaml` | AcademicChatRequest 색인과 계약 연결. |
| changed | `pyproject.toml` | 프로토타입 버전0.7.0. |
| added | `reports/evaluations/20261001-live-upgrade.json` | 실제 합성 API14요청과 화면 확인; 모델 추론0. |
| added | `reports/evaluations/20261001-runtime-recovery.json` | 실제 자기 웹 장애·종료·그래프 실패 및 복구. |
| added | `reports/evaluations/20261001-topic-model-probe.json` | GPU0 Gemma 고정 주제6건; 정답률과 구분한 실측. |
| added | `reviews/academic/clarifications/20261001-prototype-followup-questions.md` | 새 권한·과목 인정·면제 처리·마감 질문; 미승인 상태 유지. |
| added | `scripts/operations/prototype_supervisor.py` | 자기 웹만 유한 감독. 진단 총 시간·UTF-8·최대64중첩을 명시하여 플랫폼과 무관하게 실패 폐쇄. |
| changed | `src/academic_assistant/api.py` | chat 입력만 선택적 이전 질문 계약으로 확장. |
| changed | `src/academic_assistant/core.py` | 표현 변형과 전체 질문 문법 검증; 미지원 추가 절 거절. |
| changed | `src/academic_assistant/grounded_chat.py` | 현재 입력으로 재검증하는 한 질문 문맥과 최대3개 확인 후보. |
| changed | `src/academic_assistant/models.py` | 대화 요청·확인 후보 모델 추가. |
| changed | `src/academic_assistant/transcript_assessment.py` | 인정 보류·중복·조건부 학점 요약과 전체 후속 질문 검증. |
| changed | `src/academic_assistant/transcript_models.py` | 확인 항목·행 플래그·학점 요약 선택 필드. |
| changed | `src/academic_assistant/web/app.js` | 문맥 초기화·요청 순서·보완 요청 경계. |
| changed | `src/academic_assistant/web/index.html` | 질문 확인 후보 안내 문구. |
| changed | `src/academic_assistant/web/transcript.js` | 행 보류 신호·검수 체크리스트·항목 이동·직접 질문. |
| added | `tests/test_academic_dialogue.py` | 대화·API·CLI·전체 질문과 브라우저 경계18검사. |
| added | `tests/test_prototype_supervisor.py` | 자기 프로세스·시간·소켓·JSON 30검사; 인코딩과64/65경계 포함. |
| added | `tests/test_transcript_allocation.py` | 인정 보류·요약·혼합 질문·범위15검사. |
| added | `tests/test_upgrade_independent_qa.py` | 제품 구현과 분리된 독립23검사. 깊이4000회귀 유지, 명시적 깊이/인코딩4검사 추가. |
| added | `reports/runs/20261001-prototype-upgrade.md` | 변경·검사·보류 범위를 정제한 사용자 보고서. |
| added | `reports/runs/20261001-prototype-upgrade.json` | CompletionReport의 구조화된 공개 원본. |
| added | `reports/pdf/20261001-prototype-upgrade.pdf` | 같은 구조화 원본으로 생성한 주요 변경 PDF. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| harness_worker | dialogue | completed | 한 질문 문맥·표현·웹 경계 구현. 추가 절을 무시하는 오류를 소유 워커가 교정. |
| harness_worker | transcript | completed | 행 확인 신호·체크리스트·조건부 학점. 부분 문자열 대신 전체 질문을 검증. |
| harness_worker | runtime | completed | 유한 감독·Windows자식 정리·총 시간 한도 구현과 두 오류 교정. 첫LinuxCI 실패 후 명시적UTF-8·64단계 계약을 별도 구현. |
| harness_reviewer | review-v4 | completed | 새 깊이/인코딩 계약을 포함한 읽기 전용 최종 재리뷰. 입력 지문 일치, actionable결함 없음; 실행검사는 QA/메인 담당. |
| harness_qa | qa-v4 | completed | 독립23·Windows전체433·9검증기·Node통과. 제품 수정 없음. 이전 실패와 첫UbuntuCI는 보존. |
| main_session | integration | completed | 공용 계약·문서·그래프·실제 API·소량 모델 측정·보고 통합. 학사 승인 응답은 추정하지 않음. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 전체 단위 테스트 | yes | passed | python -m unittest discover -s tests -p test_*.py | Windows433개50.364초, 독립23개13.372초 통과. 전체 질문·절대시간·중첩JSON 회귀 유지. 64/65·문자열/이스케이프·17인코딩 사례 추가. |
| 학사 근거·계약·평가 | yes | passed | - | 9검증기 통과:265답변·30사용성·15새 합성·5모델/95중첩근거·승인/질문 권한/미검증 격리/하네스 동기화. 학사 사실 변경 없음. |
| 화면 모듈 순서·안전 | yes | passed | node tests/transcript_web_ordering.cjs; node tests/test_evidence_ui.js | 성적표 최신 응답 회귀와 근거 UI9그룹 통과. |
| 독립 리뷰와 최신 실행 완료 | yes | passed | python .agents/skills/harness/scripts/validate.py --project . --run 20261001-upgrade-verification-v4 --complete | v4 실제 리뷰/QA 결과와 유휴를 확인하고 최신 지문으로 완료 검사 통과. 앞선 실패·오래된 입력을 성공으로 바꾸지 않음. |
| 실제 localhost API와 화면 | yes | passed | - | 합성14 API요청 통과.100학점 입력 후 이전 질문을 검증한30학점 부족 안내, 잘못된 범위·혼합 질문 거절, 성적표 검수 항목·재수강 표시·입력 변경 무효화 확인. 이 검사에서 모델 추론0. |
| 실제 Neo4j | yes | passed | - | 별도 빈 localhost 개발DB에75노드/124관계 적재하고 현재 승인 Registry와 그래프 SHA96713f3704f2d2f7b8b8850d613aa28b87ea4f3fb15964c234f9d9c2c520c19d 대조. 기존DB 삭제/덮어쓰기 없음. |
| 실제 감독·그래프 복구 | yes | passed | - | 점유 포트 거절, 자기 자식 장애 후1회 재시작·다시 준비,32초 후 listener 정리. 자기 그래프 중계만 중단하여503/같은 웹 생존, 복원 후 검증 준비. 최종 코드도 재시작·제한 종료 확인. |
| 실제 저부하 모델 측정 | yes | passed | - | 기존 DSW GPU0 Gemma에 주제 코드6개만 순차 요청. 관련4/null2/다른 의도0, 중앙0.774초·최대11.609초. 나루 보호 프로세스 보존과60초 유휴 후 GPU 메모리 해제 확인. 자연어 정답률이나 나루 무영향 보장이 아님. |
| 배포 자산·Mermaid·정제 문서 | yes | passed | - | 0.7.0 wheel과 웹/OCR 자산7개 검증,Mermaid2개 렌더. 문서/계약/ADR/공개 보고 검사·공백 오류 없음. |
| 실제 WSL JSON 경계 | yes | passed | - | Ubuntu-24.04 Python3.12.3에서 JSON5검사와 깊이2검사, 총7개 통과. 전체 GitHub LinuxCI와 구분한 실제 로컬 Linux 검사. |
| GitHub Actions | yes | passed | - | 구현기준9cc28b82의 Linux CI 성공: https://github.com/jeongiryang/kg-ontology-decision-framework-reboot/actions/runs/36806907933 . 433테스트/Windows전용2건생략, 나머지모든단계 통과. 최초fa469462 CI는 중첩JSON2오류로 실패했고 유지·교정함. |
| 보고서 PDF 시각 검사 | yes | passed | - | 4페이지 전체 렌더·시각검수: 한글·표·잘림·겹침·고아페이지·페이지번호 정상. |
| 개발원 직접 UAT | no | not_run | - | 사용자 요청으로 생략. Codex 합성 검사와 브라우저 확인을 사람 UAT로 부르지 않음. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **warning**: 새 권한·답변 없는3항목은 보류: 전선의 심화 인정 과목 범위, 면제자의 수강·성적·학점, 정확한PCCP 제출학기. 질문 문서만으로 승인하지 않았다.
- **info**: 기존 시범400점·I U/차년도II 제한·II PASS후 졸업작품 승인 유지. 고정600점·미래 기준·개인 면제·최종 졸업 판정은 만들지 않았다.
- **info**: localhost 개발DB·최대1시간 웹 감독이다. 운영DB 인증·재부팅 자동복구·다중 사용자 부하 검증은 아니다. 원본·학생 자료·접속 설정·원시 로그는Git 제외.
- **info**: 첫 private 실행은 가상환경, 첫UbuntuCI는 깊은JSON2오류로 실패. 환경과 명시적 깊이 계약을 교정한 결과만 완료 근거로 사용했다. 실패·오래된 입력은 보존. Githead9cc28b82는 구현 기준이며 보고서 발행 커밋과 다르다.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
