from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from academic_assistant.api import app, _engine, _chat_engine
from academic_assistant.core import AnswerEngine
from academic_assistant.grounded_chat import GroundedChatEngine, GroundedChatResponse
from academic_assistant.models import AcademicAnswerRequest, AcademicChatRequest
from academic_assistant.registry import Registry


def request(question: str, previous: str | None = None, **updates) -> AcademicChatRequest:
    value = {"question": question, "previous_question": previous, "admission_year": 2026,
             "matched_curriculum_year": 2026, "department": "컴퓨터공학과"}
    value.update(updates)
    return AcademicChatRequest.model_validate(value)


class NoModel:
    def __init__(self):
        self.calls = []

    def suggest_intent(self, signal, catalog):
        self.calls.append((signal, catalog))
        raise AssertionError("Deterministic dialogue must not use inference")


class AcademicDialogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.load(ROOT)
        cls.engine = AnswerEngine(cls.registry)

    def facade(self):
        fake = NoModel()
        return GroundedChatEngine(self.engine, fake), fake

    def test_korean_paraphrases_trace_to_existing_approved_rules(self):
        cases = {
            "졸업하려면 총 몇 학점을 이수해야 하나요?": "credits.graduation.total",
            "졸업은 총 몇 학점이 필요한가요?": "credits.graduation.total",
            "졸업에 필요한 총학점 알려줘": "credits.graduation.total",
            "교양 최소학점은 얼마인가요?": "credits.general.total",
            "최소 교양 학점 기준": "credits.general.total",
            "전공 전체학점 알려주세요": "credits.major.total",
            "전필은 몇 과목이야?": "major.required-course-set",
            "필수 전공 과목 목록": "major.required-course-set",
            "심층상담은 몇 회 이수해야 하나요?": "major.counseling-completion",
            "졸논 필수야?": "graduation.thesis.required",
            "졸논이 0학점이어도 이수해야 하나요?": "graduation.thesis.completion-result",
        }
        for question, intent in cases.items():
            with self.subTest(question=question):
                response = self.engine.answer(request(question))
                self.assertEqual("supported", response.status)
                self.assertEqual([intent], response.intent_ids)
                for applied in response.evidence_packet.applied_rules:
                    self.assertEqual(self.registry.rule_hashes[applied.rule_id], applied.rule_sha256)
                self.assertTrue(response.evidence_packet.evidence)

    def test_paraphrases_do_not_relax_mixed_negative_scope_and_pending_guards(self):
        for question in (
            "졸업하려면 총 몇학점을 이수해야 하나요 그리고 PCCP 면제는?",
            "졸논은 공모전으로 대체되나요?", "전필은 몇 과목 말고 전선",
            "심층상담 면제 가능한가요?", "졸업에 필요한 총학점 또는 전공전체학점",
            "PCCP 합격 기준은 내년에도 400점인가요?",
        ):
            with self.subTest(question=question):
                response = self.engine.answer(request(question))
                self.assertEqual("insufficient_evidence", response.status)
                self.assertEqual([], response.evidence_packet.evidence)
        self.assertEqual("out_of_scope", self.engine.answer(request("기계공학과 졸논 필수야?")).status)

    def test_alias_subject_does_not_discard_unknown_deadline_location_or_extra_clause(self):
        cases = (
            "졸논 등록 마감은 언제야?",
            "졸논 필수야 등록 마감은 언제야",
            "졸업에 필요한 총학점 등록 마감은 언제야",
            "졸논 필수야 그리고 등록 마감은 언제야",
            "졸논 제출 장소는 어디야?",
            "전공 전체학점 신청 절차를 알려줘",
            "심층상담 예약 방법은 무엇인가요?",
            "전필과목 담당자 연락처는?",
            "전공필수 기준 장학금 신청은 어떻게 하나요?",
            "교양 최소학점 맛집도 알려줘",
            "재수강 접수 마감은 언제인가요?",
            "전과생 교육과정 신청 장소는?",
            "졸업논문 대체 서류 제출 절차는?",
            "균형교양 기준부터 알려주고 점심 메뉴 추천해줘",
        )
        facade, fake = self.facade()
        for question in cases:
            with self.subTest(question=question):
                direct = self.engine.answer(request(question))
                self.assertEqual("insufficient_evidence", direct.status)
                self.assertEqual([], direct.evidence_packet.applied_rules)
                self.assertEqual([], direct.evidence_packet.evidence)
                chat = facade.chat(request(question, "졸업학점 기준"))
                self.assertEqual("insufficient_evidence", chat.status)
                self.assertFalse(chat.context_used)
                self.assertEqual([], chat.evidence_packet.evidence)
                replay = facade.chat(request("그건 다시 설명해줘", question))
                self.assertEqual("insufficient_evidence", replay.status)
                self.assertFalse(replay.context_used)
                self.assertEqual([], replay.evidence_packet.evidence)
        self.assertEqual([], fake.calls)

    def test_whole_question_guard_retains_known_complete_queries(self):
        for question in (
            "졸논 필수야?", "졸업에 필요한 총학점 알려줘",
            "2026학번 전공필수 및 전공선택 기준",
            "졸업논문이 0학점이어도 필수인가요?",
            "저는 동일교과목 정책의 학점 계산 방법을 알고 싶어요",
            "심층상담은 몇 회 이수해야 하나요?",
            "전공필수 과목 목록",
        ):
            with self.subTest(question=question):
                self.assertEqual("supported", self.engine.answer(request(question)).status)

    def test_unsupported_added_clause_has_api_cli_and_core_parity(self):
        questions = (
            "졸논 등록 마감은 언제야?", "졸논 필수야 등록 마감은 언제야",
            "졸업에 필요한 총학점 등록 마감은 언제야",
        )
        with patch.dict(os.environ, {"ACADEMIC_EVIDENCE_BACKEND": "registry", "ACADEMIC_LLM_PROVIDER": "disabled"}):
            _engine.cache_clear(); _chat_engine.cache_clear()
            try:
                with TestClient(app) as client:
                    for question in questions:
                        with self.subTest(question=question):
                            chat_value = request(question).model_dump(mode="json")
                            # /answers deliberately rejects both chat-only options.
                            direct_value = {key: value for key, value in chat_value.items()
                                            if key not in {"previous_question", "generate_answer"}}
                            for endpoint, payload in (("answers", direct_value), ("chat", chat_value)):
                                response = client.post("/v1/academic/" + endpoint, json=payload)
                                self.assertEqual(200, response.status_code)
                                self.assertEqual("insufficient_evidence", response.json()["status"])
                                self.assertEqual([], response.json()["evidence_packet"]["evidence"])
                            command = [sys.executable, "-m", "academic_assistant", "ask", "--year", "2026",
                                       "--department", "컴퓨터공학과", "--question", question, "--json"]
                            result = subprocess.run(command, cwd=ROOT,
                                env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
                            self.assertEqual(2, result.returncode, result.stderr)
                            self.assertEqual("insufficient_evidence", json.loads(result.stdout)["status"])
                            self.assertEqual([], json.loads(result.stdout)["evidence_packet"]["evidence"])
            finally:
                _engine.cache_clear(); _chat_engine.cache_clear()

    def test_one_question_replay_uses_current_earned_credits_not_old_answer_state(self):
        facade, fake = self.facade()
        for earned, gap in ((100, 30), (125, 5), (150, 0)):
            response = facade.chat(request("그럼 몇 학점 남았어?", "졸업학점 기준",
                                           earned_credits={"credits.graduation.total": earned}))
            self.assertEqual("supported", response.status)
            self.assertTrue(response.context_used)
            self.assertEqual(earned, response.calculations[0].earned)
            self.assertEqual(gap, response.calculations[0].gap)
            self.assertEqual({"credits.graduation.total": earned}, response.evidence_packet.student_facts)
        self.assertEqual([], fake.calls)

    def test_missing_credits_does_not_create_personal_credit_result(self):
        facade, fake = self.facade()
        response = facade.chat(request("그럼 몇 학점 남았어?", "졸업학점 기준"))
        self.assertEqual("insufficient_evidence", response.status)
        self.assertTrue(response.context_used)
        self.assertEqual([], response.calculations)
        self.assertEqual([], response.evidence_packet.evidence)
        self.assertEqual([], fake.calls)

    def test_server_has_no_implicit_conversation_state(self):
        facade, fake = self.facade()
        self.assertEqual("supported", facade.chat(request("졸업학점 기준")).status)
        response = facade.chat(request("그럼 몇 학점인가요?"))
        self.assertEqual("insufficient_evidence", response.status)
        self.assertFalse(response.context_used)
        self.assertEqual(3, len(response.clarification_choices))
        self.assertEqual([], response.evidence_packet.evidence)
        self.assertEqual([], fake.calls)

    def test_ambiguous_prior_requires_choices_instead_of_arbitrary_metric(self):
        facade, fake = self.facade()
        for previous in ("전공필수 및 전공선택 기준", "교양 학점 기준"):
            with self.subTest(previous=previous):
                response = facade.chat(request("그럼 몇 학점인가요?", previous))
                self.assertEqual("insufficient_evidence", response.status)
                self.assertFalse(response.context_used)
                self.assertTrue(1 < len(response.clarification_choices) <= 3)
                self.assertEqual([], response.evidence_packet.applied_rules)
                for choice in response.clarification_choices:
                    checked = self.engine.answer(request(choice.question))
                    self.assertEqual("supported", checked.status)
        self.assertEqual([], fake.calls)

    def test_followup_units_are_checked_against_prior_rule_type(self):
        facade, fake = self.facade()
        for question, previous, intent in (
            ("그럼 몇 과목이야?", "전공필수 과목 목록", "major.required-course-set"),
            ("그럼 몇 번인가요?", "심층상담 이수 기준", "major.counseling-completion"),
            ("그건 다시 설명해줘", "졸업논문 이수 기준", "graduation.thesis.required"),
        ):
            response = facade.chat(request(question, previous))
            self.assertEqual("supported", response.status)
            self.assertTrue(response.context_used)
            self.assertEqual([intent], response.intent_ids)
        response = facade.chat(request("그럼 몇 과목이야?", "졸업학점 기준"))
        self.assertEqual("insufficient_evidence", response.status)
        self.assertFalse(response.context_used)
        self.assertEqual([], fake.calls)

    def test_current_scope_and_prior_text_scope_are_both_revalidated(self):
        facade, fake = self.facade()
        for updates, previous in (
            ({"admission_year": 2025}, "졸업학점 기준"),
            ({"department": "기계공학과"}, "졸업학점 기준"),
            ({}, "2025학번 졸업학점 기준"),
            ({}, "기계공학과 졸업학점 기준"),
        ):
            response = facade.chat(request("그럼 몇 학점인가요?", previous, **updates))
            self.assertEqual("out_of_scope", response.status)
            self.assertFalse(response.context_used)
            self.assertEqual([], response.evidence_packet.evidence)
        self.assertEqual([], fake.calls)

    def test_pending_negated_and_operational_prior_cannot_be_promoted_by_context(self):
        facade, fake = self.facade()
        for previous in (
            "공모전 졸업논문 면제", "전공필수 말고 전공선택 기준", "현재 PCCP 합격 기준",
            "코딩 테스트 미통과 시 처리", "졸업작품 수강 선행조건",
            "PCCP 합격 기준은 내년에도 400점인가요?", "학석사연계과정 논문 면제 가능성이 있나요",
            "졸업논문 대체요건", "수강신청 일정은 언제인가요?",
        ):
            with self.subTest(previous=previous):
                response = facade.chat(request("그건 다시 설명해줘", previous))
                self.assertEqual("insufficient_evidence", response.status)
                self.assertFalse(response.context_used)
                self.assertEqual([], response.evidence_packet.evidence)
        self.assertEqual([], fake.calls)

    def test_unapproved_or_mixed_current_followup_is_not_concatenated_to_prior(self):
        facade, fake = self.facade()
        for current in (
            "그럼 공모전으로 면제돼요?", "그럼 PCCP는 내년도 400점이죠?",
            "그럼 몇 학점 남았어 그리고 캡스톤 PASS야?",
            "그럼 전공필수 말고 몇 학점인가요?", "그럼 저는 졸업 가능해요?",
        ):
            with self.subTest(current=current):
                response = facade.chat(request(current, "졸업학점 기준"))
                self.assertNotEqual("supported", response.status)
                self.assertFalse(response.context_used)
                self.assertEqual([], response.evidence_packet.applied_rules)
        self.assertEqual([], fake.calls)

    def test_prior_conflict_is_preserved_and_not_offered_as_a_valid_choice(self):
        registry = replace(self.registry, conflicts={"credit_threshold:credits.graduation.total": ("cwnu.cs.2026.credits.graduation-total",)})
        facade = GroundedChatEngine(AnswerEngine(registry), NoModel())
        response = facade.chat(request("그럼 몇 학점인가요?", "졸업학점 기준"))
        self.assertEqual("conflict", response.status)
        self.assertFalse(response.context_used)
        self.assertEqual([], response.clarification_choices)
        self.assertEqual([], response.evidence_packet.evidence)

    def test_previous_input_is_one_bounded_validated_string_not_client_state(self):
        for updates in (
            {"previous_question": "x" * 501}, {"previous_question": " "},
            {"previous_question": {"question": "졸업학점", "status": "supported"}},
            {"history": ["졸업학점"]}, {"context_used": True},
            {"previous_rule_ids": ["cwnu.cs.2026.credits.graduation-total"]},
        ):
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                request("그럼 몇 학점인가요?", **updates)
        facade, fake = self.facade()
        for previous in ("학번 2026123456 졸업학점", "김철수 졸업학점"):
            with self.assertRaises(ValueError):
                facade.chat(request("그럼 몇 학점인가요?", previous))
        self.assertEqual([], fake.calls)

    def test_choices_contract_rejects_more_than_three_and_client_authored_rule_fields(self):
        response = self.facade()[0].chat(request("몇 학점인가요?"))
        value = response.model_dump(mode="python")
        value["clarification_choices"] *= 2
        with self.assertRaises(ValidationError):
            GroundedChatResponse.model_validate(value)
        value["clarification_choices"] = [{"label": "wrong", "question": "졸업학점", "rule_id": "invented"}]
        with self.assertRaises(ValidationError):
            GroundedChatResponse.model_validate(value)

    def test_chat_endpoint_accepts_bounded_context_but_answers_endpoint_does_not(self):
        with patch.dict(os.environ, {"ACADEMIC_EVIDENCE_BACKEND": "registry", "ACADEMIC_LLM_PROVIDER": "disabled"}):
            _engine.cache_clear(); _chat_engine.cache_clear()
            try:
                with TestClient(app) as client:
                    value = request("그럼 몇 학점 남았어?", "졸업학점 기준", earned_credits={"credits.graduation.total": 100}).model_dump(mode="json")
                    response = client.post("/v1/academic/chat", json=value)
                    self.assertEqual(200, response.status_code)
                    self.assertEqual("supported", response.json()["status"])
                    self.assertTrue(response.json()["context_used"])
                    self.assertEqual(30, response.json()["calculations"][0]["gap"])
                    self.assertEqual(422, client.post("/v1/academic/answers", json=value).status_code)
                    invalid = {**value, "previous_question": {"question": "졸업학점", "status": "supported"}}
                    self.assertEqual(422, client.post("/v1/academic/chat", json=invalid).status_code)
                    refusal = client.post("/v1/academic/chat", json=request(
                        "그럼 몇 학점인가요?", "교양 학점 기준").model_dump(mode="json")).json()
                    self.assertEqual("insufficient_evidence", refusal["status"])
                    self.assertEqual(422, client.post("/v1/academic/feedback", json={
                        "packet_id": refusal["packet_id"], "status": refusal["status"],
                        "question": "그럼 몇 학점인가요?", "earned_credits": {},
                        "category": "unclear_question", "consent_to_store": True,
                    }).status_code)
            finally:
                _engine.cache_clear(); _chat_engine.cache_clear()

    def test_browser_context_race_reset_and_safe_choice_dom(self):
        script = r'''
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
class E {
 constructor(){this.children=[];this.handlers={};this.attributes={};this.value='';this.textContent='';this.hidden=false;this.dataset={};}
 append(...xs){this.children.push(...xs);} appendChild(x){this.append(x);return x;}
 get firstChild(){return this.children[0];} removeChild(x){this.children=this.children.filter(y=>y!==x);}
 setAttribute(k,v){this.attributes[k]=v;} getAttribute(k){return this.attributes[k];}
 addEventListener(k,f){(this.handlers[k]??=[]).push(f);} focus(){} setCustomValidity(){} reportValidity(){}
 querySelector(){return submit;} dispatchEvent(e){return Promise.all((this.handlers[e.type]||[]).map(f=>f(e)));}
}
const ids=new Map(), get=id=>ids.get(id)||ids.set(id,new E()).get(id), submit=new E(), credit=new E();
credit.dataset.creditMetric='credits.graduation.total';
const document={getElementById:get,createElement:()=>new E(),querySelectorAll:s=>s==='[data-credit-metric]'?[credit]:[]};
let pending=[];const window={};
const fetch=(url,opts)=>url.endsWith('/runtime')?Promise.resolve({ok:true,json:async()=>({})}):new Promise((resolve,reject)=>pending.push({url,opts,resolve,reject}));
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{document,window,fetch,AbortController,Event:class{constructor(type){this.type=type;}}});
const ask=()=>get('question-form').handlers.submit[0]({preventDefault(){}});
const result=(text,status='supported',extra={})=>({ok:true,json:async()=>({status,answer:text,evidence_packet:{status,evidence:[],applied_rules:[],issues:[]},calculations:[],...extra})});
(async()=>{
 get('question').value='졸업학점 기준';let a=ask();pending.at(-1).resolve(result('first'));await a;
 get('question').value='그럼 몇 학점인가요?';a=ask();assert.equal(JSON.parse(pending.at(-1).opts.body).previous_question,'졸업학점 기준');
 const old=pending.at(-1);get('question').value='전공필수 기준';let b=ask();const fresh=pending.at(-1);
 assert.equal(old.opts.signal.aborted,true);fresh.resolve(result('fresh'));await b;old.resolve(result('stale'));await a;
 assert.equal(get('answer-text').textContent,'fresh');
 get('question').value='그건 다시 설명해줘';a=ask();pending.at(-1).resolve(result('context refusal','insufficient_evidence'));await a;
 assert.equal(get('feedback-section').hidden,true);
 get('feedback-consent').checked=true;const count=pending.length;await get('feedback-submit').handlers.click[0]();assert.equal(pending.length,count);
 credit.value='100';await credit.dispatchEvent({type:'input'});
 get('question').value='몇 학점인가요?';a=ask();assert.equal(JSON.parse(pending.at(-1).opts.body).previous_question,null);
 pending.at(-1).resolve(result('select','insufficient_evidence',{clarification_choices:[{label:'<b>safe</b>',question:'졸업학점 기준'}]}));await a;
 assert.equal(get('suggestion-text').children[0].textContent,'<b>safe</b>');
 assert.equal(get('feedback-section').hidden,true);
 get('question').value='그건 다시 설명해줘';a=ask();pending.at(-1).resolve(result('no context','insufficient_evidence'));await a;
 assert.equal(get('feedback-section').hidden,true);
 get('question').value='공모전 면제요건';a=ask();pending.at(-1).resolve(result('direct refusal','insufficient_evidence',{packet_id:'academic-'+'a'.repeat(32)}));await a;
 assert.equal(get('feedback-section').hidden,false);get('feedback-consent').checked=true;
 let feedback=get('feedback-submit').handlers.click[0]();assert.equal(pending.at(-1).url,'/v1/academic/feedback');
 const saved=JSON.parse(pending.at(-1).opts.body);assert.equal(saved.question,'공모전 면제요건');assert.equal('previous_question' in saved,false);
 pending.at(-1).resolve({status:201,json:async()=>({feedback_id:'feedback-ok',stored:true})});await feedback;
 get('question').value='next';a=ask();assert.equal(JSON.parse(pending.at(-1).opts.body).previous_question,null);
 pending.at(-1).resolve({ok:false,json:async()=>({detail:'invalid request'})});await a;
 console.log('PASS browser race, credit reset, unsupported/error reset, text-only choices and conservative feedback eligibility');
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run(["node", "-e", script, str(ROOT / "src/academic_assistant/web/app.js")],
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("PASS browser race", result.stdout)


if __name__ == "__main__":
    unittest.main()
