# ADR-0005: 하네스 계약 동기화와 Mermaid 렌더링을 CI에서 검증한다

- 상태: 채택
- 날짜: 2026-09-17

## 맥락

`harness-manifest.yaml`, 에이전트 TOML, 스킬 frontmatter와 JSON Schema가 각각 문법상 유효해도 이름·경로·버전이 서로 어긋날 수 있다. JSON 파일을 읽는 것만으로 JSON Schema 자체가 선택한 meta-schema에 적합한지도 확인할 수 없다. 또한 Markdown 코드 펜스 균형은 Mermaid 문법이나 GitHub 렌더링 가능성을 증명하지 않는다.

## 결정

전용 동기화 검증기가 다음을 CI에서 비교한다.

- manifest와 모든 `.codex/agents/*.toml`
- manifest와 모든 `.agents/skills/*/SKILL.md`
- manifest 계약명·경로·버전과 모든 `contracts/*.schema.json`
- manifest의 동시 실행 한도와 `.codex/config.toml`
- manifest에 등록된 설계 문서 경로
- 모든 계약의 JSON Schema Draft 2020-12 meta-schema 적합성

GitHub Actions는 고정 버전 `@mermaid-js/mermaid-cli@11.12.0`을 설치하고 `docs/harness/`의 모든 Mermaid 블록을 SVG로 실제 렌더링한다. 하네스 실행 설정, CI workflow, 보고·검증 스크립트가 바뀌면 설계 문서와 ADR을 함께 바꾸도록 freshness 범위에 포함한다.

## 결과

구조 드리프트와 잘못된 스키마를 코드 리뷰 이전에 재현 가능하게 차단하고, 다이어그램의 실제 파서 오류도 감지한다. CI에 Python YAML·JSON Schema 의존성과 Node/Mermaid 설치 시간이 추가된다. Mermaid 패키지 버전 갱신은 업스트림 검토와 실제 렌더 결과를 포함하는 별도 변경으로 수행한다.

## 검토한 대안

- 수동 리뷰만 수행: 누락·버전 드리프트를 일관되게 차단하지 못해 채택하지 않았다.
- YAML/TOML/JSON 파싱만 수행: 파일 간 의미적 동기화와 meta-schema 오류를 놓쳐 채택하지 않았다.
- Mermaid 펜스 균형만 확인: 실제 Mermaid 파서 오류를 검출하지 못해 채택하지 않았다.

