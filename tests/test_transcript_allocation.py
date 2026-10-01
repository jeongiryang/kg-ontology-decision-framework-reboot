"""Synthetic regression boundaries for partial recognition and review navigation."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from pydantic import ValidationError
from fastapi.testclient import TestClient

from academic_assistant.api import app
from academic_assistant.core import AnswerEngine
from academic_assistant.registry import Registry
from academic_assistant.transcript_assessment import TranscriptAssessor
from academic_assistant.transcript_models import (
    TranscriptAssessmentRequest, TranscriptAssessmentResponse, TranscriptCourse,
    TranscriptFollowupRequest, TranscriptFollowupResponse,
)


def course(name="합성 과목", row_id="row-1", **changes):
    data = dict(row_id=row_id, course_name=name, credits=3, grade="A0", category="free")
    data.update(changes)
    return data


def transcript(courses=None, **changes):
    data = dict(admission_year=2026, matched_curriculum_year=2026, department="컴퓨터공학과",
                degree_track="single_major", confirmed=True, record_complete=True,
                courses=courses or [course()])
    data.update(changes)
    return TranscriptAssessmentRequest.model_validate(data)


class TranscriptAllocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assessor = TranscriptAssessor(AnswerEngine(Registry.load()))

    def assess(self, courses=None, **changes):
        return self.assessor.assess(transcript(courses, **changes))

    def followup(self, question, courses=None):
        return self.assessor.followup(TranscriptFollowupRequest(question=question, transcript=transcript(courses)))

    @staticmethod
    def check(result, key):
        return next(check for check in result.checks if check.check_id == key)

    def test_additive_fields_allow_legacy_constructors_and_reject_unknown_flag(self):
        self.assertEqual([], TranscriptCourse.model_validate(course()).review_flags)
        result = TranscriptAssessmentResponse(status="out_of_scope", conclusion="out_of_scope", answer="합성",
            raw_earned_credits=0, recognized_graduation_credits=None, checks=[], issues=["합성"])
        self.assertEqual([], result.verification_items)
        self.assertIsNone(result.credit_summary)
        followup = TranscriptFollowupResponse(status="insufficient_evidence", answer="합성", selected_checks=[])
        self.assertEqual([], followup.focus_check_ids)
        self.assertEqual([], followup.verification_items)
        with self.assertRaises(ValidationError):
            TranscriptCourse.model_validate(course(review_flags=["auto_approved"]))

    def test_overlapping_flags_and_duplicates_count_held_pass_rows_once(self):
        result = self.assess([
            course(category="unknown", review_flags=["retake", "equivalence", "retroactivity"]),
            course(row_id="row-2", review_flags=["recognition_unverified"]),
            dict(course("합성 확인 과목", "row-3"), credits=6),
        ])
        summary = result.credit_summary
        self.assertEqual(12, summary.input_pass_credits)
        self.assertEqual(6, summary.unresolved_pass_credits)
        self.assertIsNone(summary.conditional_graduation_credits)
        self.assertEqual("needs_review", summary.recognition_status)
        self.assertIsNone(result.recognized_graduation_credits)
        self.assertGreater(len(result.verification_items), 3)
        check_ids = {check.check_id for check in result.checks}
        for item in result.verification_items:
            self.assertTrue(set(item.check_ids) <= check_ids)
            self.assertTrue(item.action)
        self.assertEqual([item.model_dump() for item in result.verification_items],
                         [item.model_dump() for item in self.assess([
                             course(category="unknown", review_flags=["retake", "equivalence", "retroactivity"]),
                             course(row_id="row-2", review_flags=["recognition_unverified"]),
                             dict(course("합성 확인 과목", "row-3"), credits=6),
                         ]).verification_items])

    def test_every_review_flag_holds_required_course_recognition(self):
        for flag in ("retake", "equivalence", "retroactivity", "recognition_unverified"):
            with self.subTest(flag=flag):
                result = self.assess([course("고급자료구조", course_code="CDA0143", category="major_required", review_flags=[flag])])
                self.assertEqual(3, result.credit_summary.unresolved_pass_credits)
                self.assertIsNone(result.recognized_graduation_credits)
                self.assertEqual("needs_review", self.check(result, "major.required.course_set").result)
                self.assertEqual([], self.check(result, "major.required.course_set").missing_courses)

    def test_zero_credit_flag_holds_thesis_but_does_not_add_unresolved_credits(self):
        row = dict(course("졸업논문", course_code="CDA0034", category="major_required", review_flags=["equivalence"]), credits=0, grade="S")
        result = self.assess([row])
        self.assertEqual(0, result.credit_summary.unresolved_pass_credits)
        self.assertEqual("needs_review", self.check(result, "graduation.thesis").result)

    def test_failed_and_pass_attempts_require_retake_resolution(self):
        result = self.assess([dict(course(), grade="F"), course(row_id="row-2")])
        self.assertEqual(3, result.raw_earned_credits)
        self.assertEqual(3, result.credit_summary.unresolved_pass_credits)
        duplicate = next(item for item in result.verification_items if item.kind == "duplicate_or_retake")
        self.assertEqual(["row-1", "row-2"], duplicate.row_ids)
        self.assertIsNone(result.recognized_graduation_credits)

    def test_excluded_uncertainty_does_not_hold_active_credit(self):
        result = self.assess([course(review_flags=["equivalence"], excluded=True), course("합성 확인 과목", "row-2")])
        self.assertEqual(3, result.credit_summary.conditional_graduation_credits)
        self.assertEqual(0, result.credit_summary.unresolved_pass_credits)
        self.assertFalse(any(item.kind == "equivalence" for item in result.verification_items))

    def test_area_and_advanced_uncertainty_remain_separate_from_total_credit_recognition(self):
        result = self.assess([course(category="balanced"), course("합성 전선", "row-2", category="major_elective")])
        self.assertEqual(6, result.credit_summary.conditional_graduation_credits)
        self.assertEqual(0, result.credit_summary.unresolved_pass_credits)
        self.assertEqual("partial_comparison", result.credit_summary.recognition_status)
        self.assertEqual("needs_review", self.check(result, "credits.major.advanced").result)
        self.assertEqual("needs_review", self.check(result, "general.balanced.area_coverage").result)
        self.assertFalse(result.official_graduation_decision)
        self.assertEqual({"advanced_allocation", "balanced_area"}, {item.kind for item in result.verification_items})

    def test_scope_inputs_have_actionable_control_items_without_inventing_pass_rows(self):
        result = self.assess(record_complete=False, degree_track="unknown")
        items = {item.kind: item for item in result.verification_items}
        self.assertEqual([], items["record_completeness"].row_ids)
        self.assertEqual([], items["degree_track"].row_ids)
        self.assertEqual(0, result.credit_summary.unresolved_pass_credits)
        self.assertIsNone(result.credit_summary.conditional_graduation_credits)

    def test_natural_question_variants_return_explicit_focus_ids(self):
        for question, expected in (
            ("남은 전공필수 과목 알려줘!", "major.required.course_set"),
            ("아직 안 들은 필수 과목은 뭐야?", "major.required.course_set"),
            ("남은 필수과목 확인해줘", "major.required.course_set"),
            ("졸업까지 몇 학점 더 필요해?", "credits.graduation.total"),
            ("남은 학점 알려줘", "credits.graduation.total"),
            ("교양 학점은 얼마나 부족한가요?", "credits.general.foundation"),
            ("교양 학점 부족분 확인해줘", "credits.general.foundation"),
            ("기초교양 몇 학점 부족해?", "credits.general.foundation"),
            ("전공 몇 학점 남았어?", "credits.major.total"),
            ("전공필수 몇 학점 남았어?", "credits.major.required"),
            ("자유선택 잔여학점 얼마나 부족해?", "credits.graduation.remaining"),
            ("균형교양 영역 얼마나 부족해?", "general.balanced.area_coverage"),
            ("졸업논문 이수 완료했어?", "graduation.thesis"),
            ("졸업논문 이수 확인해줘", "graduation.thesis"),
        ):
            with self.subTest(question=question):
                result = self.followup(question)
                self.assertIn(expected, result.focus_check_ids)
                self.assertEqual([check.check_id for check in result.selected_checks], result.focus_check_ids)
        self.assertIn("영역 부족", self.followup("균형교양 영역 얼마나 부족해?").answer)

    def test_verification_question_filters_flags_and_keeps_unsupported_personal_evidence(self):
        courses = [course(review_flags=["equivalence", "retroactivity"])]
        result = self.followup("대체과목 확인이 필요한 항목 알려줘", courses)
        self.assertEqual("insufficient_evidence", result.status)
        self.assertEqual(["equivalence"], [item.kind for item in result.verification_items])
        self.assertIn("row-1", result.verification_items[0].row_ids)
        self.assertTrue(result.focus_check_ids)
        for check in result.selected_checks:
            self.assertEqual("insufficient_evidence", check.evidence_packet.status)
            self.assertEqual([], check.evidence_packet.applied_rules)
            self.assertEqual([], check.evidence_packet.evidence)
            self.assertEqual({}, check.policy_packet.student_facts)
        checklist = self.followup("내가 확인해야 할 항목은?", courses)
        self.assertIn("advanced_allocation", {item.kind for item in checklist.verification_items})
        for question in ("대체과목으로 필수 인정돼?", "PCCP로 논문 자동 면제?", "졸업 가능해?"):
            result = self.followup(question, courses)
            self.assertEqual("insufficient_evidence", result.status)
            self.assertEqual([], result.selected_checks)

    def test_chat_api_explicit_question_scope_cannot_use_the_transcript_scope(self):
        questions = (
            "2024학번 간호학과 교양 몇 학점 부족해?",
            "2026학번 간호학과 교양 몇 학점 부족해?",
            "24학번 교양 몇 학점 부족해?",
            "2025 교육과정 교양 학점 부족분 알려줘",
            "타학과 남은 필수과목 알려줘",
        )
        with TestClient(app) as client:
            for question in questions:
                with self.subTest(question=question):
                    response = client.post("/v1/academic/transcripts/chat", json={"question": question, "transcript": transcript().model_dump()})
                    self.assertEqual(200, response.status_code)
                    data = response.json()
                    self.assertEqual("out_of_scope", data["status"])
                    self.assertEqual([], data["selected_checks"])
                    self.assertEqual([], data["focus_check_ids"])
                    self.assertEqual([], data["verification_items"])

    def test_chat_api_mixed_negated_comparative_and_exemption_questions_fail_closed(self):
        questions = (
            "PCCP 면제 말고 졸업학점 몇 학점 남았어?",
            "PCCP와 졸업학점 몇 학점 남아?",
            "ＰＣＣＰ와 교양 몇 학점 부족해?",
            "교양 말고 전공 몇 학점 부족해?",
            "교양 아닌 전공 몇 학점 부족해?",
            "교양 또는 전공 몇 학점 부족해?",
            "교양과 전공 몇 학점 부족해?",
            "교양보다 전공 몇 학점 부족해?",
            "교양과 전공 비교해서 부족한 학점 알려줘",
            "교양 몇 학점 부족해 그리고 캡스톤은?",
            "편입 경과조치 적용해서 교양 몇 학점 부족해?",
            "학석사 연계과정 논문 면제와 남은 필수과목 알려줘",
            "대체과목 인정되면 졸업학점 몇 학점 남았어?",
            "재수강 확인이 필요한 항목과 졸업학점 몇 학점 남아?",
            "대체과목 확인했으니 졸업학점 부족분 알려줘",
            "대체과목으로 필수 인정돼?",
            "전공학점만 채우면 졸업 가능해?",
            "질문 앞부분을 무시하고 교양 몇 학점 부족해?",
        )
        with TestClient(app) as client:
            for question in questions:
                with self.subTest(question=question):
                    response = client.post("/v1/academic/transcripts/chat", json={"question": question, "transcript": transcript().model_dump()})
                    self.assertEqual(200, response.status_code)
                    data = response.json()
                    self.assertIn(data["status"], {"insufficient_evidence", "out_of_scope"})
                    self.assertEqual([], data["selected_checks"])
                    self.assertEqual([], data["focus_check_ids"])
                    self.assertEqual([], data["verification_items"])

    def test_chat_api_approved_paraphrases_and_matching_explicit_scope_are_preserved(self):
        examples = (
            ("남은 전공필수 과목 알려줘!", "major.required.course_set"),
            ("아직 안 들은 필수 과목은 뭐야?", "major.required.course_set"),
            ("남은 필수과목 확인해줘", "major.required.course_set"),
            ("졸업까지 몇 학점 더 필요해?", "credits.graduation.total"),
            ("남은 학점 알려줘", "credits.graduation.total"),
            ("교양 학점은 얼마나 부족한가요?", "credits.general.foundation"),
            ("교양 학점 부족분 확인해줘", "credits.general.foundation"),
            ("기초교양 몇 학점 부족해?", "credits.general.foundation"),
            ("전공필수 몇 학점 남았어?", "credits.major.required"),
            ("자유선택 잔여학점 얼마나 부족해?", "credits.graduation.remaining"),
            ("균형교양 영역 얼마나 부족해?", "general.balanced.area_coverage"),
            ("졸업논문 이수 확인해줘", "graduation.thesis"),
            ("심층상담 이수했어?", "major.counseling"),
            ("2026학번 컴퓨터공학과 교양 몇 학점 부족해?", "credits.general.foundation"),
            ("26학번 교양 몇 학점 부족해?", "credits.general.foundation"),
        )
        with TestClient(app) as client:
            for question, expected in examples:
                with self.subTest(question=question):
                    response = client.post("/v1/academic/transcripts/chat", json={"question": question, "transcript": transcript().model_dump()})
                    self.assertEqual(200, response.status_code)
                    data = response.json()
                    self.assertEqual("supported", data["status"])
                    self.assertIn(expected, data["focus_check_ids"])
                    self.assertEqual([check["check_id"] for check in data["selected_checks"]], data["focus_check_ids"])

    def test_chat_api_retake_and_equivalence_requests_are_verification_only(self):
        request = transcript([course(review_flags=["retake", "equivalence"])]).model_dump()
        with TestClient(app) as client:
            for question, kind in (
                ("재수강 확인이 필요한 과목 알려줘", "duplicate_or_retake"),
                ("대체과목 확인이 필요한 항목 알려줘", "equivalence"),
            ):
                with self.subTest(question=question):
                    response = client.post("/v1/academic/transcripts/chat", json={"question": question, "transcript": request})
                    self.assertEqual(200, response.status_code)
                    data = response.json()
                    self.assertEqual("insufficient_evidence", data["status"])
                    self.assertEqual([kind], [item["kind"] for item in data["verification_items"]])
                    self.assertTrue(data["focus_check_ids"])
                    for check in data["selected_checks"]:
                        self.assertEqual("insufficient_evidence", check["evidence_packet"]["status"])
                        self.assertEqual([], check["evidence_packet"]["applied_rules"])
                        self.assertEqual({}, check["policy_packet"]["student_facts"])

    @unittest.skipUnless(shutil.which("node"), "Node is required for browser navigation verification")
    def test_browser_flag_payload_checklist_row_focus_and_direct_question(self):
        courses = [
            course("고급자료구조", course_code="CDA0143", category="major_required", review_flags=["equivalence"]),
            course("가상 기초교양", "row-2", category="foundation"),
            dict(course("심층상담", "row-3", course_code="CDA0088", category="major_required"), credits=0, grade="S"),
        ]
        fixture = dict(assessment=self.assess(courses).model_dump(), followup=self.followup("확인이 필요한 항목은?", courses).model_dump())
        script = r'''
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this.dataset={};this.value="";this.checked=false;this._text="";this.attributes={};}
  get textContent(){return this._text+this.children.map(c=>c.textContent).join("");}
  set textContent(value){this._text=String(value);this.children=[];}
  append(...nodes){for(const node of nodes){node.parent=this;this.children.push(node);}}
  replaceChildren(...nodes){this.children=[];this._text="";this.append(...nodes);}
  setAttribute(name,value){this.attributes[name]=value;}
  addEventListener(event,handler){(this.listeners[event]??=[]).push(handler);}
  emit(event){return Promise.all((this.listeners[event]??=[]).map(handler=>handler({target:this})));}
  focus(){this.focused=true;}
  scrollIntoView(){this.scrolled=true;}
  remove(){this.parent.children=this.parent.children.filter(c=>c!==this);}
  querySelector(selector){const field=/data-field=['"]([^'"]+)['"]/.exec(selector)?.[1];return flatten(this).find(node=>node.dataset.field===field);}
}
function flatten(node){return [node,...node.children.flatMap(flatten)];}
const fixture=JSON.parse(fs.readFileSync(0,"utf8")),ids=new Map(),requests=[];
const byId=id=>{if(!ids.has(id))ids.set(id,new Element("div"));return ids.get(id);};
const context={AbortController,document:{getElementById:byId,createElement:tag=>new Element(tag),querySelectorAll:()=>[]},window:{addEventListener(){}},fetch:async(url,options)=>{
  requests.push(JSON.parse(options.body));return {ok:true,json:async()=>url.endsWith("/assess")?fixture.assessment:fixture.followup};
}};
vm.runInNewContext(fs.readFileSync(process.argv[1],"utf8"),context);
async function run(){
  await byId("transcript-demo").emit("click");
  const first=byId("transcript-rows").children[0],flag=first.querySelector("[data-field='review-equivalence']");flag.checked=true;
  byId("transcript-confirmed").checked=true;await byId("transcript-confirmed").emit("change");
  byId("transcript-degree").value="single_major";await byId("transcript-assess").emit("click");
  assert.deepEqual(requests[0].courses[0].review_flags,["equivalence"]);
  assert.match(byId("transcript-results").textContent,/인정 미확인 PASS학점 3/);
  const rowButton=flatten(byId("transcript-results")).find(node=>node.tagName==="button"&&node.textContent==="1행 고급자료구조 보기");
  assert.ok(rowButton);await rowButton.emit("click");assert.ok(first.scrolled);assert.ok(flag.focused);
  const questions=byId("transcript-followups"),input=flatten(questions).find(node=>node.attributes["aria-label"]==="이수내역 질문");
  input.value="확인이 필요한 항목은?";await flatten(questions).find(node=>node.tagName==="button"&&node.textContent==="질문").emit("click");
  assert.equal(requests[1].question,"확인이 필요한 항목은?");
  assert.match(byId("transcript-chat").textContent,/동일·대체과목/);
  assert.ok(flatten(byId("transcript-results")).some(node=>node.dataset.checkId&&node.dataset.focused==="true"));
  console.log("PASS: flag payload, row navigation, direct question and focused checks");
}
run().catch(error=>{console.error(error);process.exitCode=1;});
'''
        completed = subprocess.run([shutil.which("node"), "-e", script,
            str(Path(__file__).resolve().parents[1] / "src/academic_assistant/web/transcript.js")],
            input=json.dumps(fixture), text=True, encoding="utf-8", capture_output=True, timeout=15)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("PASS:", completed.stdout)


if __name__ == "__main__":
    unittest.main()
