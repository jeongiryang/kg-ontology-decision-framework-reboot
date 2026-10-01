# 실제 Gemma 안내와 최소 학사조교 프로토타입

- 실행 ID: `20261001-gemma-final`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0 / upstream79b82281d305c89181fbb216499d5f1e962c14ed / prototype0.9.0`
- 브랜치: `codex/public-demo`
- 기준 커밋: `3e568b63ac0fb619c82f657418594bcad582f889`
- 결과 커밋: `f0b0e2eef67cfcbdaeebf4ef492073ce9b521a4b`

## 요청

실제 연구실 LLM까지 연결하고 불필요한 화면·실행 구조를 줄여 초기 프로토타입을 완성한다.

## 요약

질문 중심 화면·단일 실행 진입점·검증된 Gemma 안내를 통합했다. 실제 안내2건, 외부 HTTPS, Neo4j 근거, 학점·가상 성적표 비교와 PDF577쪽 밑줄을 확인했다. 승인29규칙·3출처·기존3보류는 변경하지 않았다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| added | `src/academic_assistant/grounded_generation.py` | 승인 공용 주장·해시·전체 문장 보존 검증. |
| changed | `src/academic_assistant/llm.py` | 실제 생성·제한 캐시·공유 예산·절대 전송 기한. |
| changed | `src/academic_assistant/grounded_chat.py` | 검증된 문장만 선택적으로 추가; 코어 응답 불변. |
| changed | `src/academic_assistant/models.py` | 명시 생성 요청과 선택 출력 필드. |
| changed | `src/academic_assistant/api.py` | 생성 설정·런타임 상태 구분. |
| changed | `contracts/academic-chat-request.schema.json` | 기본 비활성 생성 요청 계약. |
| changed | `contracts/academic-chat-response.schema.json` | 지원·주장 범위·생성/캐시 조건 검사. |
| added | `demo.ps1` | 기존 소유 실행기를 쓰는 시작·상태·종료 진입점. |
| changed | `scripts/operations/start_prototype.py` | 생성 설정만 명시 허용·검증. |
| fixed | `scripts/operations/ssh_loopback_bridge.py` | 실제 응답 끝 reset 교정·소유 자식/스레드 정리. |
| changed | `src/academic_assistant/web/index.html` | 질문 우선·성적표/기술 상세 접기. |
| changed | `src/academic_assistant/web/app.css` | 간결한 질문 중심 배치. |
| changed | `src/academic_assistant/web/app.js` | 검증한 모델 문장·재사용·실패를 사실대로 표시. |
| changed | `pyproject.toml` | 프로토타입0.9.0. |
| changed | `harness-manifest.yaml` | 생성·개인정보·예산 경계 색인 동기화. |
| documentation | `docs/harness/contracts.md` | 기존 소비자 호환과 생성 검증 경계. |
| added | `docs/harness/decisions/0021-verified-gemma-claim-generation.md` | 판정/계산과 모델 안내를 분리한 설계 결정. |
| added | `docs/operations/minimal-prototype.md` | 사용·최소 구성·실제 모델 역할과 제한. |
| documentation | `docs/operations/kg-llm-prototype.md` | 의도 제안에서 승인 안내 생성으로 구분. |
| documentation | `docs/operations/public-demo.md` | 한 진입점·생성 상태·학생자료 비전송. |
| documentation | `README.md` | 빠른 시작과 검증된 안내 설명. |
| documentation | `CHANGELOG.md` | 실제 추가·교정·한계·검사 기록. |
| changed | `.github/workflows/harness-ci.yml` | 실제 화면 소비자 회귀 검사 추가. |
| fixed | `tests/test_academic_dialogue.py` | 채팅 전용 필드를 제거한 기존 API 동등성 검사. |
| added | `tests/test_grounded_generation.py` | 의미·개인정보·캐시·실제 전송 기한33검사. |
| added | `tests/test_gemma_final_qa.py` | 독립 의미/기본 계약/실패 폐쇄16검사. |
| added | `tests/test_ssh_loopback_bridge.py` | 실제 EOF·수명·부분 쓰기8검사. |
| added | `tests/web/gemma_final_ui_test.cjs` | 실제 app.js 소비자9개 그룹. |
| added | `reports/evaluations/20261001-gemma-final.json` | 실제3시도·2검증·접속/자원 측정; 원문·주소 제외. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| harness_worker | 20261001-llm-final-implementation-v3/backend | completed | 생성·검증·공유 예산과 절대 기한 구현. |
| harness_worker | 20261001-llm-final-relay-v1/relay | completed | 실제 연결 종료 오류 재현·교정; 제품 경계 유지. |
| harness_reviewer | 20261001-llm-final-review-v2/review | completed | 기한 지적 후 수정 재검토; 추가 지적 없음. |
| harness_reviewer | 20261001-llm-final-relay-verification-v1/review | completed | 연결·정리·사용법 읽기 전용 검토 통과. |
| harness_qa | 20261001-llm-final-qa-v2/qa | completed | 학사 의미·개인정보·기본 호환·화면 소비자 독립 검사. |
| harness_qa | 20261001-llm-final-relay-verification-v1/qa | completed | 8+33+16개 검사와 화면9그룹 재실행 통과. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| Windows 전체 회귀 | yes | passed | .venv/Scripts/python.exe -m unittest discover -s tests -p test_*.py | 수정 통합본580개, 생략0, 72.995초 통과. |
| 독립 검토와 QA | yes | passed | - | 기한 지적 수정 후 재검토. 최신57개 및 화면9그룹, 실제 ID/입력/수명 확인. |
| 하네스 동기화 | yes | passed | .venv/Scripts/python.exe scripts/validation/validate_harness_sync.py --project . | 설정/스킬/계약/문서 일치, 최신2개 실행 완료 검사 통과. |
| 학사 근거/검수/격리 | yes | passed | - | 지식/권한/조사 격리 통과. 승인29규칙, 3출처, 3보류 불변. |
| 코어/성적표 계약 | yes | passed | - | 고정265사례, 성적표5계약/모델 및 중첩 근거95건 통과. |
| 실제 Gemma 안내 | yes | passed | - | 초기 unavailable1건 교정. 합성 공용 안내2건, 전체 주의문구 유지. 12.953초/14.000초. |
| 캐시/점유/판정 보존 | yes | passed | - | 캐시/busy 복귀 및 코어 불변. 가상100학점에서 부족30은 로컬 계산. |
| 범위/보류 거절 | yes | passed | - | 2025학번과 면제 학점 거절 유지, 모델 문장 없음. |
| 실제 외부 HTTPS/화면 | yes | passed | - | 외부 응답/화면 확인. 가상6학점, 남은 필수7과목, 0학점 상담 및 지우기 확인. |
| Neo4j/원본 위치 | yes | passed | - | 그래프 검증, PDF577/인쇄569쪽 exact 밑줄과 PNG/PDF 사본 확인. |
| GPU 유휴/나루 보존 | yes | passed | - | 05:40:40UTC GPU0 16MiB/0%, 활성 모델0. 나루GPU1 18510MiB, 기존 연결 보존. |
| 문서/Mermaid | yes | passed | - | 링크/계약 통과. Mermaid11.12.0 실제2블록 렌더. |
| 제품 GitHub CI | yes | passed | - | 실행36821029175 success. Ubuntu580개, 플랫폼별6개 생략, 54.779초. 모든 단계 통과. |
| 개발원 직접 UAT | no | not_run | - | 사용자 요청으로 생략. 합성 검사를 사람 평가로 표시하지 않음. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **warning**: 심화 인정·배분 과목 범위, 면제자의 수강/학점 처리, 정확한 PCCP 제출 학기는 기존 검수 보류. 자동 확정하지 않음.
- **info**: 학생 이수 비교는 승인 요건의 부분 비교이며 최종 졸업 인증이 아님. 제한된 문장 생성으로 자유 대화·모든 근거의 절대 정확성을 보증하지 않음.
- **info**: 임시 외부 시연은 PC·인터넷·앱·근거/모델 연결이 유지되는 동안만 제공. 재시작 시 주소 변경 가능; 공개 보고서에 주소·접속값·학생자료를 넣지 않음.
- **info**: 보고서 Git 기준점은 검증한 제품 커밋. 보고서 산출물은 별도 후속 커밋이며 제품을 변경하지 않음.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
