# 공개 완료 보고서

이 디렉터리는 하네스가 생성한 정제 완료 보고서를 코드와 함께 버전 관리한다. 원시 에이전트 대화와 로컬 실행 로그는 포함하지 않는다.

- `runs/<run-id>.json`: 검증·정제된 `CompletionReport`
- `runs/<run-id>.md`: 같은 입력에서 생성한 사람이 읽는 보고서
- `pdf/<run-id>.pdf`: 주요 작업에만 생성하는 PDF

보고서는 `python scripts/reporting/generate_report.py --project . --input <completion-report.json>`으로 생성한다. 생성 정책과 검증 절차는 [completion-reporting 스킬](../.agents/skills/completion-reporting/SKILL.md)을 따른다.

