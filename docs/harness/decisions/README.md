# 하네스 ADR 색인

ADR(Architecture Decision Record)은 이미 선택한 중요한 설계 결정과 맥락을 보존한다. 상태가 바뀌면 기존 문서를 지우지 않고 새 ADR에서 대체 관계를 기록한다.

| ADR | 결정 | 상태 |
| --- | --- | --- |
| [0001](0001-vendored-harness-installation.md) | WSL에서 고정 SHA의 하네스를 설치본으로 포함 | 채택 |
| [0002](0002-max-three-concurrent-agents.md) | 동시 서브에이전트 최대 3개 | 채택 |
| [0003](0003-exclude-source-documents.md) | 원문·학생정보·접속정보를 Git에서 제외 | 채택 |
| [0004](0004-pdf-completion-reports.md) | 중대 변경에 검증된 PDF 완료 보고서 생성 | 채택 |
| [0005](0005-contract-sync-ci.md) | 계약 동기화·meta-schema·Mermaid 렌더링을 CI에서 검증 | 채택 |
| [0006](0006-fail-closed-publication-and-deidentification.md) | 비식별 계약과 공개 보고서 쌍을 실패 폐쇄로 검증 | 채택 |
| [0007](0007-admission-cohort-and-human-review.md) | 교육과정은 입학연도로 적용하고 학사 관계를 사람 전수 검수 | 채택 |
| [0008](0008-portable-run-state-paths.md) | 실행 상태의 프로젝트 상대 경로를 POSIX 형식으로 정규화 | 채택 |
| [0009](0009-session-scoped-academic-clarification.md) | 사용자 학사 확인을 검수 세션과 명시적 응답 범위로 제한 | 채택 |
| [0010](0010-isolate-unverified-academic-practices.md) | 미확인 운영요건을 답변 지식에서 격리하고 공개 조사 이슈로 추적 | 채택 |
| [0011](0011-opt-in-local-feedback.md) | 비지원 질문은 명시적 동의로만 로컬 저장 | 일부 대체 |
| [0012](0012-kg-and-llm-advisory-boundary.md) | 승인 근거 그래프와 비권위 LLM 의도 제안의 경계 | 채택 |
| [0013](0013-no-raw-feedback-retention.md) | 동의한 피드백에도 질문 원문을 보존하지 않음 | 채택 |
| [0014](0014-exact-graph-runtime-and-evidence-lineage.md) | Neo4j 정확 대조·인용 정정 계보·선택적 LLM 호출 예산 | 채택 |
# 추가 결정

- [ADR0015: 비식별 성적표 부분 비교](0015-private-transcript-partial-assessment.md)
- [ADR0016: 해시가 연결된 원본 PDF 근거 표시](0016-hash-bound-pdf-evidence-preview.md)
- [ADR0017: DSW 고정 디스크 여유 비율 차단 제거](0017-remove-fixed-dsw-disk-threshold.md)
- [ADR0018: 현행 시범 운영정책과 비PDF 학과 확인](0018-current-trial-operational-confirmations.md)
- [ADR0019: 제한된 대화 문맥·성적표 검수 신호·로컬 감독](0019-bounded-dialogue-transcript-and-supervision.md)
