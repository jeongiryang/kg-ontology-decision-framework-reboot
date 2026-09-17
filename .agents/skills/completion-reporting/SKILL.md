---
name: completion-reporting
description: "완료된 하네스 작업의 실제 변경·검사 근거를 CompletionReport로 검증하고 공개용 Markdown·JSON·PDF 보고서로 정제한다. 작업 완료 보고, 변경이력 작성, 마일스톤 PDF 생성, 기존 보고서 재생성에 사용한다. 원시 대화·숨은 추론·학생정보를 보존하는 로그 수집에는 사용하지 않는다."
---

# 완료 보고서 생성

작업 결과를 사용자가 확인할 수 있는 공개 산출물로 변환한다. 공개 보고서는 원시 실행 로그가 아니며, 실제 변경·명령·검사 근거만 담는다.

## 입력과 완료 기준

1. `CompletionReport` JSON의 `schema_version`, `run_id`, `title`, `summary`, `changes`, `checks`, `issues`, `publication`을 확인한다.
2. 저장소의 `contracts/completion-report.schema.json`과 입력을 검증한다.
3. 실제 실행하지 않은 검사는 `not_run`으로 유지하고, 실패를 성공으로 바꾸지 않는다.
4. 공개 가능한 정제 JSON과 Markdown을 항상 생성한다.
5. `publication.pdf_required` 또는 `publication.major`가 참이면 같은 내용의 PDF를 생성하고 렌더링 검사한다.

## 공개 안전 정책

- 토큰·비밀번호·개인키 등 비밀값, 학번과 학생 식별 필드는 자동 추측으로 가리지 않는다. 보고서 생성을 실패시켜 작성자가 원본 입력을 고치게 한다.
- Windows 드라이브 경로, UNC 경로, Unix 절대경로는 서술·명령·근거에서 `<redacted-path>`로 바꾼다.
- `changes[].path`는 저장소 상대경로만 허용한다. 절대경로나 `..` 탈출은 계약 오류다.
- 원시 프롬프트, 시스템 지시, 숨은 추론을 뜻하는 필드는 허용하지 않는다.
- 원본 PDF/HWP/XLSX, 학생정보, SSH 접속정보, `.env`, `.local`, 원시 실행 로그를 공개 보고서에 넣지 않는다.

## 절차

1. 메인 오케스트레이터에게 승인된 작업 결과, Git 기준점, 검사 근거를 요청한다.
2. 입력 JSON에는 관찰된 사실만 기록한다. 명령이 없었던 읽기 전용 검토에 가짜 명령을 만들지 않는다.
3. 프로젝트 루트에서 다음 명령을 실행한다.

   ```bash
   python scripts/reporting/generate_report.py \
     --project . \
     --input path/to/completion-report.json
   ```

4. 생성된 `reports/runs/<run-id>.json`과 `.md`에서 변경, 검사, 이슈가 입력과 일치하는지 확인한다.
5. PDF가 생성되면 PDF용 검사 절차로 모든 페이지를 PNG로 렌더링한다. 한글 깨짐, 잘림, 표 겹침, 페이지 번호를 눈으로 확인한다. 렌더링 도구가 없으면 `not_run`으로 기록하며 주요 작업 보고를 완료로 주장하지 않는다.
6. 공개 보고서 묶음을 검사한다.

   ```bash
   python scripts/reporting/ci_checks.py --project .
   ```

7. 최종 응답에서 Markdown, JSON, 필요한 PDF, `CHANGELOG.md`, 커밋 또는 PR을 링크한다. 없는 산출물을 약속하지 않는다.

## 생성 결과

- `reports/runs/<run-id>.json`: 정제된 구조화 보고서
- `reports/runs/<run-id>.md`: 사람이 읽는 완료 보고서
- `reports/pdf/<run-id>.pdf`: 주요 작업에만 생성하는 결정적 PDF

생성기는 같은 정제 입력에 대해 같은 JSON·Markdown·PDF 바이트를 만든다. 기존 파일은 원자적으로 교체하므로 입력 검증이 실패하면 부분 보고서를 남기지 않는다.

## 실패 처리

- 계약 오류, 공개 금지 데이터, 필수 PDF 의존성 누락은 종료 코드 1로 실패한다.
- 필수 검사의 `failed` 또는 `not_run`은 보고서에 사실대로 남는다. 보고서 생성 성공은 작업 검증 성공을 의미하지 않는다.
- PDF 렌더링 검사와 Git 커밋은 생성기의 책임 밖이다. 실제 수행 여부를 별도 검사 근거로 남긴다.

