# 학과 운영 추가 답변과 정정 이력 기록

- 실행 ID: `20261001-academic-practice-notes`
- 계약 버전: `1.0.0`
- 하네스 버전: `1.0.0 / upstream 79b82281d305c89181fbb216499d5f1e962c14ed`
- 브랜치: `codex/academic-practices-review-notes`
- 기준 커밋: `c60299abf4c3f89da16228b9ac029e82b2a71395`
- 결과 커밋: `c247faa8a0d50478edd350e7f37c7eee4add3d38`

## 요청

이번 및 앞선 학사 조건 질문·답변을 기존 문서에 기록하고 이후 정정 가능성을 반영한다.

## 요약

기존 운영관계 검수 문서에 질문1~6 답변 원문·해석·미확정 범위와 항목 ID, 버전별 정정 절차를 추가했다. 사용자 설명은 기록용으로만 보존하며 승인 규칙·지식그래프·챗봇 판정은 변경하지 않았다.

## 변경 사항

| 구분 | 파일 | 내용 |
|---|---|---|
| documentation | `reviews/academic/clarifications/2026-department-practices-20260929.md` | 기존 원문을 보존하며 6개 추가 답변과 5개 최신 항목 색인·수정 버전 및 절차 기록. 600점은 고정 면제 기준으로 해석하지 않음. |
| documentation | `CHANGELOG.md` | 추가 기록과 유지되는 승인·면제·제출 시점 경계 기록. |

## 에이전트 결과

| 역할 | 작업 | 상태 | 결과 |
|---|---|---|---|
| main_session | document-notes | completed | 국소 문서 작업을 직접 수행하고 기존 학사·권한·격리·답변·공개 보고 검증을 실행. 서브에이전트는 실행하지 않음. |

## 검사 결과

| 검사 | 필수 | 상태 | 명령 | 근거 |
|---|---:|---|---|---|
| 기존 학사 지식 | yes | passed | python scripts/validation/validate_academic_knowledge.py --project . | 출처·규칙·관계·검수대장 검사 통과. |
| 검수 세션 권한 | yes | passed | python scripts/validation/validate_academic_clarifications.py --project . | 질문 패킷 범위와 기존 세션 권한 검증 통과. 이번 기록을 신규 학과 승인 사건으로 만들지 않음. |
| 미검증 주장 격리 | yes | passed | python scripts/validation/validate_academic_research.py --project . | 미검증 연구 후보가 답변 근거에서 격리된 상태 유지. |
| 답변 회귀 평가 | yes | passed | python scripts/validation/validate_academic_answer_engine.py --project . | 기존265건 평가 통과. 실제 사용자 평가나 새로운 운영조건의 정확도 검증이 아님. |
| 실행 자산 불변 | yes | passed | - | 기준c60299a 대비 제품·규칙·계약·설정·구조화 검수 JSON 변경 없음. |
| 문서·공개 정제 | yes | passed | python scripts/reporting/ci_checks.py --project . --changed-from c60299abf4c3f89da16228b9ac029e82b2a71395 | 공개 보고서·문서 링크·갱신 검사와 git diff --check 통과. |
| 전체 단위 테스트·신규 규칙·서버 | no | not_run | - | 기록 문서만 변경해 목표 학사 회귀를 실행. 새 규칙 승인·학생 최종 판정·GPU 실행은 범위 밖. |

## 학사 근거 변경

학사 근거 변경 없음.

## 이슈 및 남은 작업

- **info**: 사용자 설명은 record_only. 공식 적용 범위와 새 검수 권한을 확인한 별도 반영 작업 전에는 학생 답변 근거로 사용할 수 없음.
- **warning**: 코딩 우수자 순위 비교 대상·동점 처리·수강/이수 자체와 결과물 면제 구분·정확한 제출 학기는 아직 명확하지 않음. 600점은 고정 임계값이 아님.

## 공개 정보

- 주요 작업: `no`
- PDF 필요: `no`
