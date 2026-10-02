# 목적별 학사 대화 실제 평가 - 미완료

2026학번 컴퓨터공학과. 승인29규칙·3출처·검증43과목은 변경하지 않았다.

실제 관찰 66/66발화, passed 36, failed 29, review_required 1.

수정용 36발화: 통과25·실패10·판정대기1. 미사용 별도30발화: 통과11·실패19. 단순 전체 통과 비율로 일반적인 챗봇 정확도를 추정하지 않는다.

실패29건의 원시 표시는 제품 관련26건, 수집기/판정기 조건 혼재3건이다. R18T2/H03T2/H06T2는 앞 문맥이 없을 때 실제 화면과 같은 원래 질문 전달을 판정기가 다르게 기대한 경우다. 원래 실패 판정을 보존하고 임의 통과로 바꾸지 않았다. 다른 실패에는 요청 누락·불필요한 조회·문맥/메타데이터/범위 차이가 포함돼 모두 틀린 학사 사실이나 환각으로 해석하지 않는다. 예를 들어 H11은 3학년 전필7/전선12 숫자는 맞지만 2학년 목록까지 덧붙였다.

API의 생성 표시는 generated34·fallback21·not_applicable11이다. 안전한 대체 답변과 자연어 성공은 구분한다. 관찰 평균6.688초·최대15.719초·p95(최근접 순위)14.625초는 모델을 쓰지 않는 확인/범위 밖 발화도 포함하며 일반 모델 벤치마크가 아니다.

수정용/별도 질문은 분리했고 별도 질문을 제품 구현 담당에게 전달하지 않았다. QA는 승인 값·해시·정확한 원문 위치·질문 요구를 비교했다. 미실행과 판정 보류를 통과로 바꾸지 않았다.

실제 API 시험과 비신뢰 모의 모델 출력의 경계 시험은 별도다. 자동경계 검사에서 위반을 재현했다는 사실이 실제 Gemma가 같은 문장을 출력했다는 뜻은 아니다. 모델의 generated 표시만으로 의미 정확성이나 환각0을 증명하지 않는다.

## 남은 필수 결함

- R5-F1: 균형교양 영역별 최소1을3으로 변조한 문장 허용
- R5-F2: 과목 학점+이수의무에서 학점 누락을 supported로 표시
- R5-F3: 졸업요건 전체+과목 학점의 속성 누락
- R5-F4: 전필/전선 개수 요청에9/34숫자 없이 분류만 반환
- R5-F5: 심층상담 최소1과 매학기 권장사항의 의무 혼동
- R5-F6: 캡스톤II PASS 선행조건의 부정을 잘못 허용

누적5회 수정 뒤 필수 실패가 남아 미완료다. 공개 서버는 기존 시연 실행을 유지하고 수정본을 새 공개 버전으로 승격하지 않았다. 사람 UAT는 사용자 요청으로 생략했다.

## 질문별 실제 결과

