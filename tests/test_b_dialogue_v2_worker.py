"""B-v2 boundaries using synthetic questions and owned, mocked transports."""
import json
import asyncio
from threading import Event
import unittest
from copy import deepcopy
from unittest.mock import patch

from fastapi.testclient import TestClient
from academic_assistant import api, llm
from academic_assistant.assistant import SemanticAssistant, _verify_rule_text, _write, _plan
from academic_assistant.core import AnswerEngine, validate_public_text_safety
from academic_assistant.progress import ProgressReporter, progress_scope, accepts_ndjson
from academic_assistant.registry import Registry
from academic_assistant.semantic_llm import SemanticDocument, SemanticLLMClient, _Session
from academic_assistant.neo4j_evidence import Neo4jEvidenceReader, Neo4jSettings
from test_b_input_routing_worker import TypedModel as LegacyTypedFixture, course, bound_prose
from test_semantic_assistant_worker import request, purpose_natural
from test_neo4j_evidence import Driver, stored_graph


class TypedModel(LegacyTypedFixture):
    def __init__(self, *tools, **kwargs):
        super().__init__(*tools, **kwargs)
        self.plan_document['coverage'] = deepcopy(self.plan_document['requests'])


class CorrectingModel(TypedModel):
    def __init__(self, *tools, first, corrected=bound_prose, **kwargs):
        super().__init__(*tools, writer=first, **kwargs)
        self.corrected = corrected
        self.repairs = []

    def rewrite(self, payload, failed, error_code):
        self.repairs.append((deepcopy(payload), failed, error_code))
        return SemanticDocument(self.corrected(deepcopy(payload)))


def replace_text(text):
    def write(payload):
        result = bound_prose(payload)
        result['sections'][0]['text'] = text
        return result
    return write


