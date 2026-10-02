"""Integrated catalogue, graph, real synthetic PDF and public producer boundaries."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from academic_assistant import api
from academic_assistant.assistant_models import AssistantPart,AssistantTurnResponse
from academic_assistant.core import AnswerEngine
from academic_assistant.courses import load_catalogue,retrieve_courses,resolve_course_mentions
from academic_assistant.evidence_pdf import EvidencePdfService,_cited_pages
from academic_assistant.kg import build_graph
from academic_assistant.neo4j_evidence import _decode
from academic_assistant.public_demo import PublicDemo
from academic_assistant.registry import Registry,RegistryUnavailable,canonical_sha256
from academic_assistant.transcript_examples import example_pdf,recognize_example
from scripts.knowledge.build_graph import _neo4j_properties

ROOT=Path(__file__).resolve().parents[1]


class SemanticIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry=Registry.load(ROOT); cls.engine=AnswerEngine(cls.registry)

    def test_literal_catalogue_counts_category_and_exact_location(self):
        facts=self.registry.catalogue['courses']
        self.assertEqual(43,len(facts)); self.assertEqual(9,sum(f['category']=='major_required' for f in facts))
        self.assertEqual(34,sum(f['category']=='major_elective' for f in facts))
        for fact in facts:
            with self.subTest(code=fact['course_code']):
                self.assertEqual(fact['fact_sha256'],canonical_sha256({k:v for k,v in fact.items() if k!='fact_sha256'}))
                self.assertTrue(set(_cited_pages(fact['locator'])) <= {262,263})
        self.assertEqual({262:254},_cited_pages(next(f for f in facts if f['course_code']=='CDA0016')['locator']))
        self.assertEqual({262:23,263:20},{page:sum(set(_cited_pages(f['locator']))=={page} for f in facts) for page in (262,263)})

    def test_names_aliases_codes_and_spacing_retrieve_same_fact(self):
        for spelling,code,credits in [('컴퓨터 구조','CDA0016',3),('컴구','CDA0016',3),('운체','CDA0017',3),
                ('데이터베이스 개론','CDA0065',3),('웹프로그래밍','CDA0163',3),('캡디1','CDA0167',0),('산학캡스톤디자인II','CDA0168',3)]:
            with self.subTest(spelling=spelling):
                packet=retrieve_courses(self.engine,{'name':spelling})
                self.assertEqual('supported',packet.status); self.assertEqual(code,packet.courses[0].course_code)
                self.assertEqual(credits,packet.courses[0].credits)
                self.assertEqual(packet.courses[0].course_id,packet.evidence[0].course_id)

    def test_filters_preserve_multi_year_and_both_semesters(self):
        for year in (2,3,4):
            for semester in (1,2):
                codes={f.course_code for f in retrieve_courses(self.engine,{'year':year,'semester':semester}).courses}
                self.assertTrue({'CDA0147','CDA0173','CDA0088'}<=codes)
        for semester in (1,2):
            codes={f.course_code for f in retrieve_courses(self.engine,{'year':2,'semester':semester}).courses}
            self.assertTrue({'CDA0155','CDA0156'}<=codes)
        seasonal=retrieve_courses(self.engine,{'name':'현장실습1'}).courses[0]
        self.assertEqual([2,3,4],seasonal.offering_years); self.assertEqual(['S'],seasonal.offering_semesters)

    def test_course_mentions_reuse_identity_aliases_without_substring_subjects(self):
        self.assertEqual({'CDA0016','CDA0017'},resolve_course_mentions(self.registry,'컴구와 운체는 각각 몇 학점?'))
        self.assertEqual({'CDA0143'},resolve_course_mentions(self.registry,'고급자료구조의 학점'))
        self.assertEqual({'CDA0168'},resolve_course_mentions(self.registry,'산학캡스톤디자인II 이수 구분'))

    def test_missing_course_is_real_catalogue_absence_not_supported(self):
        result=retrieve_courses(self.engine,{'name':'없는과목'})
        self.assertEqual('insufficient_evidence',result.status); self.assertFalse(result.courses); self.assertFalse(result.evidence)

    def test_tampered_catalogue_or_pin_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'knowledge').mkdir(); (root/'config').mkdir()
            (root/'knowledge/course-catalogue.json').write_bytes((ROOT/'knowledge/course-catalogue.json').read_bytes())
            (root/'config/course-catalogue-pin.json').write_bytes((ROOT/'config/course-catalogue-pin.json').read_bytes())
            self.assertEqual(43,len(load_catalogue(root,self.registry.sources)['courses']))
            value=json.loads((root/'knowledge/course-catalogue.json').read_bytes()); value['courses'][0]['credits']=4
            (root/'knowledge/course-catalogue.json').write_text(json.dumps(value),encoding='utf-8')
            with self.assertRaises(RegistryUnavailable): load_catalogue(root,self.registry.sources)

    def test_graph_roundtrip_preserves_nulls_and_arrays_exactly(self):
        graph=build_graph(self.registry)
        courses=[n for n in graph['nodes'] if n['type']=='CourseFact']; self.assertEqual(43,len(courses))
        for node in graph['nodes']:
            self.assertEqual(node['properties'],_decode(_neo4j_properties(node['properties'])))
        self.assertEqual(86,sum(e['from_id'].startswith('course:') for e in graph['relationships']))

    def test_course_preview_resolves_source_not_manufactured_rule(self):
        service=EvidencePdfService(self.registry,{})
        rule,evidence,source,pages=service._citation('cwnu.cs.2026.course.cda0016',0,None)
        self.assertIn('course_fact',rule); self.assertNotIn('review',rule)
        self.assertEqual({262:254},pages); self.assertEqual(source['sha256'],self.registry.catalogue['source_sha256'])
        self.assertEqual('CDA0016 컴퓨터구조',evidence['excerpt'])
        with self.assertRaises(LookupError): service._citation('cwnu.cs.2026.course.cda0016',0,263)

    def test_three_real_pdf_examples_extract_and_compare(self):
        for identifier,count,total,gap in [('early',6,15,115),('near-graduation',45,130,0),('retake',7,9,121)]:
            with self.subTest(identifier=identifier):
                result=recognize_example(self.engine,identifier)
                self.assertEqual('pdf_text',result['extraction']['extraction_method'])
                self.assertEqual(count,len(result['extraction']['courses'])); self.assertEqual(total,result['assessment']['raw_earned_credits'])
                checks={c['check_id']:c for c in result['assessment']['checks']}
                self.assertEqual(gap,checks['credits.graduation.total']['gap'])
                self.assertEqual('not_met',checks['graduation.thesis']['result'])
                self.assertFalse(result['assessment']['official_graduation_decision'])

    def test_bad_extraction_cannot_be_replaced_by_fixture_rows(self):
        with patch('academic_assistant.transcript_examples.extract_isolated') as parse:
            parse.return_value.extraction_method='manual_required'; parse.return_value.courses=[]
            with self.assertRaises(ValueError): recognize_example(self.engine,'early')
        with self.assertRaises(KeyError): example_pdf('../private')

    def test_public_semantic_route_and_asset_are_reachable(self):
        packet=retrieve_courses(self.engine,{'name':'컴퓨터구조'})
        result=AssistantTurnResponse(packet_id='assistant-test',status='supported',kind='academic',
            answer='컴퓨터구조는 3학점 전공필수 과목입니다.',plan_status='generated',generation_status='generated',
            parts=[AssistantPart(title='과목 조회',text='컴퓨터구조는 3학점입니다.',status='supported',course_evidence=packet)])
        with patch.object(api,'_engine',return_value=self.engine), patch('academic_assistant.assistant.SemanticAssistant.chat',return_value=result),TestClient(PublicDemo(api.app),base_url='http://127.0.0.1:8765') as client:
            response=client.post('/v1/academic/assistant',json={'question':'컴퓨터구조는 몇 학점이야?','admission_year':2026,'matched_curriculum_year':2026,'department':'컴퓨터공학과'})
            self.assertEqual(200,response.status_code); self.assertEqual('supported',response.json()['status'])
            self.assertEqual(200,client.get('/assets/semantic-ui.js').status_code)
            self.assertEqual(404,client.get('/assets/private.js').status_code)

    def test_public_examples_use_real_recognizer_and_unknown_ids_closed(self):
        with patch.object(api,'_engine',return_value=self.engine), TestClient(PublicDemo(api.app),base_url='http://127.0.0.1:8765') as client:
            response=client.post('/v1/academic/transcripts/examples/early',json={})
            self.assertEqual(200,response.status_code); self.assertEqual(6,len(response.json()['extraction']['courses']))
            self.assertTrue(client.get('/v1/academic/transcripts/examples/early.pdf').content.startswith(b'%PDF-'))
            self.assertEqual(404,client.post('/v1/academic/transcripts/examples/unknown',json={}).status_code)

    def test_missing_catalogue_blocks_new_api_as_processing_unavailable(self):
        with patch.object(api,'_engine',return_value=AnswerEngine(replace(self.registry,catalogue=None))),TestClient(api.app) as client:
            response=client.post('/v1/academic/assistant',json={'question':'컴퓨터구조 학점','admission_year':2026,'matched_curriculum_year':2026,'department':'컴퓨터공학과'})
            self.assertEqual(503,response.status_code)


if __name__=='__main__': unittest.main()
