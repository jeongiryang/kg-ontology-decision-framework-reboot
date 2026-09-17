# `codex-harness` 업스트림 관리

## 출처와 고정 버전

- 업스트림: <https://github.com/jeongiryang/codex-harness>
- 고정 커밋: `79b82281d305c89181fbb216499d5f1e962c14ed`
- 업스트림 라이선스: Apache-2.0

하네스는 서브모듈이나 런타임 원격 의존성이 아니라 프로젝트에 설치된 복사본으로 관리한다. 따라서 오프라인에서도 설정·스킬·검증 스크립트를 함께 검토할 수 있다. 업스트림 출처와 라이선스는 루트의 `THIRD_PARTY_NOTICES`에서 보존해야 한다. 프로젝트 전체 라이선스는 산학협력 지식재산권 확인 전까지 별도로 부여하지 않는다.

## 업스트림과 프로젝트 맞춤의 경계

| 구분 | 예 |
| --- | --- |
| 설치기 관리 영역 | `AGENTS.md`의 `codex-harness:start/end` 블록, 기본 역할, `.agents/skills/harness/` |
| 프로젝트 관리 영역 | 학사 전용 역할·스킬, JSON Schema, `harness-manifest.yaml`, `docs/harness/`, 보고 정책 |

프로젝트 맞춤을 업스트림 파일 안에 섞어야 할 때는 이유와 갱신 시 충돌 위험을 ADR 또는 변경 보고서에 남긴다. `AGENTS.md`의 도메인 포인터는 설치기 블록과 별도 마커로 유지한다.

## 갱신 절차

1. 깨끗한 WSL 임시 체크아웃에서 현재 고정 커밋과 후보 커밋의 diff, 릴리스 노트와 라이선스를 검토한다.
2. 후보 커밋으로 `scripts/install.py --target <temporary-target> --dry-run`을 실행한다.
3. 임시 대상에 설치해 기본 에이전트, 스킬, 스크립트와 `AGENTS.md` 변경을 현재 프로젝트와 비교한다.
4. 프로젝트 맞춤 역할·스킬·계약·도메인 포인터가 보존되는지 확인한다.
5. `harness-manifest.yaml`의 업스트림 SHA, 이 문서, `THIRD_PARTY_NOTICES`와 관련 ADR을 함께 갱신한다.
6. 구조 검증과 작은 실제 네이티브 스모크 테스트를 수행한다. 구조 검증만으로 에이전트 실행 성공을 주장하지 않는다.
7. 실행 보고서에 변경 파일, 마이그레이션 영향, 실제 검사와 롤백 기준을 남긴다.

갱신을 위해 Windows 일반 clone의 심볼릭 링크를 억지로 일반 파일로 바꾸지 않는다. WSL에서 체크아웃·설치하는 결정은 [ADR-0001](decisions/0001-vendored-harness-installation.md)에 기록한다.

## 롤백

갱신이 실패하면 사용자 변경을 `git reset --hard` 등으로 지우지 않는다. 설치 전 diff와 Git 이력을 기준으로 업스트림 갱신분만 되돌리고, 이전 고정 SHA로 구조 및 스모크 검사를 다시 수행한다. 이미 생성된 과거 실행 장부와 공개 보고서는 덮어쓰지 않는다.

