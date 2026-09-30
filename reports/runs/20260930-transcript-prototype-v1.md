# 2026학번 성적표 기반 부분 이수 비교 프로토타입

- 실행 ID: `20260930-transcript-prototype-v1`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0 / upstream 79b82281d305c89181fbb216499d5f1e962c14ed`
- 브랜치: `codex/transcript-graduation-prototype`
- 기준 커밋: `b23eccbff090013b8b3290deb99f5cce7e178941`
- 결과 커밋: `9a9a1de7fb38f924f6a90535b213f401ea34c753`

## 요청

남은 초기 프로토타입 작업을 진행하고 정제 결과를 보고한다. 별도 연구실 GPU 사용을 목표로 하며 개발원 직접 평가는 생략한다.

## 요약

성적표 PDF 로컬 추출·인식값 수정·직접 입력, 승인 규칙에 따른 부족 학점·전공필수/0학점 과목 비교와 후속 질문을 구현했다. 실제 전용 Neo4j 조회와 웹 동작을 확인했다. LLM·전체 졸업 인증은 완료로 주장하지 않는다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| added | `src/academic_assistant/transcript_extract.py` | 페이지 선택·PDFium 텍스트/로컬 OCR·부분 인식, 불확실한 값은 직접 확인. |
| added | `src/academic_assistant/ocr_windows.ps1` | 메모리 PNG 입력과 제한된 한국어 OCR 결과만 반환하는 번들 helper. |
| security | `src/academic_assistant/transcript_process.py` | 120초 전체 자식 처리와 Windows job/POSIX 전용 그룹 종료,3초 정리 대기. |
| added | `src/academic_assistant/transcript_models.py` | 학생 식별자 없는 확인 입력·부분 평가·정책/학생 근거·후속 질문 모델. |
| added | `src/academic_assistant/transcript_assessment.py` | 승인 지식과 정확한 과목 식별로 학점·필수과목 비교, 불명확한 배분 보류. |
| changed | `src/academic_assistant/api.py` | extract/assess/chat API, 메모리 업로드·동시1건·일반 오류·실패 폐쇄. |
| changed | `src/academic_assistant/web/index.html` | PDF·직접 입력·가상 예제·이수유형·확인·기록 지우기 영역. |
| added | `src/academic_assistant/web/transcript.js` | 과목 수정·결과·근거·후속 질문, 변경 무효화와 최신 요청만 표시. |
| changed | `src/academic_assistant/web/app.css` | 성적표 표·결과 항목 스타일과 좁은 화면 가로 스크롤. |
| added | `contracts/transcript-extraction.schema.json` | 확인 전 부분 인식 계약1.0. |
| added | `contracts/transcript-assessment-request.schema.json` | 엄격한 확인 boolean·명시 이수유형·비식별 과목 입력 계약1.0. |
| added | `contracts/transcript-assessment-response.schema.json` | 부분 결과·근거·정책 참고 계약1.0; 최종 졸업 인증 false. |
| added | `contracts/transcript-followup-request.schema.json` | 동일 확인 기록을 사용하는 후속 질문 계약1.0. |
| added | `contracts/transcript-followup-response.schema.json` | 상태·제한 답변·선택 비교 항목 계약1.0. |
| added | `scripts/validation/validate_transcript_prototype.py` | 모델/계약 동일성과 모든 학생·정책 packet의 원래 근거 계약 검증. |
| added | `tests/test_transcript_extract.py` | 원본 없이 합성 PDF·과목 좌표·부분 인식·범위 경계. |
| added | `tests/test_transcript_assessment.py` | 중복·알려진 학점/구분·0학점·상한·범위·보류·API·근거 회귀. |
| added | `tests/test_transcript_process.py` | 시간 제한·오류·실제 소유 자손 종료와 무관한 프로세스 보존. |
| added | `tests/transcript_web_ordering.cjs` | 실제 웹 핸들러 역순 응답·삭제 무효화·정책 표시·영역 단위. |
| changed | `.github/workflows/harness-ci.yml` | 신규 계약·웹 순서·번들 OCR/웹 자산 검증과 Linux 회귀. |
| changed | `pyproject.toml` | 버전0.5.0·고정 pypdfium2 의존성과 자산 패키징. |
| documentation | `THIRD_PARTY_NOTICES.md` | pypdfium2/PDFium 라이선스와 출처 고지. |
| changed | `harness-manifest.yaml` | 신규 계약5개와 성적표 검증 명령 등록. |
| documentation | `docs/harness/contracts.md` | 성적표 계약·정확한 확인·부분 판정·정책 packet 설명. |
| documentation | `docs/harness/workflows.md` | 메모리 추출→사용자 확인→부분 비교→후속 질문 흐름. |
| documentation | `docs/harness/decisions/0015-private-transcript-partial-assessment.md` | 개인정보·확인·부분 범위·실행 차단 설계 결정. |
| documentation | `docs/harness/decisions/README.md` | ADR0015 색인. |
| documentation | `docs/operations/transcript-prototype.md` | 실제 이용법·지원 조건·불확실성·API·종료 경계. |
| documentation | `docs/operations/human-uat.md` | 이번 단계 사용자 지시로 사람 평가 생략 표시. |
| documentation | `reports/source-audits/2026-transcript-allocation-boundaries.md` | 공식 PDF에서 확인한 학점·과목 경계와 미확정 개인 배분. |
| documentation | `README.md` | 새 기능 사용법·부분 인식·DSW 차단·최종 인증 미지원 안내. |
| documentation | `CHANGELOG.md` | 추가·수정·보안·제한·미실행 기록. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| harness_worker | transcript-extractor-and-repairs | completed | 로컬 추출기와 두 차례 제한된 제품 수정을 수행. 원시 성적표는 공개 산출물에서 제외. |
| academic_source_auditor | assessment-source-audit | completed | 공식 PDF 기준 과목·학점 배분 경계를 읽기 전용 감사. 출처·규칙 승인 상태는 변경하지 않음. |
| harness_architect (read-only reviewer fallback) | transcript-review | completed | 독립 검토에서 초기 경계 오류와 하위 작업 종료 결함을 발견; 수정 후 현재 지문과 검증 근거를 재확인. |
| harness_explorer (read-only execution QA fallback) | transcript-qa | completed | 실제 테스트·합성 API·기존 근거 계약·웹 응답 순서·소유 프로세스 정리를 독립 검증. |
| main_orchestrator | integration-publication | completed | 공유 API·모델·계약·CI·문서 통합, 실제 그래프와 웹 검증, 정제 보고. 학생 개인 졸업 결과는 생성하지 않음. |
| main_orchestrator (DSW safety preflight) | dsw-model-runtime | blocked | 나루 GPU1 제외, 0/2/3의 빈 계산 자원 확인. 서버 공간 정책 미달과 NAS 접근 오류로 모델 시작·다운로드·GPU 할당 미실행. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 전체 단위 테스트 | yes | passed | .venv/Scripts/python.exe -m unittest discover -s tests -q | 최종 통합본293개 통과. PDF·평가·개인정보·프로세스 회귀 포함. 독립QA에서도 재실행. |
| 공통 계약·웹 순서 | yes | passed | scripts/validation/validate_transcript_prototype.py; node tests/transcript_web_ordering.cjs | 5개 모델/스키마 일치·95개 원래 계약 중첩 근거 패킷·실제JS의 늦은 응답/삭제 무효화/영역 단위 통과. |
| 실제 소유 작업 종료 | yes | passed | - | Windows 실프로세스 timeout·부모 먼저 종료 오류 모두 후손 종료 및 무관한 sentinel 생존 확인. 정리 대기3초. 독립 리뷰의 파일없는 재현도 통과. |
| 패키징·하네스·문서 | yes | passed | - | 0.5.0 최종 wheel에서 웹·OCR·프로세스 helper 포함 확인. manifest/schema/정적 동기화·공개 보고 검사·Mermaid2블록 렌더 통과. |
| 독립 리뷰·QA와 실행 장부 | yes | passed | .agents/skills/harness/scripts/validate.py --project . --run 20260930-transcript-verification-v3 --complete | 실제 독립35리뷰테스트/293QA테스트/37API검사/258canonicalpacket/96정책빈학생사실/실제후손정리통과. v3필수결과·최신지문·유휴상태와 완료검사통과. |
| GitHub Linux CI | yes | passed | gh run view 36727873991 | source commit9a9a1de 대상 validate 성공. Linux전체단위·실제 POSIX후손 종료·계약·패키징·학사평가·문서/보고/그림 검사 성공. https://github.com/jeongiryang/kg-ontology-decision-framework-reboot/actions/runs/36727873991 |
| 기존 학사 지식·평가 | yes | passed | scripts/validation/validate_academic_knowledge.py; validate_academic_clarifications.py; validate_academic_research.py; validate_academic_answer_engine.py; validate_academic_usability.py; validate_reference_patterns.py | 승인 지식·세션 권한·미검증 분리 통과. 기존 265건, 사용성 합성30건, 참고 질문 기반 합성15건 통과. |
| 실제 그래프와 웹 | yes | passed | - | 전용 로컬 Neo4j68노드·115관계가 승인 Registry와 정확 일치. 합성 예제6학점·130기준124학점 부족·0학점 상담 이수·남은 필수과목·후속 답변·수정 무효화·기록 삭제를 실제 브라우저에서 확인. |
| 비공개 PDF 형식 probe | yes | passed | - | 허가된 Gmail 지원서의 성적표 페이지만 로컬 형식 확인. 지원 입학년도와 달라 개인 학사 계산에 사용하지 않음. 인식은 부분 결과이며 직접 확인·수정 필요. |
| DSW 읽기 전용 사전점검 | yes | passed | - | 계정·가동 상태·GPU·디스크 실제 확인. 나루 GPU1 변경 없음; 비점유 GPU0/2/3 확인. 3.6TB 파일시스템의15GB 여유(1% 미만), NAS 접근 오류 관찰. |
| DSW 실제 모델 실행 | yes | not_run | - | dsw-server-operations의 최소20% 디스크 여유 게이트 미충족. 모델 다운로드·추론·상주 서비스·GPU 할당을 시작하지 않음. |
| 개발원 직접 평가 | no | not_run | - | 사용자 요청으로 생략. 다른 교수님 챗봇의 참고 UAT 기록을 현재 제품 검증 통과로 사용하지 않음. |
| PDF 전 페이지 시각 검수 | yes | passed | - | 4개 A4페이지를 모두105dpi PNG로 렌더링·시각검수했다. 한글·표 경계·반복 머리글·페이지번호1~4·여백·잘림·겹침·고아페이지 이상 없음. 최종 재생성 후 다시 확인. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **warning**: 프로토타입 전체 완료가 아니다. 연구실 LLM 실제 실행은 서버 디스크 정책과 NAS 접근 오류 해결 전까지 차단.
- **warning**: Gmail에서 찾은 성적표는 2026학번 자료가 아니므로 실제 2026 개인 졸업 판정 자료로 사용하지 않았다. 원본·학생 기록은 Git/공개 보고서/LLM에 포함하지 않는다.
- **warning**: OCR은 부분 인식이며 사용자 수정·확인이 필요하다. 심화전공 과목별 배분, 기초교양 지정 영역·개인 면제·대체·소급 등은 검수 필요. 최종 졸업 인증을 선언하지 않는다.
- **info**: 2026년 PCCP400 시범 기준을 미래 학번 조건으로 고정하지 않음. II PASS→졸업작품·공모전 자동 대체 등 보류 관계는 유지.
- **info**: 초기 실패 검증 실행은 보존하고 최신 통합 검증으로 해결을 확인한다. 원시 대화·OCR 조각을 공개 로그로 내보내지 않는다.

## 공개 정보

- 주요 작업: `yes`
- PDF 필요: `yes`
