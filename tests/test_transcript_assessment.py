import unittest
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from academic_assistant.api import app, _engine
from academic_assistant.core import AnswerEngine
from academic_assistant.registry import Registry, RegistryUnavailable
from academic_assistant.transcript_models import TranscriptAssessmentRequest, TranscriptFollowupRequest
from academic_assistant.transcript_assessment import TranscriptAssessor


def row(name="가상 과목", *, credits=3, category="free", grade="A0", code=None, row_id="row-1", **kw):
    return dict(row_id=row_id, course_name=name, course_code=code, credits=credits, grade=grade, category=category, **kw)


def request(courses=None, **kw):
    return TranscriptAssessmentRequest(admission_year=2026, matched_curriculum_year=2026, department="컴퓨터공학과", degree_track="single_major", confirmed=True, record_complete=True, courses=courses or [row()], **kw)


class TranscriptAssessmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = Registry.load()

    def assess(self, req):
        return TranscriptAssessor(AnswerEngine(self.registry)).assess(req)

    @staticmethod
    def check(result, key):
        return next(check for check in result.checks if check.check_id == key)

    def test_empty_confirm_false_pii_extra_rejected(self):
        for override in ({"courses":[]}, {"confirmed":False}, {"confirmed":1}, {"confirmed":"true"}, {"student_id":"private"}, {"admission_year":True}):
            data=request().model_dump(); data.update(override)
            with self.subTest(override=override), self.assertRaises(ValidationError):
                TranscriptAssessmentRequest.model_validate(data)

    def test_course_validation(self):
        for bad in (row(credits=-1), row(credits=3.5), row(credits=True), row(category="전선"), row(grade="unknown"), row(name="학번12345678")):
            with self.subTest(bad=bad), self.assertRaises(ValidationError): request([bad])

    def test_wrong_admission_or_curriculum_year_out_of_scope(self):
        for key in ("admission_year", "matched_curriculum_year"):
            req=request(); data=req.model_dump(); data[key]=2022
            result=self.assess(TranscriptAssessmentRequest.model_validate(data))
            self.assertEqual(result.status,"out_of_scope"); self.assertFalse(result.checks)

    def test_degree_track_gates(self):
        data=request().model_dump(); data["degree_track"]="multiple_major"
        result=self.assess(TranscriptAssessmentRequest.model_validate(data)); self.assertEqual(result.status,"out_of_scope")
        del data["degree_track"]
        result=self.assess(TranscriptAssessmentRequest.model_validate(data)); self.assertIsNone(result.recognized_graduation_credits)
        self.assertEqual(self.check(result,"credits.major.total").result,"needs_review")

    def test_credit_gap_and_free_not_double_counted(self):
        result=self.assess(request([row("가상 교양",credits=9,category="foundation"),row("가상 자유",credits=6,row_id="row-2")]))
        self.assertEqual(result.raw_earned_credits,15)
        self.assertEqual(self.check(result,"credits.graduation.total").gap,115)
        self.assertEqual(self.check(result,"credits.graduation.remaining").earned,6)
        self.assertEqual(self.check(result,"credits.general.remaining").earned,0)

    def test_general_residual_does_not_reuse_foundation_balanced_minimum(self):
        result=self.assess(request([row("기초",credits=12,category="foundation"),row("균형",credits=15,category="balanced",row_id="row-2"),row("확대",credits=7,category="expanded",row_id="row-3")]))
        self.assertEqual(self.check(result,"credits.general.remaining").earned,13)

    def test_general_cap42(self):
        result=self.assess(request([row("교양1",credits=30,category="expanded"),row("교양2",credits=20,category="expanded",row_id="row-2")]))
        self.assertEqual(result.raw_earned_credits,50)
        self.assertEqual(result.recognized_graduation_credits,42)

    def test_general_residual_obeys_cap_and_disjoint_minimum_allocations(self):
        result=self.assess(request([
            row("합성기초",credits=30,category="foundation"),
            row("합성균형",credits=30,category="balanced",row_id="row-2"),
            row("합성확대",credits=30,category="expanded",row_id="row-3"),
        ]))
        self.assertEqual(result.raw_earned_credits,90)
        self.assertEqual(result.recognized_graduation_credits,42)
        self.assertEqual(self.check(result,"credits.general.remaining").earned,21)
        applied={r.rule_id for r in self.check(result,"credits.general.remaining").evidence_packet.applied_rules}
        self.assertIn("cwnu.cs.2026.credits.general-recognition-cap",applied)

    def test_failed_and_excluded_not_counted(self):
        for grade in ("U","F","F0","W"):
            result=self.assess(request([row(grade=grade)])); self.assertEqual(result.raw_earned_credits,0)
        result=self.assess(request([row(excluded=True)])); self.assertEqual(result.raw_earned_credits,0)

    def test_zero_credit_thesis_and_counseling_required(self):
        result=self.assess(request())
        self.assertIn("졸업논문",self.check(result,"major.required.course_set").missing_courses)
        result=self.assess(request([row("졸업논문",credits=0,category="major_required",grade="S"),row("심층상담",credits=0,category="major_required",grade="S",row_id="row-2")]))
        self.assertEqual(self.check(result,"graduation.thesis").result,"met")
        self.assertEqual(self.check(result,"major.counseling").result,"met")

    def test_all9_required(self):
        items=self.registry.rules["cwnu.cs.2026.major-required-course-set"]["decision"]["outcome"]["items"]
        courses=[row(item["label"],code=item["item_id"],credits=0 if item["label"] in {"졸업논문","심층상담"} else 3,category="major_required",row_id=f"row-{index}") for index,item in enumerate(items)]
        result=self.assess(request(courses))
        self.assertEqual(self.check(result,"major.required.course_set").result,"met")
        self.assertFalse(result.official_graduation_decision)
        self.assertNotEqual(result.status,"supported")

    def test_missing_with_incomplete_record_is_review(self):
        data=request().model_dump(); data["record_complete"]=False
        result=self.assess(TranscriptAssessmentRequest.model_validate(data))
        self.assertEqual(self.check(result,"major.required.course_set").result,"needs_review")
        self.assertFalse(self.check(result,"major.required.course_set").missing_courses)

    def test_duplicate_records_block_credit_totals(self):
        result=self.assess(request([row(),row(row_id="row-2")]))
        self.assertIsNone(result.recognized_graduation_credits)
        self.assertEqual(self.check(result,"credits.graduation.total").result,"needs_review")

    def test_mixed_code_and_exact_name_duplicates_share_approved_identity(self):
        result=self.assess(request([
            row("고급자료구조",code="CDA0143",category="major_required"),
            row("고급 자료 구조",category="major_required",row_id="row-2"),
        ]))
        self.assertIsNone(result.recognized_graduation_credits)
        self.assertEqual(self.check(result,"credits.major.required").result,"needs_review")
        self.assertEqual(self.check(result,"major.required.course_set").result,"needs_review")

    def test_required_course_category_and_approved_credit_value_are_not_repaired(self):
        bad_rows=(
            row("고급자료구조",code="CDA0143",category="major_elective"),
            row("고급자료구조",code="CDA0143",category="major_required",credits=1),
            row("졸업논문",code="CDA0034",category="major_required",credits=3,grade="S"),
            row("심층상담",code="CDA0088",category="major_required",credits=3,grade="S"),
            row("심층상담",category="free",credits=0,grade="S"),
        )
        for course in bad_rows:
            with self.subTest(code=course["course_code"],credits=course["credits"],category=course["category"]):
                req=request([course]); original=req.model_dump()
                result=self.assess(req)
                self.assertEqual(original,req.model_dump())
                self.assertIsNone(result.recognized_graduation_credits)
                self.assertIsNone(self.check(result,"credits.major.required").earned)
                self.assertEqual("needs_review",self.check(result,"major.required.course_set").result)

    def test_unmatched_major_required_row_cannot_assert_approved_credits(self):
        result=self.assess(request([row("합성 미등록필수",category="major_required")]))
        check=self.check(result,"credits.major.required")
        self.assertEqual("needs_review",check.result)
        self.assertIsNone(check.earned)
        self.assertEqual([],check.evidence_packet.applied_rules)

    def test_repeated0_counseling_is_not_credit_duplicate(self):
        result=self.assess(request([row("심층상담",credits=0,grade="S",category="major_required"),row("심층상담",credits=0,grade="S",category="major_required",row_id="row-2")]))
        self.assertEqual(result.recognized_graduation_credits,0)
        self.assertEqual(self.check(result,"major.counseling").result,"met")

    def test_unknown_category_and_code_name_conflict_block(self):
        for course in (row(category="unknown"),row("알고리즘",code="CDA0016",category="major_required")):
            result=self.assess(request([course])); self.assertIsNone(result.recognized_graduation_credits)

    def test_no_arbitrary_advanced_allocation(self):
        result=self.assess(request([row(credits=30,category="major_elective")]))
        self.assertEqual(self.check(result,"credits.major.advanced").result,"needs_review")

    def test_balanced_unknown_areas_review(self):
        result=self.assess(request([row(category="balanced")]))
        self.assertEqual(self.check(result,"general.balanced.area_coverage").result,"needs_review")

    def test_all_balanced_areas_known(self):
        areas=("digital-communication","humanities-arts","society-culture","nature-science-technology")
        result=self.assess(request([row(f"가상균형{index}",row_id=f"row-{index}",category="balanced",balanced_area=area) for index,area in enumerate(areas)]))
        self.assertEqual(self.check(result,"general.balanced.area_coverage").result,"met")

    def test_all_applied_evidence_and_hashes(self):
        result=self.assess(request())
        for check in result.checks:
            packet=check.evidence_packet if check.evidence_packet.status == "supported" else check.policy_packet
            if check.evidence_packet.status != "supported":
                self.assertEqual([],check.evidence_packet.applied_rules)
                self.assertEqual([],check.evidence_packet.evidence)
                self.assertTrue(check.evidence_packet.issues)
                self.assertEqual({},packet.student_facts)
                self.assertTrue(packet.packet_id.endswith("-policy"))
                self.assertNotEqual(check.evidence_packet.packet_id,packet.packet_id)
            for rule in packet.applied_rules:
                self.assertEqual(rule.rule_sha256,self.registry.rule_hashes[rule.rule_id])
            self.assertTrue(packet.evidence)
            for ref in packet.evidence:
                self.assertIn(ref.source_id,self.registry.sources); self.assertIn("PDF",ref.locator)

    def test_actual_api_nested_packets_pass_canonical_schema_including_review_issues(self):
        schema=json.loads((Path(__file__).resolve().parents[1]/"contracts/evidence-packet.schema.json").read_text(encoding="utf-8"))
        validator=Draft202012Validator(schema)
        examples=[request(),request([row(category="unknown")]),request([row(category="balanced")])]
        incomplete=request().model_dump(); incomplete["record_complete"]=False
        examples.append(TranscriptAssessmentRequest.model_validate(incomplete))
        with TestClient(app) as client:
            for req in examples:
                responses=[client.post("/v1/academic/transcripts/assess",json=req.model_dump()),
                           client.post("/v1/academic/transcripts/chat",json={"question":"전공학점부족분","transcript":req.model_dump()})]
                for response in responses:
                    self.assertEqual(200,response.status_code)
                    data=response.json()
                    for check in data.get("checks",data.get("selected_checks",[])):
                        validator.validate(check["evidence_packet"])
                        if check["result"] == "needs_review":
                            self.assertEqual("insufficient_evidence",check["evidence_packet"]["status"])
                            self.assertEqual([],check["evidence_packet"]["applied_rules"])
                            self.assertEqual([],check["evidence_packet"]["evidence"])
                            self.assertEqual([],check["evidence_packet"]["issues"][0]["related_ids"])
                            validator.validate(check["policy_packet"])
                            self.assertEqual("supported",check["policy_packet"]["status"])
                            self.assertEqual({},check["policy_packet"]["student_facts"])

    def test_api_numeric_confirmation_is_rejected(self):
        data=request().model_dump(); data["confirmed"]=1
        with TestClient(app) as client:
            response=client.post("/v1/academic/transcripts/assess",json=data)
        self.assertEqual(422,response.status_code)

    def test_conflicting_rules_fail_closed(self):
        registry=deepcopy(self.registry); registry.conflicts["credit_threshold:credits.graduation.total"]=["a","b"]
        result=TranscriptAssessor(AnswerEngine(registry)).assess(request()); self.assertEqual(result.status,"conflict"); self.assertFalse(result.checks)

    def test_followup_bounded_and_no_held_inferences(self):
        assessor=TranscriptAssessor(AnswerEngine(self.registry))
        result=assessor.followup(TranscriptFollowupRequest(question="남은 필수과목",transcript=request()))
        self.assertEqual(result.status,"supported"); self.assertIn("운영체제",result.answer)
        for question in ("캡스톤 II PASS면 졸업작품 가능?","공모전으로 면제 가능?","졸업 가능해?","PCCP로 논문 자동 면제?"):
            result=assessor.followup(TranscriptFollowupRequest(question=question,transcript=request()))
            self.assertEqual(result.status,"insufficient_evidence"); self.assertFalse(result.selected_checks)

    def test_transcript_raw_not_sent_to_llm(self):
        with patch("academic_assistant.api._chat_engine",side_effect=AssertionError("LLM invoked")):
            with TestClient(app) as client:
                response=client.post("/v1/academic/transcripts/assess",json=request().model_dump())
                self.assertEqual(response.status_code,200)

    def test_api_503_and_generic_validation_no_echo(self):
        with TestClient(app) as client:
            data=request().model_dump(); data["name"]="private student"
            response=client.post("/v1/academic/transcripts/assess",json=data)
            self.assertEqual(response.status_code,422); self.assertNotIn("private",response.text)
            with patch("academic_assistant.api._engine",side_effect=RegistryUnavailable()):
                response=client.post("/v1/academic/transcripts/assess",json=request().model_dump()); self.assertEqual(response.status_code,503)

    def test_duplicate_row_ids_reject(self):
        with self.assertRaises(ValidationError): request([row(),row(name="과목2")])

    def test_api_pdf_error_contract_and_cache(self):
        with TestClient(app) as client:
            response=client.post("/v1/academic/transcripts/extract",content=b"bad",headers={"Content-Type":"application/pdf"})
            self.assertEqual(response.status_code,422)
            self.assertEqual(response.headers["Cache-Control"],"no-store")
            response=client.post("/v1/academic/transcripts/extract?page_number=0",content=b"bad",headers={"Content-Type":"application/pdf"})
            self.assertEqual(response.status_code,422)


if __name__ == "__main__": unittest.main()
