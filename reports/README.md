# 보고서와 작업 재개 기록

이 디렉터리는 정제 완료 보고서와 미완료 작업의 재개 기록을 구분해 관리한다. 원시 에이전트 대화와 로컬 실행 로그는 포함하지 않는다.

## 현재 작업과 남은 일

[2026-10-01 발표용 수정본·남은 작업·재개 기록](presentation-handoff-20261001.md)을 먼저 읽는다.
실제 Gemma/Neo4j/과목 조회/가상 PDF/근거 표시의 관찰, 성능 설명의 한계,
복합 질문의 미처리 요구, 발표 후 검사와 재개 순서, 서버 링크, 기록 위치와 파일 지문을 모았다.
**새 작업본의 검증은 실행했으나 필수 실패가 남아 제품은 미완료다.** 누적5회 수정 뒤 자동으로 다음 제품 수정을 숨겨 진행하지 않는다.

## 최신 구현·검증 결과 (미완료)

- [변경·검사·남은6결함](runs/20261001-purpose-dialogue.md), [구조화 보고](runs/20261001-purpose-dialogue.json), [동일 입력4페이지 PDF](pdf/20261001-purpose-dialogue.pdf).
- [실제 질문66발화의 통과/실패/대기](evaluations/20261001-purpose-dialogue.md), [실제 답변·기대값·판정·변경61파일](evaluations/20261001-purpose-dialogue.json).
- Windows780개:774통과·6실패. 실제 모델 평가:36통과·29실패·1대기(수정용25/10/1, 미사용 별도11/19/0). 실패에는 요청/문맥/범위 차이와 수집기·판정기 혼재3건도 포함하며, 모두 틀린 학사 사실로 해석하지 않는다.
- [Draft PR12](https://github.com/jeongiryang/kg-ontology-decision-framework-reboot/pull/12)와 [구현 커밋 CI 실패](https://github.com/jeongiryang/kg-ontology-decision-framework-reboot/actions/runs/36879345919). 병합/새 공개 승격은 차단하며 기존 서버를 유지한다. 보고서 발행 성공은 제품 완료가 아니다.

## 기존 완료 보고서

이전 기준점: [2026-09-30 근거·그래프 검증 보고서](runs/20260930-runtime-pilot-v2.md)
([JSON](runs/20260930-runtime-pilot-v2.json), [PDF](pdf/20260930-runtime-pilot-v2.pdf)).
이 보고서의 당시 코드·실 Neo4j·독립 QA 검사는 통과했지만 DSW 실 추론은 미실행이었다.
이후 실제 Gemma 연결 관찰은 위 10월 1일 재개 기록에 있으며, 이전 보고서를 현재 코드의
검증 결과로 사용하지 않는다. 실제 사람 UAT는 사용자가 이번 단계에서 제외했다.
보류 운영관계도 완료로 기록하지 않는다.

- `runs/<run-id>.json`: 검증·정제된 `CompletionReport`
- `runs/<run-id>.md`: 같은 입력에서 생성한 사람이 읽는 보고서
- `pdf/<run-id>.pdf`: 주요 작업에만 생성하는 PDF
- `presentation-handoff-20261001.md`: 미완료 작업과 실제 관찰을 담은 재개 문서(CompletionReport 아님)

보고서는 `python scripts/reporting/generate_report.py --project . --input <completion-report.json>`으로 생성한다. 생성 정책과 검증 절차는 [completion-reporting 스킬](../.agents/skills/completion-reporting/SKILL.md)을 따른다.
