# 하네스 워크플로

## 최초 설치

Windows의 심볼릭 링크 제약을 피하기 위해 WSL에서 업스트림을 체크아웃하고 Windows 프로젝트 경로(`/mnt/c/...`)를 대상으로 설치한다.

```bash
git clone https://github.com/jeongiryang/codex-harness.git
cd codex-harness
git checkout 79b82281d305c89181fbb216499d5f1e962c14ed
python3 scripts/install.py --target /mnt/c/path/to/kg-ontology-decision-framework-reboot --dry-run
python3 scripts/install.py --target /mnt/c/path/to/kg-ontology-decision-framework-reboot
```

설치 뒤 `AGENTS.md`, `.codex/config.toml`, `.codex/agents/`, `.agents/skills/harness/`를 확인한다. 설치본을 단순 링크로 호출하는 것이 아니라 프로젝트에 복사해 버전 관리한다. 재설치는 프로젝트 도메인 블록과 맞춤 파일을 덮어쓰지 않는지 dry-run diff로 먼저 확인한다.

## 실행 초기화와 배정

1. 메인은 목표, 작업, 의존성, `ownership`, `inputs`, 결정, 스킬과 완료 기준이 담긴 계획 파일을 작성한다.
2. `run.py init`으로 새 실행을 만든다. 과거 실행 장부는 수정하지 않는다.
3. `run.py ready`로 의존성이 충족된 작업만 확인한다.
4. 첫 자식은 제품 파일을 읽거나 쓰지 않는 대기 지시로 생성한다.
5. 실제 agent ID가 반환되면 `run.py start`로 해당 작업과 연결한다.
6. 갱신된 `input.md`를 실제 후속 메시지로 전달한다. 필요한 경우에만 추가 자식을 만들며 최대 3개를 넘지 않는다.

```bash
python .agents/skills/harness/scripts/run.py --project . init \
  --plan-file /absolute/path/to/plan.json --run-id example-v1
python .agents/skills/harness/scripts/run.py --project . ready --run example-v1
python .agents/skills/harness/scripts/run.py --project . start \
  --run example-v1 --task source-audit --agent-id ACTUAL_AGENT_ID
```

`run.py`는 로컬 장부만 갱신한다. 자식 생성·메시지 전송·중단에는 현재 런타임의 네이티브 도구가 별도로 필요하다.

## 결과·리뷰·QA

작업자는 산출물, 실제 검사와 문제를 `TaskResult` 형식으로 반환한다. 메인은 결과 파일을 보존하고 `run.py result`로 등록한다. 완료 결과에는 최소 하나의 필수 검사가 실제 근거와 함께 통과해야 한다.

구현 산출물은 생산자와 다른 관점에서 검토한다. 리뷰어는 제품 파일을 고치지 않고 재현 가능한 결함을 반환한다. QA는 실제 사용자 경계의 출력과 오류 동작을 확인하며, 제품 수정은 워커에게 되돌린다. 수정 후 영향받는 검사를 다시 실행한다.

## 실패와 부분 재개

하네스가 기록하는 프로젝트 상대 경로는 운영체제와 관계없이 `/` 구분자를 사용하는 POSIX 형식으로 정규화한다. 따라서 Windows에서 생성한 실행 상태도 Linux/WSL에서 재개할 수 있고, 역방향 재개도 같은 계약을 따른다.

- 일시적 작업 오류는 원인을 확인한 뒤 기본 1회 재시도한다.
- 필수 작업의 실패·미실행은 완료를 막는다. 선택 작업 생략은 이유와 영향을 기록한다.
- 첫 네이티브 생성에서 실제 ID 없이 실패하면 추가 생성을 중단하고, 순차 대체 작업과 네이티브 실행 검증을 구분한다.
- 입력 파일 내용만 바뀌면 `resume`으로 새 실행을 만들 수 있다.
- 목표, 작업 구성, 소유권, 결정 또는 완료 기준이 바뀌면 새 계획 파일과 새 실행 ID를 사용한다.
- 승인된 산출물이 후속 단계에서 변경되면 생산자와 소비자의 관련 결과를 무효화하고 재검증한다.

```bash
python .agents/skills/harness/scripts/run.py --project . resume \
  --run example-v1 --new-run example-v2
```

## 완료와 보고

1. 실제 유휴·중단·종료 응답을 에이전트 수명 상태와 함께 기록한다.
2. `validate.py --project . --run <id> --complete`로 필수 결과, 지문, 산출물, 검사 근거를 확인한다.
3. [보고 정책](reporting.md)에 따라 `CompletionReport` 기반 Markdown·JSON을 만들고, 중대 변경이면 PDF도 만든다.
4. 최종 응답에서 변경 파일, 검사 결과, 미실행 검사, 공개 보고서, 커밋 또는 PR을 연결한다.

구조 검증 통과는 실제 에이전트 발견·샌드박스 적용·병렬 실행의 증거가 아니다. 실제 네이티브 스모크 결과는 별도로 보고한다.

## 계약·문서 동기화 검사

CI는 `harness-manifest.yaml`을 구조화된 색인으로 사용해 다음 실행 기준을 대조한다.

- manifest의 역할명·경로와 `.codex/agents/*.toml`의 `name`
- manifest의 스킬명·경로와 각 `SKILL.md` frontmatter의 `name`
- manifest의 계약별 버전·경로와 `contracts/*.schema.json`의 `title`, `schema_version`, `$id`
- 모든 JSON Schema의 Draft 2020-12 meta-schema 적합성
- manifest의 동시 실행 한도와 `.codex/config.toml`
- manifest에 등록된 설계 문서의 실제 존재 여부

```bash
python scripts/validation/validate_harness_sync.py --project .
```

학사 출처·규칙·관계·검수 큐는 별도 의미 검증기로 확인한다. 스키마 통과만으로 승인된 근거,
존재하는 관계 대상 또는 전수 검수 완료를 주장하지 않는다.
또한 학생 답변을 `supported`로 공개하기 전에는 `validate_evidence_packet`으로 규칙 해시,
승인 상태, 입학연도 범위와 인용 연결을 현재 저장소 지식과 대조한다.

```bash
python scripts/validation/validate_academic_knowledge.py --project .
```

`AGENTS.md`, `harness-manifest.yaml`, `.codex/config.toml`, 에이전트·스킬·계약, 하네스 CI workflow, `scripts/reporting/` 또는 `scripts/validation/`이 바뀌면 같은 변경에서 `docs/harness/` 설명과 ADR을 갱신해야 한다. 이 freshness 정책의 실제 판정은 CI 검사 코드가 담당한다.

GitHub용 Mermaid는 코드 펜스 개수만 세지 않는다. CI가 고정된 `@mermaid-js/mermaid-cli@11.12.0`으로 `docs/harness/`의 모든 Mermaid 블록을 SVG로 실제 렌더링한다.

```bash
python scripts/validation/render_mermaid.py \
  --project . --output-dir /tmp/harness-mermaid-rendered
```