| ID | 구분 | 질문 | 판정 | 실패 분류/근거 |
|---|---|---|---|---|
| PD2-R01-T01 | repair | 2026학번 컴공인데, 컴퓨터구조 학점만 확인해 주세요. | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R02-T01 | repair | 고급자료구조의 이수구분은 전공필수인가요, 전공선택인가요? | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R03-T01 | repair | 교육과정 표에서 객체지향프로그래밍은 몇 학년의 어느 학기로 잡혀 있나요? | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R04-T01 | repair | 소프트웨어설계 | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R04-T02 | repair | 편성 학기가 궁금해요. | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R05-T01 | repair | 데이타통신은 학점이 어떻게 돼요? | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R05-T02 | repair | 정보보안 | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R05-T03 | repair | 피지컬AI입문 | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R06-T01 | repair | 알고리즘의 학점하고 편성 학기를 함께 확인하고 싶어요. | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R06-T02 | repair | 운영체제 | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R06-T03 | repair | 편성 학기만요. | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R07-T01 | repair | 컴퓨터구조는 전선이니 꼭 이수할 필요는 없는 거죠? | failed | missing_retrieval / supported_demand_unanswered, rule_demand_not_retrieved, completion_not_rendered |
| PD2-R08-T01 | repair | 고급자료구조를 빼고도 2026학번 컴공 졸업요건을 채울 수 있나요? | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R09-T01 | repair | 컴퓨터구조를 꼭 이수해야 한다면 수업에는 출석하지 않고 시험만 보면 되나요? | failed | question_understanding / gap_reason_wrong, gap_demand_not_explained |
| PD2-R10-T01 | repair | 다른 대학에서 들은 자료구조로 고급자료구조 이수를 대신 인정받을 수 있을까요? | failed | question_understanding / gap_reason_wrong, gap_demand_not_explained |
| PD2-R11-T01 | repair | 산학캡스톤디자인2가 전공선택으로 보이는데 졸업할 때 아예 안 해도 되는 건가요? | failed | question_understanding / gap_reason_wrong, gap_demand_not_explained |
| PD2-R12-T01 | repair | 컴구 듣는 걸 고민 중인데 학점부터 알려줄래요? | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R13-T01 | repair | 고자구는 졸업할 때 반드시 이수해야 하는 과목에 포함돼요? | failed | missing_retrieval / supported_demand_unanswered, rule_demand_not_retrieved, completion_not_rendered |
| PD2-R14-T01 | repair | 네프가 교육과정에는 몇 학년 몇 학기로 나와 있어요? | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R15-T01 | repair | 고급컴퓨자료터구조는 전필 과목이야? | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R15-T02 | repair | 고급자료구조를 말한 거예요. | failed | question_understanding / unrequested_body_fields |
| PD2-R15-T03 | repair | 그러면 컴퓨터구조는 전필이에요? | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R16-T01 | repair | 네트웍프로그래밍이 몇 학기 과목인지 찾고 있어요. | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R16-T02 | repair | 네트워크프로그래밍을 뜻했어요. | failed | question_understanding / unrequested_body_fields |
| PD2-R17-T01 | repair | 컴구는 전필인지 알려주세요. | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R17-T02 | repair | 고자구 | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R17-T03 | repair | 네프 | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R18-T01 | repair | 컴퓨터그래픽스 알려주세요. | failed | question_understanding / clarification_not_preserved, clarification_identity_not_retained, continuation_context_missing |
| PD2-R18-T02 | repair | 학점하고 이수구분을 알고 싶어요. | failed | question_understanding / collector_context_mismatch |
| PD2-R19-T01 | repair | 클라우드컴퓨팅은 몇 학점으로 돼 있나요? | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R19-T02 | repair | 그 과목이 편성된 학기도 알려주세요. | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R20-T01 | repair | 2026학번 컴공 단일전공 졸업 조건을 전체적으로 설명해 주세요. 총학점, 교양과 전공, 졸업논문 쪽에서 무엇을 확인해야 해요? | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R21-T01 | repair | 2026 컴공 교육과정의 전공필수 과목 수와 전공선택 과목 수를 둘 다 알고 싶어요. 각 구분의 개수를 따로 써 주세요. | passed | - / 승인 사실·인용·요구 검증 |
| PD2-R22-T01 | repair | 피지컬AI입문에서는 실제로 무엇을 배우나요? 수업 내용이 적힌 자료를 근거로 설명해 주세요. | passed | genuine_evidence_gap / 승인 사실·인용·요구 검증 |
| PD2-R23-T01 | repair | 이산수학이 편성표에 있어도 이번 학기에 실제로 개설되는지는 따로 봐야 하나요? 이번 개설 여부를 알려주세요. | review_required | - / gap_demand_not_explained |
| PD2-R24-T01 | repair | 2026학번 컴공이에요. 제 이수내역을 아직 보내지 않았는데 졸업에 부족한 학점을 계산해 줄 수 있나요? | failed | question_understanding / gap_reason_wrong, gap_demand_not_explained |
| PD2-H01-T01 | heldout | 고자구 이수구분부터 확인할게요. | passed | - / 승인 사실·인용·요구 검증 |
| PD2-H01-T02 | heldout | 그럼 언제 듣게 편성되어 있어요? | passed | - / 승인 사실·인용·요구 검증 |
| PD2-H01-T03 | heldout | 네프는요? | passed | - / 승인 사실·인용·요구 검증 |
| PD2-H02-T01 | heldout | 네트워크프로그래밍 | passed | - / 승인 사실·인용·요구 검증 |
| PD2-H02-T02 | heldout | 꼭 마쳐야 하는 과목인지 알고 싶어서요. | failed | wrong_answer / gap_not_preserved, gap_reason_wrong, gap_demand_not_explained |
| PD2-H02-T03 | heldout | 컴구도 같은 기준으로 봐 주세요. | failed | missing_retrieval / rule_demand_not_retrieved, completion_not_rendered |
| PD2-H03-T01 | heldout | 현장실습4의 인정 학점은 얼마로 편성돼 있나요? | failed | question_understanding / supported_demand_unanswered |
| PD2-H03-T02 | heldout | 2026학번 컴공 졸업 조건도 전체적으로 알려주세요. | failed | question_understanding / collector_context_mismatch |
| PD2-H03-T03 | heldout | 고급자료구조는요? | failed | question_understanding / required_context_not_used |
| PD2-H04-T01 | heldout | 전선이라고 들은 컴퓨터구조와 알고리즘을 둘 다 안 들어도, 다른 전공학점만 채우면 되는지 확인해 주세요. | failed | wrong_answer / supported_demand_unanswered, rule_demand_not_retrieved, completion_subject_not_rendered, completion_not_rendered, false_elective_premise_uncorrected, completion_subject_not_rendered, completion_not_rendered, false_elective_premise_uncorrected |
| PD2-H05-T01 | heldout | 2026학번 컴공이 졸업하려면 심층상담을 이수해야 하나요? | failed | missing_retrieval / rule_demand_not_retrieved |
| PD2-H05-T02 | heldout | 그렇다면 상담에 실제로 참여하지 않아도 이수 처리되는지 궁금해요. | failed | question_understanding / gap_reason_wrong, gap_demand_not_explained, required_context_not_used |
| PD2-H06-T01 | heldout | 컴퓨터자료구조는 몇 학점으로 계산하나요? | failed | question_understanding / clarification_not_preserved, real_candidate_missing, real_candidate_missing, clarification_identity_not_retained, clarification_identity_not_retained, clarification_reason_wrong, continuation_context_missing |
| PD2-H06-T02 | heldout | 제 질문은 컴퓨터구조였어요. | failed | question_understanding / collector_context_mismatch |
| PD2-H06-T03 | heldout | 아까 학점 말고 고급자료구조가 몇 학기에 편성됐는지 물어볼게요. | passed | - / 승인 사실·인용·요구 검증 |
| PD2-H07-T01 | heldout | 운영체제의 학점과 전필 여부를 봐 주세요. | passed | - / 승인 사실·인용·요구 검증 |
| PD2-H07-T02 | heldout | 영상처리 | passed | - / 승인 사실·인용·요구 검증 |
| PD2-H07-T03 | heldout | 필수·선택 구분 쪽을 이어서 물은 거예요. | passed | - / 승인 사실·인용·요구 검증 |
| PD2-H08-T01 | heldout | 네트워크컴퓨터프로그래밍의 학점을 찾고 있어요. | failed | question_understanding / real_candidate_missing, clarification_identity_not_retained |
| PD2-H08-T02 | heldout | 네트워크프로그래밍으로 확인해 주세요. | failed | question_understanding / unrequested_body_fields |
| PD2-H08-T03 | heldout | 컴구 학점도 추가로요. | passed | - / 승인 사실·인용·요구 검증 |
| PD2-H09-T01 | heldout | 데이터베이쓰개론은 전선인지 전필인지 찾아줄래요? | passed | - / 승인 사실·인용·요구 검증 |
| PD2-H09-T02 | heldout | 데이타베이스개론을 뜻한 질문이에요. | failed | question_understanding / unrequested_body_fields |
| PD2-H10-T01 | heldout | 26학번 컴공 졸업 준비 중이에요. 학점 합계뿐 아니라 필수 이수, 교양, 졸업논문까지 챙겨야 할 조건을 한 번에 정리해 줄래요? | failed | missing_retrieval / supported_demand_unanswered, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, overview_is_catalogue_dump |
| PD2-H11-T01 | heldout | 2026학번 교육과정에서 3학년에 편성된 전필은 몇 개이고 전선은 몇 개인가요? 두 학기 모두 편성된 과목은 같은 구분 안에서 한 번만 세어 주세요. | failed | missing_retrieval / count_set_incomplete, count_set_incomplete, count_union_mismatch |
| PD2-H12-T01 | heldout | 2026학번 컴공 전필 과목 수와 전선 과목 수를 각각 알려주고, 다음 학기 실제 개설되는 전선은 그중 몇 개인지도 확인해 주세요. | failed | wrong_answer / gap_not_preserved, gap_reason_wrong, gap_demand_not_explained |
| PD2-H13-T01 | heldout | 딥러닝과자연어처리는 몇 학점인지 먼저 알고 싶어요. 그 수업에서 사용하는 교재랑 주차별 내용도 자료로 확인할 수 있을까요? | passed | genuine_evidence_gap / 승인 사실·인용·요구 검증 |
| PD2-H14-T01 | heldout | 컴구를 꼭 이수해야 한다면 제가 따로 만든 CPU 프로젝트로 출석과 과목 이수를 모두 대신할 수 있나요? | failed | wrong_answer / gap_not_preserved, gap_reason_wrong, gap_demand_not_explained, gap_demand_not_explained |
| PD2-H15-T01 | heldout | 산학캡스톤디자인1은 전선으로 분류되는지 확인해 주고, 전선이면 졸업까지 이수하지 않아도 되는지도 따로 설명해 주세요. | failed | question_understanding / gap_reason_wrong |
| PD2-H16-T01 | heldout | 졸업논문과 심층상담이 학점 합계에 거의 영향을 안 주면, 2026학번 졸업 준비에서는 두 과목을 제외해도 되나요? | failed | missing_retrieval / supported_demand_unanswered, rule_demand_not_retrieved, rule_demand_not_retrieved, rule_demand_not_retrieved, completion_not_rendered, completion_not_rendered |

정확한 기대값·실제 답변·판정 사유·동결 파일 해시는 [정제 평가 JSON](20261001-purpose-dialogue.json)에 있다. 원시 요청·모델 지시·서버 접속값·학생 성적표는 공개하지 않는다.

## 코드 기준과 변경 파일

기준 `1d22f14e39651438f3a1eeb4008eada5d3c9c61a`, 구현 `2c644c07b8a1555007ead475b327bcd9e6865062`, 브랜치 `codex/semantic-prototype`.

구현 커밋의 변경 파일 61개는 평가 JSON의 `changed_code_files`에 모두 기록했다. 평가 파일과 보고서 발행 커밋은 구현 커밋 이후에 따로 남긴다. 지문은 실제 동결 작업 파일의 바이트이며 Git의 줄바꿈 정규화 뒤 blob 해시와는 구분한다.
