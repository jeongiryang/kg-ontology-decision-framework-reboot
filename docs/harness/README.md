# 프로젝트 하네스 설계 문서

이 디렉터리는 **KG Ontology Decision Framework Reboot**의 Codex 하네스를 설명한다. 하네스는 작업을 작은 패킷으로 나누고, 필요한 전문 에이전트만 호출하며, 구현과 검증 결과를 재현 가능한 기록으로 남기는 프로젝트 운영 계층이다. 챗봇·Neo4j·문서 파서·모델 서빙 자체는 이 하네스의 범위가 아니다.

> 실행의 단일 기준은 `.codex/config.toml`, `.codex/agents/*.toml`, `.agents/skills/*/SKILL.md`와 JSON Schema다. `harness-manifest.yaml`은 그 구조화된 색인이고, 이 문서들은 사람이 이해하도록 설명한다. 서로 다르면 문서를 근거로 실행하지 말고 실제 설정과 스키마를 확인한 뒤 문서와 색인을 함께 고친다.

## 문서 지도

| 문서 | 내용 |
| --- | --- |
| [아키텍처](architecture.md) | 구성요소, fan-out/fan-in, 실행 시퀀스 |
| [에이전트와 권한](agents-and-permissions.md) | 역할, 입력·출력, 파일 소유권 |
| [워크플로](workflows.md) | 설치, 작업 실행, 실패·재개, 완료 절차 |
| [계약](contracts.md) | 여섯 JSON 계약과 예시 |
| [보고](reporting.md) | 원시 로그, 공개 보고서, PDF 검증 |
| [DSW 운영](dsw-operations.md) | GPU·디스크·프로세스 안전 계약 |
| [업스트림](upstream.md) | `codex-harness` 출처, 고정 버전, 갱신 절차 |
| [설계 결정](decisions/README.md) | 변경 이유와 대안이 담긴 ADR 색인 |

## 빠른 시작

1. 프로젝트 루트에서 `AGENTS.md`와 작업에 해당하는 `SKILL.md`를 읽는다.
2. 구조 검사는 `python .agents/skills/harness/scripts/validate.py --project .`로 실행한다. 이는 실제 서브에이전트 실행 성공을 증명하지 않는다.
3. 메인 세션은 `run.py init`으로 실행 장부와 작업별 `input.md`를 만들고, 준비된 작업만 실제 에이전트에 배정한다.
4. 첫 대기 에이전트에서 실제 ID가 반환된 것을 확인한 뒤에만 최대 3개까지 팬아웃한다.
5. 구현 후 독립 리뷰·QA를 수행하고 `run.py result`로 실제 검사 근거가 포함된 결과를 등록한다.
6. 완료 시 [보고 정책](reporting.md)에 따라 Markdown/JSON과 필요한 경우 PDF를 생성한다.

자세한 명령과 상태 전이는 [워크플로](workflows.md)를 따른다. 하네스를 변경하는 PR은 실제 설정·스키마, `harness-manifest.yaml`, 이 문서와 필요한 ADR을 같은 변경에서 동기화해야 한다.