class DialogueV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.load()
        cls.engine = AnswerEngine(cls.registry)

    def ask(self, text, model, **kwargs):
        return SemanticAssistant(self.engine, model).chat(request(text, **kwargs))

    def fact(self, intent):
        entry = next(item for item in self.registry.intents['intents'] if item['intent_id'] == intent)
        from academic_assistant.conversation import approved_statement
        return [dict(fact_id=key, statement=approved_statement(self.registry.rules[key]),
                     outcome=self.registry.rules[key]['decision']['outcome']) for key in entry['rule_ids']]

    def test_b_names_ids_question_context_and_output_allowed(self):
        text = '홍길동님, 컴퓨터구조는 3학점입니다. 학번 20260001.'
        model = TypedModel(course('컴퓨터구조'), writer=replace_text(text), context=True)
        result = self.ask('홍길동 학번 20260001인데 컴구 학점 알려줘', model,
                          previous_question='홍길동님 컴퓨터구조 학점')
        self.assertEqual(('supported', 'generated'), (result.status, result.generation_status))
        self.assertEqual(text, result.answer)
        self.assertIn('홍길동', model.payloads[0]['question'])
        self.assertNotIn('earned_credits', model.payloads[0])
        self.assertNotIn('홍길동', json.dumps(model.payloads[1], ensure_ascii=False))

    def test_a_privacy_remains_default_and_no_blanket_flag(self):
        for text in ('홍길동님 졸업학점', '학번 20260001 졸업학점'):
            with self.assertRaises(ValueError):
                validate_public_text_safety(text)
            validate_public_text_safety(text, allow_identifying=True)
        with self.assertRaises(ValueError):
            validate_public_text_safety('question\x00', allow_identifying=True)

    def test_bare_student_identifier_is_not_an_academic_number(self):
        result = self.ask('학번 20260001 컴구 학점', TypedModel(course('컴퓨터구조'),
                          writer=replace_text('20260001 학생님, 컴퓨터구조는 3학점입니다.')))
        self.assertEqual('generated', result.generation_status)
        result = self.ask('컴구 학점', TypedModel(course('컴퓨터구조'), writer=replace_text('컴퓨터구조는 20260001학점입니다.')))
        self.assertEqual('processing_unavailable', result.reason_code)

    def test_current_records_and_calculations_never_forwarded(self):
        model = TypedModel(course('컴퓨터구조'))
        result = self.ask('컴구 학점', model, earned_credits={'credits.graduation.total': 127})
        self.assertEqual('supported', result.status)
        wire = json.dumps(model.payloads, ensure_ascii=False)
        self.assertNotIn('127', wire)
        self.assertNotIn('earned_credits', wire)

    def test_structured_transcript_is_local_even_with_named_question(self):
        from academic_assistant.transcript_models import TranscriptAssessmentRequest
        record = TranscriptAssessmentRequest(admission_year=2026, matched_curriculum_year=2026,
            department='컴퓨터공학과', degree_track='single_major', confirmed=True, record_complete=True,
            courses=[dict(row_id='private-synthetic-row', course_name='비공개합성강의', credits=7, grade='A0', category='free')])
        model = TypedModel(dict(kind='transcript', topic='graduation_credits'))
        result = self.ask('홍길동의 현재 성적표 졸업 부족학점', model, transcript=record)
        self.assertEqual('supported', result.status)
        self.assertIn('123', result.answer)
        wire = json.dumps(model.payloads, ensure_ascii=False)
        for secret in ('private-synthetic-row','비공개합성강의','"grade"','"A0"','123'):
            self.assertNotIn(secret, wire)
        self.assertTrue(model.payloads[0]['has_transcript'])

    def test_generic_conjunction_and_true_entity_scope(self):
        for text in ('컴퓨터구조 내용과 학점을 알려줘', '소프트웨어공학 전공 필수 과목인가요?',
                     '소프트웨어공학과 컴퓨터구조 학점을 각각 알려줘'):
            tools = [course('소프트웨어공학'), course('컴퓨터구조')] if '각각' in text else [course('소프트웨어공학', ('category',))] if text.startswith('소프트웨어') else [course('컴퓨터구조')]
            self.assertEqual('supported', self.ask(text, TypedModel(*tools)).status, text)
        for text in ('소프트웨어공학을 전공하는 학생의 졸업요건', '소프트웨어공학 전공 기준', '전자과 졸업요건',
                     '2025학번 컴구 학점', '정보학과 졸업요건'):
            model = TypedModel(dict(kind='requirements_overview'))
            self.assertEqual('out_of_scope', self.ask(text, model).status, text)
            self.assertEqual([], model.payloads)

    def test_source_backed_additional_attributes_are_not_phrase_refused(self):
        model = TypedModel(course('컴퓨터구조'), writer=replace_text('컴퓨터구조는 3학점인 전공필수 과목이고 3학년 첫 학기에 편성되어 있습니다.'))
        self.assertEqual('generated', self.ask('컴구 학점', model).generation_status)
        for bad in ('컴퓨터구조는 3학점인 전공선택 과목입니다.',
                    '컴퓨터구조는 3학점이고 2학년 첫 학기에 편성됩니다.',
                    '컴퓨터구조는 3학점입니다. 양자역학도 3학점입니다.',
                    '컴퓨터구조는 3학점입니다. 양자역학 3학점.'):
            result = self.ask('컴구 학점', TypedModel(course('컴퓨터구조'), writer=replace_text(bad)))
            self.assertEqual('processing_unavailable', result.reason_code, bad)
            self.assertNotIn(bad, result.answer)

    def test_compound_subject_omission_is_processing_not_missing_evidence(self):
        result = self.ask('졸업요건과 컴구 학점을 함께', TypedModel(dict(kind='requirements_overview')))
        self.assertEqual(('insufficient_evidence', 'processing_unavailable'), (result.status, result.reason_code))
        self.assertEqual('generated', result.plan_status)

    def test_selected_followup_and_candidate_do_not_require_old_subjects(self):
        for text, previous in [('그럼 다른 것은요', '알고리즘 학점 및 편성 학년 학기'),
                               ('2번', '과목 후보 1 알고리즘 또는 2 소프트웨어공학 확인 요청 학점')]:
            result = self.ask(text, TypedModel(course('소프트웨어공학'), context=True), previous_question=previous)
            self.assertEqual('supported', result.status)
            self.assertIn('소프트웨어공학', result.context_question)
            self.assertNotIn('알고리즘', result.context_question)

    def test_per_area_cardinality_is_bound_not_just_allowed_number(self):
        facts = self.fact('general.balanced-area-coverage')
        _verify_rule_text(facts[0]['statement'], facts)
        bad = facts[0]['statement'].replace('각각 1과목 이상', '각각 3과목 이상')
        with self.assertRaises(llm.LLMInvalidResponse):
            _verify_rule_text(bad, facts)

    def test_counseling_minimum_and_recommendation_both_bound(self):
        facts = self.fact('major.counseling-completion')
        _verify_rule_text('심층상담은 0학점이어도 최소 1회 이수해야 합니다. 학기마다 상담을 받는 것은 권장입니다.', facts)
        for bad in ('심층상담은 0학점이어도 최소 1회 이수해야 합니다. 매 학기 상담은 반드시 해야 하는 필수입니다.',
                    '심층상담은 0학점이어도 최소 1회 이수해야 합니다.'):
            with self.assertRaises(llm.LLMInvalidResponse):
                _verify_rule_text(bad, facts)

    def test_prerequisite_polarity_and_negative_graduation_caveat(self):
        facts = self.fact('operations.graduation-work-prerequisite')
        _verify_rule_text(facts[0]['statement'], facts)
        with self.assertRaises(llm.LLMInvalidResponse):
            _verify_rule_text(facts[0]['statement'] + ' 캡스톤 II를 PASS하지 않아도 졸업작품 수강할 수 있습니다.', facts)

    def test_minimum_can_use_natural_necessity_but_not_exactness(self):
        facts = self.fact('credits.graduation.total')
        _verify_rule_text('졸업에는 130학점이 필요합니다.', facts)
        for bad in ('졸업에는 정확히 130학점이 필요합니다.', '졸업에는 130학점이 필요하지 않습니다.'):
            with self.assertRaises(llm.LLMInvalidResponse):
                _verify_rule_text(bad, facts)

    def test_valid_extra_explanation_does_not_promote_recommendation(self):
        facts = self.fact('general.recommended-courses')
        _verify_rule_text(facts[0]['statement'] + ' 필수가 아닌 권장사항입니다.', facts)

    def test_llm_stage_explanations_reused_only_at_real_steps(self):
        model = TypedModel(course('컴퓨터구조'))
        model.plan_document['explanation'] = '어떤 과목의 정보를 묻는지 살펴봅니다.'
        model.plan_document['explanations'] = {'retrieval': '확인된 원문에서 해당 과목을 찾습니다.',
                                             'writing': '조회한 내용을 읽기 쉽게 설명합니다.'}
        events = []
        result = SemanticAssistant(self.engine, model).chat(request('컴구 학점'), ProgressReporter(events.append))
        self.assertEqual('generated', result.generation_status)
        produced = [(event['stage'], event['message']) for event in events if event['explanation_source'] == 'llm']
        self.assertIn(('intent', model.plan_document['explanation']), produced)
        self.assertIn(('retrieval', model.plan_document['explanations']['retrieval']), produced)
        self.assertIn(('writing', model.plan_document['explanations']['writing']), produced)
        self.assertEqual(2, len(model.payloads))
        self.assertTrue(all(event['explanation_source'] == 'system' for event in events if event['stage'] == 'query'))

    def test_per_area_counseling_prerequisite_rewrite_codes_are_observed(self):
        cases = [
            ('general.balanced-area-coverage', '균형교양 영역 기준',
             lambda text: text.replace('1과목 이상', '3과목 이상'), 'per_area_minimum_changed'),
            ('major.counseling-completion', '심층상담 기준',
             lambda text: '심층상담은 0학점이어도 최소 1회 이수해야 하며 매 학기 반드시 상담해야 합니다. 매 학기 권장입니다.', 'recommendation_promoted'),
            ('operations.graduation-work-prerequisite', '졸업작품 선행조건',
             lambda text: text + ' 캡스톤 II를 PASS하지 않아도 졸업작품 수강할 수 있습니다.', 'prerequisite_negated')]
        for intent, question, mutate, code in cases:
            with self.subTest(intent=intent):
                def first(payload):
                    document = purpose_natural(payload)
                    document['sections'][0]['text'] = mutate(document['sections'][0]['text'])
                    return document
                model = CorrectingModel(dict(kind='rule', intent_ids=[intent]), first=first, corrected=purpose_natural)
                result = self.ask(question, model)
                self.assertEqual('generated', result.generation_status)
                self.assertEqual(code, model.repairs[0][2])

    def test_one_corrective_rewrite_receives_same_facts_and_real_error(self):
        model = CorrectingModel(course('컴퓨터구조'), first=replace_text('컴퓨터구조는 9학점입니다.'))
        events = []
        result = SemanticAssistant(self.engine, model).chat(request('컴구 학점'), ProgressReporter(events.append))
        self.assertEqual(('supported', 'generated'), (result.status, result.generation_status))
        self.assertEqual('컴퓨터구조는 3학점입니다.', result.answer)
        self.assertEqual(1, len(model.repairs))
        self.assertEqual(model.payloads[1], model.repairs[0][0])
        self.assertEqual('unsupported_number', model.repairs[0][2])
        repair = next(event for event in events if event['stage'] == 'repair' and event['state'] == 'started')
        self.assertEqual(1, repair['details']['attempt'])
        self.assertTrue(model.released)

    def test_failed_rewrite_no_canned_academic_substitution(self):
        bad = replace_text('컴퓨터구조는 9학점입니다.')
        model = CorrectingModel(course('컴퓨터구조'), first=bad, corrected=bad)
        result = self.ask('컴구 학점', model)
        self.assertEqual(('insufficient_evidence', 'fallback', 'processing_unavailable'), (result.status, result.generation_status, result.reason_code))
        self.assertNotIn('3학점', result.answer)
        self.assertNotIn('9학점', result.answer)
        self.assertEqual(3, result.parts[0].course_evidence.courses[0].credits)
        self.assertEqual(1, len(model.repairs))
        self.assertFalse(model.accepted)

    def test_transport_unavailability_is_not_retried_as_rewrite(self):
        model = CorrectingModel(course('컴퓨터구조'), first=bound_prose)
        with patch.object(model, 'write', side_effect=llm.LLMUnavailable('private-provider-error')):
            result = self.ask('컴구 학점', model)
        self.assertEqual('processing_unavailable', result.reason_code)
        self.assertEqual([], model.repairs)
        self.assertNotIn('private-provider-error', result.answer)
        self.assertTrue(model.released)

    def test_list_summary_count_required_even_for_category_only(self):
        tool = course(properties=('category',), category='major_elective')
        model = CorrectingModel(tool, first=replace_text('검증된 목록은 전공선택입니다.'), corrected=purpose_natural)
        result = self.ask('선택 분류 목록', model)
        self.assertEqual('generated', result.generation_status)
        self.assertEqual('course_count_omitted', model.repairs[0][2])
        self.assertIn('34과목', result.answer)
        self.assertEqual(34, len(result.parts[0].course_evidence.courses))

    def test_list_names_and_all_requested_attributes_are_in_final_answer(self):
        tools = [course(properties=('names', 'credits', 'category', 'offering', 'code'), category=category)
                 for category in ('major_required', 'major_elective')]
        model = TypedModel(*tools, writer=purpose_natural)
        result = self.ask('필수 선택 과목 정보를 각각', model)
        self.assertEqual('generated', result.generation_status)
        for part in result.parts:
            for fact in part.course_evidence.courses:
                self.assertIn(fact.course_name + ':', part.text)
                self.assertIn(fact.course_code, part.text)
                self.assertIn(fact.offering_label, part.text)
                self.assertIn(str(fact.credits) + '학점', part.text)
        self.assertEqual([9, 34], [len(part.course_evidence.courses) for part in result.parts])

    def test_provider_list_projection_retains_requested_values_without_full_rows(self):
        facade = SemanticAssistant(self.engine)
        part, spec = facade._courses({'category':'major_required'}, 1, ['names', 'credits'])
        session = _Session(SemanticLLMClient(llm.LLMSettings('ollama', 'http://127.0.0.1:11434', 'synthetic')))
        captured = []
        def generate(stage, payload, schema, instruction):
            captured.append(deepcopy(payload))
            return SemanticDocument(dict(sections=[dict(part_id='p1',fact_ids=['f1'], text='전공필수 목록은 9과목입니다.')]))
        payload = dict(version='1.0.0', parts=[{key:spec[key] for key in ('part_id','title','facts','properties','course_summary')}])
        with patch.object(session, '_request', side_effect=generate):
            result = session.write(payload)
        aggregate = captured[0]['parts'][0]['facts'][0]
        self.assertEqual(['course_name','credits'], aggregate['columns'])
        self.assertEqual([[fact['course_name'],fact['credits']] for fact in spec['facts']], aggregate['rows'])
        self.assertNotIn('offering_years', aggregate)
        self.assertEqual([fact['fact_id'] for fact in spec['facts']], result.document['sections'][0]['fact_ids'])

    def test_provider_three_call_one_rewrite_and_single_shared_reservation(self):
        settings = llm.LLMSettings('ollama', 'http://127.0.0.1:11434', 'synthetic', timeout_seconds=1)
        client = SemanticLLMClient(settings)
        wire = []
        def post(req, remaining):
            wire.append(json.loads(req.data))
            self.assertTrue(llm._budget.active)
            self.assertLessEqual(remaining, 1)
            return json.dumps(dict(done=True, message=dict(content=json.dumps(dict(sections=[dict(part_id='p1', fact_ids=['f1'], text='컴퓨터구조는 3학점입니다.')]))))).encode()
        payload = dict(version='1.0.0', parts=[dict(part_id='p1', title='조회', facts=[dict(fact_id='real-id', course_name='컴퓨터구조', credits=3)], properties=['credits'])])
        with llm._budget.lock:
            llm._budget.active = False; llm._budget.next_allowed = 0
        try:
            with patch.object(client, '_post', side_effect=post), client.session() as session:
                session._request('plan', {}, {}, 'test')
                first = session.write(payload)
                session.rewrite(payload, first, 'course_credit_mismatch')
                with self.assertRaises(llm.LLMUnavailable):
                    session.rewrite(payload, first, 'same_error')
                with self.assertRaises(llm.LLMUnavailable):
                    session._request('extra', {}, {}, 'test')
                session.accept()
            self.assertFalse(llm._budget.active)
            self.assertEqual(3, len(wire))
            self.assertEqual('course_credit_mismatch', json.loads(wire[2]['messages'][1]['content'])['untrusted_data']['correction']['error_code'])
            self.assertEqual([512,1024,1024], [item['options']['num_predict'] for item in wire])
            self.assertTrue(all(len(item['messages']) == 2 for item in wire))
        finally:
            with llm._budget.lock:
                llm._budget.active = False; llm._budget.next_allowed = 0

    def test_deadline_exhaustion_does_not_trigger_repair_transport(self):
        session = _Session(SemanticLLMClient(llm.LLMSettings('ollama', 'http://127.0.0.1:11434', 'synthetic')))
        session.deadline = 0
        with patch.object(session.client, '_post') as post, self.assertRaises(llm.LLMUnavailable):
            session._request('plan', {}, {}, 'test')
        post.assert_not_called()

    def test_actual_graph_query_and_parameters_not_fake_or_secret(self):
        class CapturingDriver(Driver):
            def run(self, query, **params):
                self.query, self.parameters = query, params
                return super().run(query, **params)
        driver = CapturingDriver(stored_graph(self.registry))
        reader = Neo4jEvidenceReader(Neo4jSettings('bolt://127.0.0.1:7687', 'secret-database', 'secret-user', 'secret-password'), driver=driver)
        events = []
        with progress_scope(ProgressReporter(events.append)):
            reader.verify(self.registry)
        completed = next(event for event in events if event['stage'] == 'query' and event['state'] == 'completed')
        self.assertEqual(driver.query, completed['details']['cypher'])
        self.assertEqual(driver.parameters, completed['details']['parameters'])
        self.assertGreaterEqual(completed['duration_ms'], 0)
        self.assertNotIn('secret-', json.dumps(events))
        self.assertEqual(1, driver.calls)

    def test_ndjson_actual_events_result_and_json_compatibility(self):
        facade = SemanticAssistant(self.engine, TypedModel(course('컴퓨터구조')))
        with patch.object(api, '_engine', return_value=self.engine), patch('academic_assistant.assistant.SemanticAssistant', return_value=facade):
            client = TestClient(api.app)
            body = request('컴구 학점').model_dump()
            streamed = client.post('/v1/academic/assistant', json=body, headers={'Accept': 'application/x-ndjson'})
            ordinary = client.post('/v1/academic/assistant', json=body)
            bad = client.post('/v1/academic/assistant', json={'question': ''}, headers={'Accept': 'application/x-ndjson'})
        self.assertEqual(200, streamed.status_code)
        self.assertIn('application/x-ndjson', streamed.headers['content-type'])
        rows = [json.loads(line) for line in streamed.text.splitlines()]
        self.assertEqual('result', rows[-1]['type'])
        self.assertEqual(ordinary.json(), rows[-1]['response'])
        events = rows[:-1]
        self.assertEqual(list(range(1, len(events) + 1)), [row['sequence'] for row in events])
        self.assertEqual(sorted(row['elapsed_ms'] for row in events), [row['elapsed_ms'] for row in events])
        schema_keys = {'schema_version','type','request_id','sequence','stage','state','elapsed_ms','duration_ms','message','explanation_source','details'}
        self.assertTrue(all(set(row) == schema_keys for row in events))
        self.assertTrue({'received', 'intent', 'retrieval', 'query', 'writing', 'verification', 'complete'} <= {row['stage'] for row in events})
        self.assertTrue(any(row['details'].get('backend') == 'registry' for row in events))
        self.assertFalse(any('cypher' in row['details'] for row in events))
        self.assertEqual(422, bad.status_code)
        self.assertIn('application/json', bad.headers['content-type'])

    def test_stream_internal_failure_is_sanitized_without_second_call(self):
        facade = unittest.mock.Mock()
        facade.chat.side_effect = RuntimeError('secret-password backend URI')
        with patch.object(api, '_engine', return_value=self.engine), patch('academic_assistant.assistant.SemanticAssistant', return_value=facade):
            response = TestClient(api.app).post('/v1/academic/assistant', json=request('컴구').model_dump(), headers={'Accept':'application/x-ndjson'})
        row = json.loads(response.text)
        self.assertEqual('error', row['type'])
        self.assertNotIn('secret', response.text)
        self.assertEqual(1, facade.chat.call_count)

    def test_identifying_email_phone_are_transient_not_academic_numbers(self):
        prose = '홍길동님 연락처 010-1234-5678, qa2026@example.invalid. 컴퓨터구조는 3학점입니다.'
        model = TypedModel(course('컴퓨터구조'), writer=replace_text(prose))
        result = self.ask('qa2026@example.invalid 010-1234-5678 컴구 학점', model)
        self.assertEqual(('supported', 'generated'), (result.status, result.generation_status))
        self.assertEqual(prose, result.answer)
        self.assertNotIn('qa2026', json.dumps(model.payloads[1], ensure_ascii=False))
        self.assertNotIn('010-1234', json.dumps(model.payloads[1], ensure_ascii=False))

    def test_progress_does_not_publish_model_academic_or_prejudged_failure_claims(self):
        for bad in ('컴퓨터구조는 9학점입니다.', '컴퓨터구조는 전공선택입니다.',
                    'qa@example.invalid의 요청을 확인합니다.', '학점이 틀렸기 때문에 수정합니다.',
                    '검증이 성공했습니다.'):
            model = TypedModel(course('컴퓨터구조'))
            model.plan_document['explanation'] = bad
            model.plan_document['explanations'] = {'repair': bad, 'writing': bad}
            events = []
            result = SemanticAssistant(self.engine, model).chat(request('컴구 학점'), ProgressReporter(events.append))
            self.assertEqual('generated', result.generation_status)
            self.assertFalse(any(bad in row['message'] for row in events), bad)

    def test_typed_coverage_rejects_missing_facet_purpose_filter_and_rule(self):
        cases = [
            ([course('컴퓨터구조')], [course('컴퓨터구조', properties=('credits','offering'))]),
            ([course('컴퓨터구조')], [course('컴퓨터구조', purpose='completion_obligation', properties=('credits',))]),
            ([course(properties=('count',), category='major_required')],
             [course(properties=('count',), category='major_required'), course(properties=('count',), category='major_elective')]),
            ([dict(kind='rule', intent_ids=['credits.graduation.total'])],
             [dict(kind='rule', intent_ids=['credits.graduation.total','credits.general.total'])]),
            ([dict(kind='rule', intent_ids=['credits.graduation.total'])], [dict(kind='requirements_overview')]),
            ([dict(kind='requirements_overview')], [dict(kind='requirements_overview'),course('컴퓨터구조')]),
        ]
        for tools, demands in cases:
            model = TypedModel(*tools)
            model.plan_document['coverage'] = demands
            result = self.ask('질문 전체를 해석해주세요', model)
            self.assertEqual(('insufficient_evidence','processing_unavailable','rejected'),
                             (result.status,result.reason_code,result.plan_status))
            self.assertEqual(1, len(model.payloads))

    def test_typed_coverage_is_required_but_untyped_compatibility_is_explicit(self):
        model = TypedModel(course('컴퓨터구조'))
        del model.plan_document['coverage']
        self.assertEqual('processing_unavailable', self.ask('컴구 학점', model).reason_code)
        document = SemanticDocument(dict(requests=[dict(kind='greeting')],context_used=False))
        self.assertEqual('greeting', _plan(document, self.registry, None)['requests'][0]['kind'])

    def test_complete_typed_overview_named_attribute_and_split_facets_remain_supported(self):
        cases = [
            ([dict(kind='requirements_overview'),course('컴퓨터구조')],None),
            ([course('컴퓨터구조'),course('컴퓨터구조',properties=('offering',))],
             [course('컴퓨터구조',properties=('credits','offering'))]),
            ([course('컴퓨터구조',purpose='completion_obligation',properties=('credits',))],None),
            ([course(properties=('count',),category='major_required'),course(properties=('count',),category='major_elective')],None),
        ]
        for tools,coverage in cases:
            model=TypedModel(*tools,writer=purpose_natural)
            if coverage is not None:
                model.plan_document['coverage']=coverage
            result=self.ask('여러 요청 항목을 확인해주세요',model)
            self.assertEqual(('supported','generated'),(result.status,result.generation_status),str(tools))
            if any(tool.get('purpose')=='completion_obligation' for tool in tools):
                self.assertIn('이수 의무',result.context_question)
                self.assertIn('학점',result.context_question)

    def test_execution_cannot_claim_supported_after_tool_drops_facet(self):
        facade = SemanticAssistant(self.engine, TypedModel(course('컴퓨터구조', properties=('credits','offering'))))
        original = facade._courses
        def partial(filters, index, properties=None):
            return original(filters,index,['credits'] if properties is not None else None)
        with patch.object(facade,'_courses',side_effect=partial):
            result = facade.chat(request('컴구 정보'))
        self.assertEqual(('insufficient_evidence','processing_unavailable'), (result.status,result.reason_code))

    def test_production_schema_coverage_binding_and_planner_token_bound(self):
        from academic_assistant.semantic_llm import PLAN_SCHEMA
        self.assertIn('coverage', PLAN_SCHEMA['required'])
        self.assertEqual(PLAN_SCHEMA['properties']['coverage']['items'],PLAN_SCHEMA['properties']['requests']['items'])
        session = _Session(SemanticLLMClient(llm.LLMSettings('ollama','http://127.0.0.1:11434','synthetic')))
        captured = []
        def wire(req, remaining):
            captured.append(json.loads(req.data))
            return json.dumps({'done':True,'message':{'content':json.dumps({'coverage':[{'kind':'greeting'}], 'requests':[{'kind':'greeting'}],'context_used':False})}}).encode()
        with patch.object(session.client,'_post',side_effect=wire):
            plan = session.plan({'question':'안녕','previous_question':None,'catalog':{}})
        self.assertTrue(plan.typed_plan)
        self.assertEqual(512,captured[0]['options']['num_predict'])
        self.assertEqual({'const':False},captured[0]['format']['properties']['context_used'])
        self.assertEqual('greeting',_plan(plan,self.registry,None)['coverage'][0]['kind'])

    def test_prerequisite_direction_rejects_reverse_and_extra_reverse_relation(self):
        facts = self.fact('operations.graduation-work-prerequisite')
        caveat = ' 이 선행조건만으로 졸업 가능 여부나 학점 취득을 판정하지 않습니다.'
        valid = '캡스톤디자인 II를 PASS한 후 졸업작품을 수강해야 합니다.' + caveat
        _verify_rule_text(valid,facts)
        for wrong in ('캡스톤디자인 II를 수강하려면 졸업작품을 PASS해야 합니다.' + caveat,
                      facts[0]['statement'] + ' 캡스톤디자인 II 수강에는 졸업작품 PASS가 조건입니다.',
                      facts[0]['statement'] + ' 캡스톤디자인 II를 미이수해도 졸업작품을 수강할 수 있습니다.'):
            with self.assertRaises(llm.LLMInvalidResponse,msg=wrong):
                _verify_rule_text(wrong,facts)

    def test_mixed_category_list_binds_every_extra_named_attribute(self):
        _,spec = SemanticAssistant(self.engine)._courses({},1,['count'])
        def doc(text):
            return SemanticDocument({'sections':[{'part_id':'p1','text':text,'fact_ids':[fact['fact_id'] for fact in spec['facts']]}]})
        _write(doc('확인된 목록은 43과목입니다. 컴퓨터구조는 전공필수입니다.'),[spec])
        for bad in ('확인된 목록은 43과목입니다. 컴퓨터구조는 전공선택입니다.',
                    '확인된 목록은 43과목입니다. 컴구는 전공선택입니다.',
                    '확인된 목록은 43과목입니다. 컴퓨터구조는 1학년 첫 학기에 편성됩니다.'):
            with self.assertRaises(llm.LLMInvalidResponse,msg=bad):
                _write(doc(bad),[spec])

    def test_generic_following_sentence_preserves_source_subject(self):
        for bad in ('컴퓨터구조는 3학점입니다. 해당 과목의 편성은 1학년 1학기입니다.',
                    '컴퓨터구조는 3학점입니다. 편성은 1학년 첫 학기입니다.',
                    '컴퓨터구조는 3학점입니다. 이 과목은 전공선택입니다.',
                    '컴퓨터구조는 3학점입니다. 해당 과목은 3학년 첫 학기에 편성되지 않습니다.',
                    '컴퓨터구조는 3학점입니다. 양자역학 3학년 1학기 편성입니다.'):
            result = self.ask('컴구 학점',TypedModel(course('컴퓨터구조'),writer=replace_text(bad)))
            self.assertEqual('processing_unavailable',result.reason_code,bad)
        valid='컴퓨터구조는 3학점입니다. 해당 과목은 3학년 첫 학기에 편성됩니다.'
        self.assertEqual('generated',self.ask('컴구 학점',TypedModel(course('컴퓨터구조'),writer=replace_text(valid))).generation_status)

    def test_course_subject_multiple_credit_predicates_not_number_set_only(self):
        _,spec = SemanticAssistant(self.engine)._courses({},1,['count'])
        varying = next(fact for fact in spec['source_facts'] if fact['credits'] != 3)
        text = f"목록은 43과목입니다. 컴퓨터구조는 {varying['credits']}학점이고 {varying['course_name']}은 3학점입니다."
        with self.assertRaises(llm.LLMInvalidResponse):
            _write(SemanticDocument({'sections':[{'part_id':'p1','text':text,'fact_ids':[fact['fact_id'] for fact in spec['facts']]}]}),[spec])

    def test_noun_conjunctions_not_departments_but_real_entities_stay_guarded(self):
        for noun in ('이름과','의견과','구성과','학점과'):
            model=TypedModel(course('컴퓨터구조'))
            result=self.ask('컴퓨터구조의 '+noun+' 관련 정보를 확인해주세요',model)
            self.assertEqual('supported',result.status,noun)
            self.assertEqual(2,len(model.payloads))
        for dept in ('전자과','경영학과','소프트웨어공학과','학과: 새로운과'):
            model=TypedModel(dict(kind='requirements_overview'))
            self.assertEqual('out_of_scope',self.ask(dept+' 학생 졸업요건',model).status,dept)
            self.assertEqual([],model.payloads)

    def test_accept_media_type_and_quality_are_exact(self):
        positive = ['application/x-ndjson','APPLICATION/X-NDJSON; q=0.5','application/json, application/x-ndjson;q=1']
        negative = ['application/x-ndjson;q=0','application/x-ndjson-ish','*/x-ndjson','application/x-ndjson;q=nan',
                    'application/x-ndjson;q=inf','application/x-ndjson;q=2','application/x-ndjson;q=bad',
                    'application/x-ndjson;q=0;q=1']
        for header in positive+negative:
            self.assertEqual(header in positive,accepts_ndjson([(b'accept',header.encode())]),header)
        facade=SemanticAssistant(self.engine,TypedModel(course('컴퓨터구조')))
        with patch.object(api,'_engine',return_value=self.engine),patch('academic_assistant.assistant.SemanticAssistant',return_value=facade):
            for header in ('application/x-ndjson;q=0','application/x-ndjson-ish'):
                response=TestClient(api.app).post('/v1/academic/assistant',json=request('컴구 학점').model_dump(),headers={'Accept':header})
                self.assertIn('application/json',response.headers['content-type'])

    def test_progress_full_action_grammar_rejects_word_numerals_and_embedded_claims(self):
        bads = [
            '컴퓨터구조는 아홉 학점으로 알고 있으며 질문을 확인합니다.',
            '컴퓨터구조는 네 학점이라고 생각하면서 질문을 살펴봅니다.',
            '컴퓨터구조의 학점이 많다는 점을 확인합니다.',
            '졸업작품을 먼저 PASS하면 캡스톤을 들을 수 있음을 확인합니다.',
            '학점을 확인합니다. 졸업할 수 있다는 정보를 살펴봅니다.',
            '학생의 학번을 확인합니다.',
        ]
        for bad in bads:
            model=TypedModel(course('컴퓨터구조'))
            model.plan_document['explanation']=bad
            events=[]
            result=SemanticAssistant(self.engine,model).chat(request('컴구 학점'),ProgressReporter(events.append))
            self.assertEqual('generated',result.generation_status,bad)
            self.assertFalse(any(row['message']==bad for row in events),bad)
        for good in ('질문 전체의 목적과 항목을 해석합니다.', '요청한 정보를 확인하고 관련 근거를 점검하겠습니다.'):
            model=TypedModel(course('컴퓨터구조'))
            model.plan_document['explanation']=good
            events=[]
            SemanticAssistant(self.engine,model).chat(request('컴구 학점'),ProgressReporter(events.append))
            self.assertTrue(any(row['message']==good and row['explanation_source']=='llm' for row in events),good)

    def test_relative_clause_and_topic_prerequisites_supported_but_reverse_not(self):
        caveat=' 졸업 가능 여부를 판정하지 않습니다. 학점 취득을 판정하지 않습니다.'
        good = [
            '졸업작품은 캡스톤디자인 II를 PASS한 학생만 수강할 수 있습니다.',
            '캡스톤디자인 II는 졸업작품을 수강하려면 PASS해야 하는 선행조건입니다.',
            '졸업작품의 수강 조건은 캡스톤디자인 II의 PASS입니다.',
        ]
        bad = [
            '캡스톤디자인 II는 졸업작품을 PASS한 학생만 수강할 수 있습니다.',
            '졸업작품은 캡스톤디자인 II를 PASS하지 않은 학생도 수강할 수 있습니다.',
        ]
        tool=dict(kind='rule',intent_ids=['operations.graduation-work-prerequisite'])
        for text in good+bad:
            writer=replace_text(text+caveat)
            model=CorrectingModel(tool,first=writer,corrected=writer)
            result=self.ask('졸업작품의 수강 선행조건을 알려줘',model)
            self.assertEqual('supported' if text in good else 'insufficient_evidence',result.status,text)
            self.assertEqual('generated' if text in good else 'fallback',result.generation_status,text)
            if text in good:
                facade=SemanticAssistant(self.engine,TypedModel(tool,writer=writer))
                with patch.object(api,'_engine',return_value=self.engine),patch('academic_assistant.assistant.SemanticAssistant',return_value=facade):
                    response=TestClient(api.app).post('/v1/academic/assistant',json=request('졸업작품 수강 기준').model_dump())
                self.assertEqual(('supported','generated'),(response.json()['status'],response.json()['generation_status']),text)

    def test_unknown_short_department_roles_and_instrumental_admission_refuse(self):
        for department in ('통계과','가람과','융합콘텐츠과','해양과'):
            for role in (' 학생인데','에 다니는 학생인데','로 편입한 학생인데',' 신입생인데','의 졸업생인데','학생인데'):
                model=TypedModel(dict(kind='requirements_overview'))
                result=self.ask(department+role+' 졸업요건을 알려줘',model)
                self.assertEqual(('out_of_scope','unsupported_scope'),(result.status,result.reason_code),department+role)
                self.assertEqual([],model.payloads)
        for frame in ('소프트웨어공학으로 전과한 학생의 졸업요건','소프트웨어공학으로 입학한 학생의 졸업기준'):
            model=TypedModel(dict(kind='requirements_overview'))
            self.assertEqual('out_of_scope',self.ask(frame,model).status)
            self.assertEqual([],model.payloads)
        for text in ('컴퓨터공학과 학생인데 졸업요건','컴퓨터구조의 이름과 학점','소프트웨어공학의 구성과 학점'):
            tool=dict(kind='requirements_overview') if '학생' in text else course('소프트웨어공학' if '소프트웨어' in text else '컴퓨터구조')
            self.assertEqual('supported',self.ask(text,TypedModel(tool,writer=purpose_natural)).status,text)

    def test_truthful_negative_category_corrections_bind_subject_and_preserve_credits(self):
        correct = [
            '컴퓨터구조는 전공선택이 아니라 전공필수이고 3학점입니다.',
            '컴퓨터구조는 3학점이며 전공선택이 아닌 전공필수 과목입니다.',
            '컴퓨터구조는 전공선택은 아닙니다. 컴퓨터구조는 전공필수로 3학점입니다.',
        ]
        for text in correct:
            result=self.ask('컴구 학점',TypedModel(course('컴퓨터구조'),writer=replace_text(text)))
            self.assertEqual(('supported','generated'),(result.status,result.generation_status),text)
            self.assertIn(text,result.answer)
        for bad in ('컴퓨터구조는 전공필수가 아니라 전공선택이고 3학점입니다.',
                    '컴퓨터구조는 전공선택이 아니라 전공필수이고 3학점이 아닙니다.',
                    '컴퓨터구조는 전공선택이 아니라 전공필수이고 3학점입니다. 컴퓨터구조는 전공선택입니다.'):
            result=self.ask('컴구 학점',TypedModel(course('컴퓨터구조'),writer=replace_text(bad)))
            self.assertEqual('processing_unavailable',result.reason_code,bad)

    def test_source_multiyear_and_both_semester_enumerations_are_not_unknown(self):
        rows=self.registry.catalogue['courses']
        examples=[next(row for row in rows if len(row['offering_years'])>1),
                  next(row for row in rows if len(row['offering_semesters'])>1 and 'S' not in row['offering_semesters'])]
        for row in examples:
            year='·'.join(map(str,row['offering_years']))+'학년'
            term='계절학기' if row['offering_semesters']==['S'] else ','.join(row['offering_semesters'])+'학기'
            text=row['course_name']+'는 '+year+' '+term+' 편성입니다.'
            result=self.ask('편성 정보를 확인해주세요',TypedModel(course(row['course_name'],properties=('offering',)),writer=replace_text(text)))
            self.assertEqual(('supported','generated'),(result.status,result.generation_status),text)

    def test_actual_provider_length_reason_rejects_even_complete_json_no_retry(self):
        settings=llm.LLMSettings('ollama','http://127.0.0.1:11434','synthetic',timeout_seconds=1)
        client=SemanticLLMClient(settings)
        wire=[]
        document={'coverage':[course('컴퓨터구조')],'requests':[course('컴퓨터구조')],'context_used':False}
        def post(req,remaining):
            wire.append(json.loads(req.data))
            return json.dumps({'done':True,'done_reason':'length','message':{'content':json.dumps(document)}}).encode()
        with llm._budget.lock:
            llm._budget.active=False;llm._budget.next_allowed=0
        try:
            with patch.object(client,'_post',side_effect=post):
                result=SemanticAssistant(self.engine,client).chat(request('컴구 학점'))
            self.assertEqual(('insufficient_evidence','processing_unavailable','rejected'),(result.status,result.reason_code,result.plan_status))
            self.assertEqual(1,len(wire))
            self.assertEqual(8192,wire[0]['options']['num_ctx'])
            self.assertEqual(512,wire[0]['options']['num_predict'])
            self.assertFalse(llm._budget.active)
            self.assertNotIn('3학점',result.answer)
            self.assertEqual(3,result.parts[0].course_evidence.courses[0].credits)
        finally:
            with llm._budget.lock:
                llm._budget.active=False;llm._budget.next_allowed=0

    def test_complete_compound_provider_plan_at_8192_retains_all_facets(self):
        client=SemanticLLMClient(llm.LLMSettings('ollama','http://127.0.0.1:11434','synthetic'))
        session=_Session(client)
        tools=[dict(kind='requirements_overview'),course('컴퓨터구조',properties=('credits','category')),
               course(properties=('count',),category='major_required'),course(properties=('count',),category='major_elective')]
        document={'coverage':deepcopy(tools),'requests':tools,'context_used':False}
        captured=[]
        def post(req,remaining):
            captured.append(json.loads(req.data))
            return json.dumps({'done':True,'done_reason':'stop','message':{'content':json.dumps(document)}}).encode()
        with patch.object(client,'_post',side_effect=post):
            result=session.plan({'question':'복합 요청 전체를 확인해주세요','previous_question':None,'catalog':SemanticAssistant(self.engine)._public_catalog()})
        parsed=_plan(result,self.registry,None)
        self.assertEqual(tools,parsed['coverage'])
        self.assertEqual(tools,parsed['requests'])
        self.assertEqual(8192,captured[0]['options']['num_ctx'])
        self.assertEqual(512,captured[0]['options']['num_predict'])
        self.assertLess(len(json.dumps(document).encode()),8192)

    def test_actual_rewrite_explanation_from_same_call_binds_observed_error(self):
        explanation='조회한 근거와 문장 수치를 비교하고 표현을 교정합니다.'
        def corrected(payload):
            document=bound_prose(payload)
            document['explanation']=explanation
            return document
        model=CorrectingModel(course('컴퓨터구조'),first=replace_text('컴퓨터구조는 9학점입니다.'),corrected=corrected)
        predicted='요청한 문장을 다시 교정합니다.'
        model.plan_document['explanations']={'repair':predicted}
        events=[]
        result=SemanticAssistant(self.engine,model).chat(request('컴구 학점'),ProgressReporter(events.append))
        self.assertEqual(('supported','generated'),(result.status,result.generation_status))
        repaired=[row for row in events if row['stage']=='repair' and row['explanation_source']=='llm']
        self.assertEqual([explanation],[row['message'] for row in repaired])
        self.assertEqual({'attempt':1,'error_code':'unsupported_number'},repaired[0]['details'])
        self.assertEqual(1,len(model.repairs))
        self.assertEqual(model.payloads[1],model.repairs[0][0])
        self.assertEqual('컴퓨터구조는 9학점입니다.',model.repairs[0][1].document['sections'][0]['text'])
        self.assertFalse(any(row['message']==predicted for row in events))

    def test_unsafe_or_absent_rewrite_explanation_drops_without_extra_inference(self):
        for explanation in (None,'컴퓨터구조는 아홉 학점으로 알고 있으며 문장을 교정합니다.',
                            '서버 내부의 오류 때문에 재작성합니다.','교정에 성공했다고 확인합니다.'):
            def corrected(payload):
                document=bound_prose(payload)
                if explanation is not None:
                    document['explanation']=explanation
                return document
            model=CorrectingModel(course('컴퓨터구조'),first=replace_text('컴퓨터구조는 9학점입니다.'),corrected=corrected)
            events=[]
            result=SemanticAssistant(self.engine,model).chat(request('컴구 학점'),ProgressReporter(events.append))
            self.assertEqual('generated',result.generation_status)
            self.assertFalse(any(row['stage']=='repair' and row['explanation_source']=='llm' for row in events))
            self.assertEqual(1,len(model.repairs))
            self.assertEqual(2,len(model.payloads))

    def test_rewrite_operation_explanation_never_promotes_invalid_final_facts(self):
        def incorrect(payload):
            document=replace_text('컴퓨터구조는 9학점입니다.')(payload)
            document['explanation']='근거와 문장을 다시 비교하고 표현을 교정합니다.'
            return document
        model=CorrectingModel(course('컴퓨터구조'),first=incorrect,corrected=incorrect)
        events=[]
        result=SemanticAssistant(self.engine,model).chat(request('컴구 학점'),ProgressReporter(events.append))
        self.assertEqual(('insufficient_evidence','processing_unavailable','fallback'),(result.status,result.reason_code,result.generation_status))
        self.assertEqual(1,len(model.repairs))
        self.assertNotIn('9학점',result.answer)
        self.assertNotIn('3학점',result.answer)
        self.assertTrue(any(row['stage']=='repair' and row['explanation_source']=='llm' for row in events))

    def test_provider_restores_optional_rewrite_explanation_with_short_references(self):
        session=_Session(SemanticLLMClient(llm.LLMSettings('ollama','http://127.0.0.1:11434','synthetic')))
        payload=dict(version='1.0.0',parts=[dict(part_id='p1',title='과목',facts=[dict(fact_id='full-id',course_name='컴퓨터구조',credits=3)],properties=['credits'])])
        captured=[]
        explanation='문장의 수치를 비교하고 표현을 교정합니다.'
        def generate(stage,data,schema,instruction):
            captured.append((data,schema))
            return SemanticDocument({'sections':[{'part_id':'p1','fact_ids':['f1'],'text':'컴퓨터구조는 3학점입니다.'}],'explanation':explanation})
        previous=SemanticDocument({'sections':[{'part_id':'p1','fact_ids':['full-id'],'text':'컴퓨터구조는 9학점입니다.'}]})
        with patch.object(session,'_request',side_effect=generate):
            result=session.rewrite(payload,previous,'unsupported_number')
        self.assertEqual(explanation,result.document['explanation'])
        self.assertEqual(['full-id'],result.document['sections'][0]['fact_ids'])
        self.assertEqual('unsupported_number',captured[0][0]['correction']['error_code'])
        self.assertEqual(previous.document,captured[0][0]['correction']['previous_document'])
        self.assertIn('explanation',captured[0][1]['properties'])

    def test_approved_credit_qualifiers_and_nested_metric_names_bind(self):
        for intent in ('credits.general.recognition-cap','credits.general.remaining','credits.general.bundle'):
            facts=self.fact(intent)
            _verify_rule_text(' '.join(fact['statement'] for fact in facts),facts)
        cap=self.fact('credits.general.recognition-cap')
        for text in ('교양은 최소 42학점 이상 인정됩니다.','교양은 정확히 42학점 인정됩니다.'):
            with self.assertRaises(llm.LLMInvalidResponse):
                _verify_rule_text(text,cap)
        remaining=self.fact('credits.general.remaining')
        with self.assertRaises(llm.LLMInvalidResponse):
            _verify_rule_text('졸업 잔여학점을 13학점 이상 이수해야 합니다.',remaining)

    def test_separate_counting_action_retains_retroactive_individual_hold(self):
        facts=self.fact('course-counting.post-completion-equivalence')
        _verify_rule_text(facts[0]['statement'],facts)
        _verify_rule_text('이수 후 동일 과목으로 지정되어도 학점은 별도로 계산합니다. 소급 적용될 경우 개인 판정은 보류합니다.',facts)
        for text in ('이수 후 동일 과목 학점은 별도로 계산합니다.',
                     '이수 후 동일 과목 학점은 별도로 계산하지 않습니다. 소급 적용될 경우 개인 판정은 보류합니다.',
                     '학점은 무조건 별도로 계산합니다. 소급 적용도 개인 판정은 보류합니다.'):
            with self.assertRaises(llm.LLMInvalidResponse):
                _verify_rule_text(text,facts)

    def test_generic_source_offering_sentence_stays_bound_to_named_course(self):
        text='컴퓨터구조는 3학점인 전공필수 과목입니다. 원문 편성 표시는 3학년 1학기입니다.'
        result=self.ask('컴구 편성 정보',TypedModel(course('컴퓨터구조',properties=('offering',)),writer=replace_text(text)))
        self.assertEqual('generated',result.generation_status)
        for wrong in ('원문 편성 표시는 1학년 1학기입니다.','양자역학은 3학년 1학기입니다.'):
            result=self.ask('컴구 편성 정보',TypedModel(course('컴퓨터구조',properties=('offering',)),writer=replace_text('컴퓨터구조는 3학년 1학기 편성입니다. '+wrong)))
            self.assertEqual('processing_unavailable',result.reason_code)

    def test_possessive_course_property_heads_bind_only_known_source_subjects(self):
        for name in ('데이타베이스개론','컴퓨터구조','운영체제'):
            row=next(item for item in self.registry.catalogue['courses'] if item['course_name']==name)
            term=row['offering_semesters'][0]
            for head,props,value in (('의 학점 정보',('credits',),str(row['credits'])+'학점'),
                                     ('의 교육과정 편성 정보',('offering',),str(row['offering_years'][0])+'학년 '+term+'학기'),
                                     ('의 이수구분 정보',('category',),'전공필수' if row['category']=='major_required' else '전공선택')):
                text=name+head+'는 '+value+'입니다.'
                result=self.ask('과목 속성 확인',TypedModel(course(name,properties=props),writer=replace_text(text)))
                self.assertEqual('generated',result.generation_status,text)
        for wrong in ('컴퓨터구조의 학점 정보는 9학점입니다.',
                      '컴퓨터구조의 학점 정보는 3학점입니다. 양자역학의 학점 정보는 3학점입니다.'):
            self.assertEqual('processing_unavailable',self.ask('컴구 학점',TypedModel(course('컴퓨터구조'),writer=replace_text(wrong))).reason_code)

    def test_scope_affiliation_participles_and_possessive_heads_precede_planner(self):
        for department in ('가람과','통계과','기계과'):
            for tail in (' 소속 학생의 졸업요건','에 속한 학생의 졸업요건','에 재학 중인 학생의 졸업요건',
                         ' 소속인 재학생의 졸업요건',' 의 졸업 요건','의 졸업 기준'):
                model=TypedModel(dict(kind='requirements_overview'))
                self.assertEqual('out_of_scope',self.ask(department+tail,model).status,department+tail)
                self.assertEqual([],model.payloads)
        for text in ('컴구의 이름과 학점','컴구의 의견과 학점','컴구의 구성과 학점','과목 이름 김민수'):
            tools=course(properties=('names',)) if text.startswith('과목 이름') else course('컴퓨터구조')
            self.assertEqual('supported',self.ask(text,TypedModel(tools)).status,text)

    def test_prerequisite_necessary_case_and_concession_are_separate(self):
        facts=self.fact('operations.graduation-work-prerequisite')
        caveat=' 졸업 가능 여부를 판정하지 않습니다. 학점 취득을 판정하지 않습니다.'
        for condition in ('PASS한 경우에만','PASS하는 경우에만','PASS했을 때만','PASS한 경우에 한하여'):
            _verify_rule_text('졸업작품은 캡스톤디자인 II를 '+condition+' 수강할 수 있습니다.'+caveat,facts)
        for condition in ('PASS 여부와 관계없이','PASS하든 하지 않든','PASS했든 말든','PASS든 아니든'):
            with self.assertRaises(llm.LLMInvalidResponse):
                _verify_rule_text('졸업작품을 수강하려면 캡스톤디자인 II를 '+condition+' 신청할 수 있습니다.'+caveat,facts)
            with self.assertRaisesRegex(llm.LLMInvalidResponse,'negated the prerequisite'):
                _verify_rule_text('캡스톤디자인 II의 '+condition+' 졸업작품을 수강하려면 신청합니다.'+caveat,facts)
        _verify_rule_text('졸업작품을 수강하려면 캡스톤디자인 II를 PASS해야 하지만 졸업 판정 여부와 무관하게 학점 취득을 판정하지 않습니다. 졸업 가능 여부를 판정하지 않습니다.',facts)

    def test_classification_negation_binds_actual_and_opposite_category(self):
        for text in ('컴퓨터구조는 전공선택으로 분류되지 않고 전공필수인 3학점 과목입니다.',
                     '컴퓨터구조는 전공선택으로 지정된 것이 아니라 전공필수이며 3학점입니다.'):
            self.assertEqual('generated',self.ask('컴구 이수구분',TypedModel(course('컴퓨터구조',properties=('category',)),writer=replace_text(text))).generation_status,text)
        for text in ('컴퓨터구조는 전공필수로 분류되지 않고 전공선택인 3학점 과목입니다.',
                     '컴퓨터구조는 전공선택으로 분류되지 않고 전공필수인 9학점 과목입니다.'):
            self.assertEqual('processing_unavailable',self.ask('컴구 이수구분',TypedModel(course('컴퓨터구조',properties=('category',)),writer=replace_text(text))).reason_code,text)

    def test_prerequisite_policy_participants_do_not_require_extra_course_lookup(self):
        rule=dict(kind='rule',intent_ids=['operations.graduation-work-prerequisite'])
        result=self.ask('캡스톤디자인 II PASS와 졸업작품 수강 선행조건을 알려줘',TypedModel(rule,writer=purpose_natural))
        self.assertEqual(('supported','generated'),(result.status,result.generation_status))
        self.assertTrue(all(part.course_evidence is None for part in result.parts))
        model=TypedModel(rule)
        model.plan_document['coverage'].append(course('산학캡스톤디자인II',properties=('credits',)))
        result=self.ask('캡스톤디자인 II의 선행조건과 학점',model)
        self.assertEqual('processing_unavailable',result.reason_code)
        result=self.ask('선행조건과 과목 학점',TypedModel(rule,course('산학캡스톤디자인II',properties=('credits',)),writer=purpose_natural))
        self.assertEqual('supported',result.status)
        self.assertTrue(any(part.course_evidence is not None for part in result.parts))

    def test_actual_repair_explanation_corrects_answer_not_source(self):
        for explanation in ('컴퓨터구조 답변의 수치를 확인하고 문장을 교정합니다.',
                            '컴구의 답변 표현을 다시 바로잡습니다.'):
            def corrected(payload):
                return {**bound_prose(payload),'explanation':explanation}
            model=CorrectingModel(course('컴퓨터구조'),first=replace_text('컴퓨터구조는 9학점입니다.'),corrected=corrected)
            events=[]
            result=SemanticAssistant(self.engine,model).chat(request('컴구 학점'),ProgressReporter(events.append))
            self.assertEqual('generated',result.generation_status)
            self.assertTrue(any(row['stage']=='repair' and row['message']==explanation and row['explanation_source']=='llm' for row in events),explanation)
        for explanation in ('근거의 수치를 바로잡습니다.','원문의 오류를 교정합니다.',
                            '근거와 문장의 수치를 교정합니다.','답변에서 근거의 오류를 수정합니다.'):
            def corrected(payload):
                return {**bound_prose(payload),'explanation':explanation}
            model=CorrectingModel(course('컴퓨터구조'),first=replace_text('컴퓨터구조는 9학점입니다.'),corrected=corrected)
            events=[]
            result=SemanticAssistant(self.engine,model).chat(request('컴구 학점'),ProgressReporter(events.append))
            self.assertEqual('generated',result.generation_status)
            self.assertFalse(any(row['message']==explanation for row in events),explanation)


class DisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_saturation_has_guaranteed_terminal_and_no_hang(self):
        from starlette.requests import Request
        engine = AnswerEngine(Registry.load())
        result = SemanticAssistant(engine, TypedModel(course('컴퓨터구조'))).chat(request('컴구 학점'))
        finished = Event()
        class BurstingAssistant:
            def chat(self, req, progress=None):
                try:
                    for _ in range(160):
                        progress.emit('query','info','읽기 전용 조회를 확인합니다.')
                    return result
                finally:
                    finished.set()
        scope={'type':'http','method':'POST','path':'/v1/academic/assistant','headers':[(b'accept',b'application/x-ndjson')]}
        with patch.object(api,'_engine',return_value=engine),patch('academic_assistant.assistant.SemanticAssistant',return_value=BurstingAssistant()):
            response=await api.semantic_turn(request('컴구 학점'),Request(scope))
            iterator=response.body_iterator
            first=await asyncio.wait_for(anext(iterator),1)
            await asyncio.sleep(.18)  # actual transport backpressure, not model work
            async def drain():
                return [first]+[row async for row in iterator]
            rows=[json.loads(row) for row in await asyncio.wait_for(drain(),2)]
        self.assertTrue(finished.is_set())
        self.assertEqual('error',rows[-1]['type'])
        self.assertEqual(1,sum(row['type'] in {'result','error'} for row in rows))
        self.assertNotIn('None',str(rows[-1]))
        self.assertLess(len(rows),161)

    async def test_asgi_disconnect_waits_actual_worker_finally(self):
        from starlette.requests import Request
        engine = AnswerEngine(Registry.load())
        result = SemanticAssistant(engine, TypedModel(course('컴퓨터구조'))).chat(request('컴구 학점'))
        release, finished = Event(), Event()
        disconnect = asyncio.Event()
        class BlockingAssistant:
            def chat(self, req, progress=None):
                progress.emit('received', 'started', '요청을 확인합니다.')
                try:
                    release.wait(1)
                    return result
                finally:
                    finished.set()
        scope = {'type':'http', 'asgi':{'spec_version':'2.0'}, 'method':'POST', 'path':'/v1/academic/assistant',
                 'headers':[(b'accept', b'application/x-ndjson')], 'query_string':b''}
        with patch.object(api, '_engine', return_value=engine), patch('academic_assistant.assistant.SemanticAssistant', return_value=BlockingAssistant()):
            response = await api.semantic_turn(request('컴구 학점'), Request(scope))
            async def receive():
                await disconnect.wait()
                return {'type':'http.disconnect'}
            async def send(message):
                if message['type'] == 'http.response.body' and message.get('body'):
                    disconnect.set()
            task = asyncio.create_task(response(scope, receive, send))
            try:
                await asyncio.wait_for(disconnect.wait(), 1)
                await asyncio.sleep(.02)
                self.assertFalse(task.done(), 'HTTP capacity released before producer completed')
                self.assertFalse(finished.is_set())
            finally:
                release.set()
                await asyncio.wait_for(task, 1)
            self.assertTrue(finished.is_set())


if __name__ == '__main__':
    unittest.main()
