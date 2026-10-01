# 로그인 없는 외부 학사 시연 구축 결과

- 실행 ID: `20261001-public-demo`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0 / upstream79b82281d305c89181fbb216499d5f1e962c14ed`
- 브랜치: `codex/public-demo`
- 기준 커밋: `73da9192b296fe33f4086d9b283c68a64e2bea5b`
- 결과 커밋: `34afce23be313756f3694eecd233892ec66592b5`

## 요청

같은 네트워크가 아니어도 사용자 PC의 챗봇을 외부 링크로 접속하고, 로그인 없이 성적표 비교를 사용할 수 있도록 한다.

## 요약

로그인 없는 임시 HTTPS 시연을 구축하고 답변·부족분·근거 PDF·가상 성적표·종료를 실제 검증했다. 현재 백그라운드 실행 중이다. 처음 발견한 의존성·종료·시간 제한 결함을 교정했다. 학사 승인·보류는 유지하고, 성적표는 방문자별 요청 메모리에서 처리한다. 상세 측정은 reports/evaluations/20261001-public-demo.json에 기록했다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| added | `src/academic_assistant/public_demo.py` | 명시적 공개 모드, 허용 경로·Origin·Host 검사, 전역 자원 제한과 정제된 오류. |
| added | `scripts/operations/public_demo.py` | 공식 파일 해시, 별도 localhost 웹, 자기 자식·잠금·실행 신원·중계 시작과 종료. Windows 웹의 프로젝트 의존성만 사용. |
| added | `scripts/operations/public_demo.ps1` | 숨김 백그라운드 시작·상태·종료. 시작 중 종료 요청을 유한 재시도. |
| fixed | `src/academic_assistant/api.py` | 업로드 본문 수신 총30초, 실패 시 작업 슬롯 회수. |
| security | `src/academic_assistant/evidence_pdf.py` | 공개 모드의 원문 표시를 승인된 근거 전용 자식으로 분리. |
| added | `src/academic_assistant/evidence_process.py` | 20초·768MiB·16MiB 제한, 비밀 환경 상속 제거와 원문·페이지 검증. |
| security | `src/academic_assistant/transcript_process.py` | 공개 PDF 인식의 메모리·시간·출력·자식 정리와 최소 환경. 비공개 경로 보존. |
| changed | `src/academic_assistant/web/app.js` | 외부 모드에서 피드백 저장 숨김·차단, 기존 비공개 동작 유지. |
| changed | `src/academic_assistant/web/index.html` | 로그인 없는 공개 시연과 Cloudflare·호스팅 PC 처리 안내. |
| fixed | `src/academic_assistant/web/transcript.js` | 자료 변경·지우기·탭 종료 시 요청 취소와 오래된 결과 무효화, 탭별 자료 분리. |
| added | `tests/test_public_demo.py` | 공개 경계·시간 제한·늦은 작업 슬롯 유지·안전 거절 회귀. |
| added | `tests/test_public_demo_integration.py` | 실제 FastAPI와 공개 경계의 학사·개인정보·동시 작업 통합 회귀. |
| added | `tests/test_public_demo_launcher.py` | 실행 신원·준비 시간·종료·소유권·Windows 실제 의존성 import 회귀38개. |
| added | `tests/test_public_demo_transport.py` | 실제 소유 웹 자식·TCP 답변·개인정보 경계·타 리스너 보존·정상 종료2개. |
| added | `tests/test_public_resource_bounds.py` | 본문·메모리·출력·환경·PDF·자식 정리 경계 검사. |
| changed | `tests/test_transcript_allocation.py` | 실제 AbortController를 반영한 기존 브라우저 검사 환경 보완. |
| changed | `tests/transcript_web_ordering.cjs` | 이전 응답 순서 회귀의 취소 환경 유지. |
| added | `tests/web/transcript_lifecycle_test.js` | 지우기·교체·확인 취소·탭 분리10개 회귀 묶음. |
| changed | `.github/workflows/harness-ci.yml` | 성적표 취소·분리 Node 회귀를 전체 CI에 추가. |
| changed | `pyproject.toml` | 프로토타입 버전0.8.0. |
| documentation | `README.md` | 외부 시연 사용법 연결. |
| documentation | `THIRD_PARTY_NOTICES.md` | Cloudflare 도구의 출처·라이선스·고정 버전 안내. |
| documentation | `docs/harness/README.md` | 익명 외부 시연 설계 연결. |
| documentation | `docs/harness/decisions/0020-anonymous-pc-hosted-demo.md` | 익명 시연의 범위·권한·개인정보·운영 제한 ADR. |
| documentation | `docs/harness/decisions/README.md` | ADR 색인 갱신. |
| documentation | `docs/operations/public-demo.md` | 시작·주소 확인·종료·제한·문제 발생 시 확인 절차. |
| documentation | `docs/operations/runtime-stability.md` | 비공개 감독과 외부 시연의 경계 구분. |
| documentation | `CHANGELOG.md` | 추가·수정·보안·실제 실패 교정과 검증 결과 기록. |
| added | `reports/evaluations/20261001-public-demo.json` | 정제된 실제 HTTPS·브라우저·종료·독립 QA·CI 결과와 한계. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| harness_worker | resource-bounds/scrub/bootstrap | completed | PDF 경계·최소 환경·Windows 의존성 교정. |
| harness_worker | browser-lifecycle | completed | 자료 변경·취소·오래된 결과 제거·탭 분리·공개 처리 고지 구현. |
| harness_worker | launcher | completed | 소유 실행·종료·신원 잠금·총 준비 시간·시작 중 종료 검사. |
| harness_reviewer | review-v5 | completed | 16입력 일치, 정적 중대 발견 없음. 실행 검사는 안 함. |
| harness_qa | transport-qa-v1 | completed | TCP2개·영향87개·Node10묶음 통과.11개 입력 지문 전후 일치. |
| main_orchestrator | integration/publication | completed | 공용 경계·문서 통합, 실제 외부 접속·종료·화면 검증과 정제 보고. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| Windows 네이티브 웹 연결 | yes | passed | .venv/Scripts/python.exe -m unittest tests.test_public_demo_transport -v | 2개0.953초. 실제 TCP·130학점·종료·타 리스너 보존. |
| 독립 영향 구간 회귀 | yes | passed | .venv/Scripts/python.exe -m unittest tests.test_public_demo tests.test_public_resource_bounds tests.test_public_demo_launcher tests.test_public_demo_integration -v | 87개14.244초. 실제 import·종료·API·개인정보·시간·환경. |
| 성적표 화면 수명 | yes | passed | node tests/web/transcript_lifecycle_test.js | 10개 묶음. 취소·오래된 출력·지우기·교체·탭 분리. |
| 최종 독립 코드 검토 | yes | passed | - | review-v5의16입력 일치. 의존성·종료·슬롯·환경 중대 발견 없음. |
| 하네스 실행 완료 장부 | yes | passed | .venv/Scripts/python.exe .agents/skills/harness/scripts/validate.py --project . --run 20261001-public-demo-transport-qa-v1 --complete | bootstrap-v1·review-v5·transport-qa-v1 각각0오류. 실제 완료·유휴 기록. |
| 실제 외부 HTTPS | yes | passed | - | 15항목씩2회. 실제 그래프·130기준·30부족·범위 거절·exact PDF577·PNG·가상6학점. 추가 추론0회. |
| 실제 소유 종료 | yes | passed | ./scripts/operations/public_demo.ps1 -Stop | 자기 웹·중계·리스너 종료, 이전 링크530. 비공개 연결 보존. 최종 시연 재시작 유지. |
| 외부 화면 검증 | yes | passed | - | 130기준·논문 인용·가상6학점→124부족·남은 필수과목·보류·지우기·새 탭 분리. PDF577 빨간 밑줄 시각 확인. |
| GitHub Linux 전체 CI | yes | passed | gh run view 36815449365 --log | 34afce2 success.522개51.064초(6플랫폼skip).265학사·30사용성·15합성·계약·하네스·Node·패키지·Mermaid·보고 통과. Windows는 위 QA. |
| 다른 물리 PC 수동 접속 | no | not_run | - | 공개 HTTPS 경로는 검증. 별도 장소의 사람이 직접 사용한 것은 아님. 개발원 UAT는 요청대로 생략. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **info**: 임시 주소는 PC·인터넷·앱·근거 연결이 필요하며 재시작 시 변경된다. 가동 보장·자동 부팅·절전 변경은 없다. 링크 보유자 누구나 접속하며 Cloudflare로 중계한다. 사용자 원본 성적표의 기본 공개나 방문자 자료 공유는 없다.
- **warning**: 전공선택 인정 과목, 면제자의 수강·PASS·학점, 정확한 PCCP 제출 학기는 새 세션 질문으로 유지한다. 성적표 인식은 사용자 확인이 필요하며 최종 졸업 인증이 아니다. 실제 성적표·추가 GPU 추론·개발원 UAT는 이번 검증에 사용하지 않았다.
- **info**: 최초 NumPy 자동 로딩 실패를 Windows 웹 전용 -S와 명시적 의존성으로 교정했다. 시험 주소의 초기 DNS 지연 후 접속도 확인했다. 시스템 패키지·DNS 설정은 변경하지 않았다. 독립 검수의 종료·시간·환경 결함은 수정과 회귀로 추적한다.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
