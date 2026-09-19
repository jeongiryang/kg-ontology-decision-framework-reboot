from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.validation.validate_academic_clarifications import validate_clarifications
from scripts.validation.validate_academic_knowledge import canonical_sha256


ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "reviews/academic/clarifications/2026-initial-review-questions.json"


def configure_conflict_question(question: dict) -> None:
    question["purpose"] = "conflict_triage"
    question["choices"] = [
        {"choice_id": "evidence", "label": "근거 제출", "disposition": "provide_evidence", "impact": "공식 근거를 추가한다."},
        {"choice_id": "revise", "label": "수정 필요", "disposition": "needs_revision", "impact": "해석을 수정한다.", "comment_required": True},
        {"choice_id": "defer", "label": "보류", "disposition": "pending", "impact": "충돌을 보존한다."},
    ]
    question["recommended_choice_id"] = "evidence"


class AcademicClarificationValidationTest(unittest.TestCase):
    def make_project(self, root: Path) -> Path:
        shutil.copytree(ROOT / "contracts", root / "contracts")
        shutil.copytree(ROOT / "knowledge", root / "knowledge")
        shutil.copytree(ROOT / "reviews", root / "reviews")
        # This helper builds a synthetic pre-approval state for the original
        # 2026 review session.  A later, already-applied TA confirmation packet
        # must not be revalidated against the deliberately mutated fixtures.
        (root / "reviews/academic/clarifications/2026-ta-confirmation.json").unlink()
        path = root / "reviews/academic/clarifications/2026-initial-review-questions.json"
        packet = json.loads(path.read_text(encoding="utf-8"))
        review_path = root / "reviews/academic/2026-curriculum-initial-rules.json"
        review = json.loads(review_path.read_text(encoding="utf-8"))
        for subject in review["subjects"]:
            subject["status"] = "pending"
            for field in ("reviewer_id", "reviewed_at", "comment"):
                subject.pop(field, None)
        review["overall_status"] = "open"
        review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")

        snapshots = [{
            "artifact_id": f"review:{review['review_id']}",
            "sha256": canonical_sha256(review),
        }]
        for directory, prefix, id_field in (
            ("sources", "source", "source_id"),
            ("rules", "rule", "rule_id"),
        ):
            for object_path in sorted((root / "knowledge" / directory).glob("*.json")):
                value = json.loads(object_path.read_text(encoding="utf-8"))
                value["review"] = {"status": "needs_review", "mode": "human", "scope": "full"}
                object_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
                snapshots.append({
                    "artifact_id": f"{prefix}:{value[id_field]}",
                    "sha256": canonical_sha256(value),
                })

        packet["state"] = "ready"
        packet.pop("application", None)
        packet["input_snapshots"] = sorted(snapshots, key=lambda item: item["artifact_id"])
        packet["session"] = {
            "session_id": packet["session"]["session_id"],
            "run_id": packet["session"]["run_id"],
            "thread_binding": "current_task",
            "state": "awaiting_authority",
            "started_at": packet["session"]["started_at"],
            "authority": {
                "kind": "department_confirmation",
                "status": "not_requested",
                "authorization_prompt": packet["session"]["authority"]["authorization_prompt"],
                "choices": ["grant", "decline"],
            },
        }
        for batch in packet["batches"]:
            batch["status"] = "queued"
            for question in batch["questions"]:
                question["status"] = "queued"
        packet["responses"] = []
        packet["audit_events"] = [{
            "event_id": "event.created",
            "event": "created",
            "at": packet["session"]["started_at"],
            "actor": "main",
            "details": "테스트용 준비 상태를 생성했다.",
        }]
        path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
        return path

    def mutate(self, callback) -> list[str]:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            path = self.make_project(project)
            packet = json.loads(path.read_text(encoding="utf-8"))
            callback(packet)
            path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
            return validate_clarifications(project)

    def test_repository_packet_has_exact_full_review_coverage(self) -> None:
        self.assertEqual(validate_clarifications(ROOT), [])
        packet = json.loads(PACKET.read_text(encoding="utf-8"))
        self.assertEqual(packet["state"], "applied")
        self.assertEqual(packet["session"]["state"], "closed")
        self.assertEqual(packet["session"]["authority"]["status"], "expired")
        covered = [
            subject
            for batch in packet["batches"]
            for question in batch["questions"]
            for subject in question["subject_ids"]
        ]
        self.assertEqual(len(covered), 14)
        self.assertEqual(len(set(covered)), 14)

    def test_applied_output_snapshot_must_match_approved_objects(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            shutil.copytree(ROOT / "contracts", project / "contracts")
            shutil.copytree(ROOT / "knowledge", project / "knowledge")
            shutil.copytree(ROOT / "reviews", project / "reviews")
            path = project / "reviews/academic/clarifications/2026-initial-review-questions.json"
            packet = json.loads(path.read_text(encoding="utf-8"))
            packet["application"]["output_snapshots"][0]["sha256"] = "0" * 64
            path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
            errors = validate_clarifications(project)
            self.assertTrue(any("snapshot hash mismatch" in error for error in errors))

    def test_applied_rule_decision_cannot_change_with_refreshed_output_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            shutil.copytree(ROOT / "contracts", project / "contracts")
            shutil.copytree(ROOT / "knowledge", project / "knowledge")
            shutil.copytree(ROOT / "reviews", project / "reviews")
            rule_path = project / "knowledge/rules/cwnu.cs.2026.credits.general-foundation.json"
            rule = json.loads(rule_path.read_text(encoding="utf-8"))
            rule["decision"]["statement"] = "기초교양을 99학점 이상 이수해야 한다."
            rule["decision"]["outcome"]["credits"] = 99
            rule_path.write_text(json.dumps(rule, ensure_ascii=False), encoding="utf-8")
            packet_path = project / "reviews/academic/clarifications/2026-initial-review-questions.json"
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            artifact_id = "rule:cwnu.cs.2026.credits.general-foundation"
            for snapshot in packet["application"]["output_snapshots"]:
                if snapshot["artifact_id"] == artifact_id:
                    snapshot["sha256"] = canonical_sha256(rule)
            packet_path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
            errors = validate_clarifications(project)
            self.assertTrue(any("changed outside review metadata" in error for error in errors))

    def test_unverified_claim_paraphrase_cannot_replace_approved_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            shutil.copytree(ROOT / "contracts", project / "contracts")
            shutil.copytree(ROOT / "knowledge", project / "knowledge")
            shutil.copytree(ROOT / "reviews", project / "reviews")
            rule_path = project / "knowledge/rules/cwnu.cs.2026.graduation.thesis-substitution.json"
            rule = json.loads(rule_path.read_text(encoding="utf-8"))
            rule["decision"]["statement"] = "총장상급 이상의 외부 공모전에서 수상하면 졸업작품을 대신할 수 있다."
            rule_path.write_text(json.dumps(rule, ensure_ascii=False), encoding="utf-8")
            packet_path = project / "reviews/academic/clarifications/2026-initial-review-questions.json"
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            artifact_id = "rule:cwnu.cs.2026.graduation.thesis-substitution"
            for snapshot in packet["application"]["output_snapshots"]:
                if snapshot["artifact_id"] == artifact_id:
                    snapshot["sha256"] = canonical_sha256(rule)
            packet_path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
            errors = validate_clarifications(project)
            self.assertTrue(any("changed outside review metadata" in error for error in errors))

    def test_applied_review_metadata_is_attested(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            shutil.copytree(ROOT / "contracts", project / "contracts")
            shutil.copytree(ROOT / "knowledge", project / "knowledge")
            shutil.copytree(ROOT / "reviews", project / "reviews")
            rule_path = project / "knowledge/rules/cwnu.cs.2026.credits.general-foundation.json"
            rule = json.loads(rule_path.read_text(encoding="utf-8"))
            rule["review"]["reviewer_id"] = "replacement_reviewer"
            rule_path.write_text(json.dumps(rule, ensure_ascii=False), encoding="utf-8")
            review_path = project / "reviews/academic/2026-curriculum-initial-rules.json"
            review = json.loads(review_path.read_text(encoding="utf-8"))
            subject = next(
                item for item in review["subjects"]
                if item["subject_id"] == "cwnu.cs.2026.credits.general-foundation"
            )
            subject["reviewer_id"] = "replacement_reviewer"
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            packet_path = project / "reviews/academic/clarifications/2026-initial-review-questions.json"
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            replacements = {
                "rule:cwnu.cs.2026.credits.general-foundation": canonical_sha256(rule),
                "review:cwnu.cs.2026.initial-rules": canonical_sha256(review),
            }
            for snapshot in packet["application"]["output_snapshots"]:
                if snapshot["artifact_id"] in replacements:
                    snapshot["sha256"] = replacements[snapshot["artifact_id"]]
            packet_path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
            errors = validate_clarifications(project)
            self.assertTrue(any("review attestation mismatch" in error for error in errors))

    def test_applied_user_exact_text_is_attested(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            shutil.copytree(ROOT / "contracts", project / "contracts")
            shutil.copytree(ROOT / "knowledge", project / "knowledge")
            shutil.copytree(ROOT / "reviews", project / "reviews")
            packet_path = project / "reviews/academic/clarifications/2026-initial-review-questions.json"
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            packet["responses"][0]["exact_text"] = "사용자가 말하지 않은 조작 문구"
            packet_path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
            errors = validate_clarifications(project)
            self.assertTrue(any("response snapshot mismatch" in error for error in errors))

    def test_missing_subject_fails_closed(self) -> None:
        errors = self.mutate(lambda packet: packet["batches"][1]["questions"].pop())
        self.assertTrue(any("full_review coverage is incomplete" in error for error in errors))

    def test_unknown_recommended_choice_is_rejected(self) -> None:
        def change(packet):
            packet["batches"][0]["questions"][0]["recommended_choice_id"] = "unknown"

        errors = self.mutate(change)
        self.assertTrue(any("recommends an unknown choice" in error for error in errors))

    def test_question_page_locator_must_match_current_subject(self) -> None:
        def change(packet):
            packet["batches"][1]["questions"][0]["evidence"][0]["locator"] = "PDF p.999"

        errors = self.mutate(change)
        self.assertTrue(any("page locators do not match subject" in error for error in errors))

    def test_question_evidence_is_exactly_bound_to_current_subject(self) -> None:
        mutations = (
            ("locator", "PDF p.23 (printed p.15), p.261 (printed p.253), p.577 (printed p.569): 정보통신공학과 학점표의 기초교양 열"),
            ("claim", "기초교양을 99학점 이상 이수해야 한다."),
            ("authority", "university_statute"),
        )
        for field, value in mutations:
            def change(packet, field=field, value=value):
                packet["batches"][1]["questions"][0]["evidence"][0][field] = value

            with self.subTest(field=field):
                errors = self.mutate(change)
                self.assertTrue(any("not canonically bound" in error for error in errors))

    def test_department_confirmation_requires_granted_event(self) -> None:
        def change(packet):
            question = packet["batches"][0]["questions"][0]
            question["status"] = "accepted"
            packet["responses"] = [{
                "response_id": "response.source",
                "question_id": question["question_id"],
                "session_id": packet["session"]["session_id"],
                "answer_mode": "choice",
                "selected_choice_id": "confirm",
                "exact_text": "승인",
                "answered_by": "project_owner",
                "answered_at": "2026-09-18T14:35:00+09:00",
                "authority_used": "department_confirmation",
                "authorization_event_id": "event.missing",
                "normalization_status": "accepted"
            }]

        errors = self.mutate(change)
        self.assertTrue(any("lacks a valid authority_granted" in error for error in errors))

    def test_expired_authority_cannot_authorize_later_answer(self) -> None:
        def change(packet):
            question = packet["batches"][0]["questions"][0]
            question["status"] = "accepted"
            packet["session"]["state"] = "closed"
            packet["session"]["ended_at"] = "2026-09-18T14:34:00+09:00"
            packet["session"]["end_reason"] = "completed"
            packet["session"]["authority"]["status"] = "expired"
            packet["audit_events"].extend([
                {"event_id": "event.granted", "event": "authority_granted", "at": "2026-09-18T14:31:00+09:00", "actor": "project_owner", "details": "권한 부여"},
                {"event_id": "event.expired", "event": "authority_expired", "at": "2026-09-18T14:34:00+09:00", "actor": "main", "details": "세션 종료"}
            ])
            packet["responses"] = [{
                "response_id": "response.source", "question_id": question["question_id"],
                "session_id": packet["session"]["session_id"], "answer_mode": "choice",
                "selected_choice_id": "confirm", "exact_text": "승인",
                "answered_by": "project_owner", "answered_at": "2026-09-18T14:35:00+09:00",
                "authority_used": "department_confirmation", "authorization_event_id": "event.granted",
                "normalization_status": "accepted"
            }]

        errors = self.mutate(change)
        self.assertTrue(any("after authority ended" in error for error in errors))

    def test_answer_before_later_session_close_remains_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            path = self.make_project(project)
            packet = json.loads(path.read_text(encoding="utf-8"))
            question = packet["batches"][0]["questions"][0]
            question["status"] = "accepted"
            packet["state"] = "closed"
            packet["batches"][0]["status"] = "answered"
            packet["session"].update(
                state="closed", ended_at="2026-09-18T14:36:00+09:00", end_reason="completed"
            )
            packet["session"]["authority"]["status"] = "expired"
            packet["audit_events"].extend([
                {"event_id": "event.granted", "event": "authority_granted", "at": "2026-09-18T14:31:00+09:00", "actor": "project_owner", "details": "권한 부여"},
                {"event_id": "event.presented", "event": "question_presented", "at": "2026-09-18T14:32:00+09:00", "actor": "main", "question_id": question["question_id"], "details": "질문 제시"},
                {"event_id": "event.answered", "event": "answer_recorded", "at": "2026-09-18T14:35:00+09:00", "actor": "main", "question_id": question["question_id"], "response_id": "response.source", "details": "답변 기록"},
                {"event_id": "event.expired", "event": "authority_expired", "at": "2026-09-18T14:36:00+09:00", "actor": "main", "details": "세션 종료"},
                {"event_id": "event.closed", "event": "closed", "at": "2026-09-18T14:36:00+09:00", "actor": "main", "details": "검수 종료"}
            ])
            packet["responses"] = [{
                "response_id": "response.source", "question_id": question["question_id"],
                "session_id": packet["session"]["session_id"], "answer_mode": "choice",
                "selected_choice_id": "confirm", "exact_text": "승인",
                "answered_by": "project_owner", "answered_at": "2026-09-18T14:35:00+09:00",
                "authority_used": "department_confirmation", "authorization_event_id": "event.granted",
                "normalization_status": "accepted"
            }]
            path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(validate_clarifications(project), [])

    def test_review_snapshot_change_requires_regeneration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            review_path = next((project / "reviews/academic").glob("*.json"))
            review = json.loads(review_path.read_text(encoding="utf-8"))
            review["notes"].append("changed")
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            errors = validate_clarifications(project)
            self.assertTrue(any("snapshot hash mismatch" in error for error in errors))

    def test_duplicate_snapshot_ids_are_rejected(self) -> None:
        def change(packet):
            packet["input_snapshots"].append(copy.deepcopy(packet["input_snapshots"][0]))

        errors = self.mutate(change)
        self.assertTrue(any("duplicate input snapshot artifact_id" in error for error in errors))

    def test_subject_change_requires_question_regeneration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.make_project(project)
            rule_path = project / "knowledge/rules/cwnu.cs.2026.credits.general-foundation.json"
            rule = json.loads(rule_path.read_text(encoding="utf-8"))
            rule["decision"]["outcome"]["credits"] = 10
            rule_path.write_text(json.dumps(rule, ensure_ascii=False), encoding="utf-8")
            errors = validate_clarifications(project)
            self.assertTrue(any("subject snapshot hash mismatch" in error for error in errors))

    def test_question_authority_cannot_be_downgraded(self) -> None:
        def change(packet):
            question = packet["batches"][0]["questions"][0]
            question["status"] = "accepted"
            packet["responses"] = [{
                "response_id": "response.source", "question_id": question["question_id"],
                "session_id": packet["session"]["session_id"], "answer_mode": "choice",
                "selected_choice_id": "confirm", "exact_text": "승인",
                "answered_by": "project_owner", "answered_at": "2026-09-18T14:35:00+09:00",
                "authority_used": "project_review", "normalization_status": "accepted"
            }]

        errors = self.mutate(change)
        self.assertTrue(any("required authority" in error for error in errors))

    def test_only_project_owner_can_grant_department_authority(self) -> None:
        def change(packet):
            question = packet["batches"][0]["questions"][0]
            question["status"] = "accepted"
            packet["audit_events"].append(
                {"event_id": "event.granted", "event": "authority_granted", "at": "2026-09-18T14:31:00+09:00", "actor": "main", "details": "invalid self grant"}
            )
            packet["responses"] = [{
                "response_id": "response.source", "question_id": question["question_id"],
                "session_id": packet["session"]["session_id"], "answer_mode": "choice",
                "selected_choice_id": "confirm", "exact_text": "승인",
                "answered_by": "project_owner", "answered_at": "2026-09-18T14:35:00+09:00",
                "authority_used": "department_confirmation", "authorization_event_id": "event.granted",
                "normalization_status": "accepted"
            }]

        errors = self.mutate(change)
        self.assertTrue(any("lacks a valid authority_granted" in error for error in errors))

    def test_session_end_time_cannot_be_bypassed_by_missing_close_event(self) -> None:
        def change(packet):
            question = packet["batches"][0]["questions"][0]
            question["status"] = "accepted"
            packet["session"].update(
                state="closed", ended_at="2026-09-18T14:34:00+09:00", end_reason="completed"
            )
            packet["session"]["authority"]["status"] = "expired"
            packet["audit_events"].append(
                {"event_id": "event.granted", "event": "authority_granted", "at": "2026-09-18T14:31:00+09:00", "actor": "project_owner", "details": "권한 부여"}
            )
            packet["responses"] = [{
                "response_id": "response.source", "question_id": question["question_id"],
                "session_id": packet["session"]["session_id"], "answer_mode": "choice",
                "selected_choice_id": "confirm", "exact_text": "승인",
                "answered_by": "project_owner", "answered_at": "2026-09-18T14:35:00+09:00",
                "authority_used": "department_confirmation", "authorization_event_id": "event.granted",
                "normalization_status": "accepted"
            }]

        errors = self.mutate(change)
        self.assertTrue(any("outside the session time window" in error for error in errors))

    def test_deferred_choice_cannot_be_recorded_as_accepted(self) -> None:
        def change(packet):
            question = packet["batches"][0]["questions"][0]
            question["status"] = "accepted"
            packet["audit_events"].append(
                {"event_id": "event.granted", "event": "authority_granted", "at": "2026-09-18T14:31:00+09:00", "actor": "project_owner", "details": "권한 부여"}
            )
            packet["responses"] = [{
                "response_id": "response.source", "question_id": question["question_id"],
                "session_id": packet["session"]["session_id"], "answer_mode": "choice",
                "selected_choice_id": "defer", "exact_text": "보류",
                "answered_by": "project_owner", "answered_at": "2026-09-18T14:35:00+09:00",
                "authority_used": "department_confirmation", "authorization_event_id": "event.granted",
                "normalization_status": "accepted"
            }]

        errors = self.mutate(change)
        self.assertTrue(any("disposition requires question status deferred" in error for error in errors))

    def test_unprocessed_choice_cannot_finalize_question(self) -> None:
        def change(packet):
            question = packet["batches"][0]["questions"][0]
            question["status"] = "accepted"
            packet["audit_events"].append(
                {"event_id": "event.granted", "event": "authority_granted", "at": "2026-09-18T14:31:00+09:00", "actor": "project_owner", "details": "권한 부여"}
            )
            packet["responses"] = [{
                "response_id": "response.source", "question_id": question["question_id"],
                "session_id": packet["session"]["session_id"], "answer_mode": "choice",
                "selected_choice_id": "defer", "exact_text": "보류",
                "answered_by": "project_owner", "answered_at": "2026-09-18T14:35:00+09:00",
                "authority_used": "department_confirmation", "authorization_event_id": "event.granted",
                "normalization_status": "unprocessed"
            }]

        errors = self.mutate(change)
        self.assertTrue(any("cannot finalize a question" in error for error in errors))

    def test_free_text_cannot_be_accepted_without_normalized_choice(self) -> None:
        def change(packet):
            question = packet["batches"][0]["questions"][0]
            question["status"] = "accepted"
            packet["audit_events"].append(
                {"event_id": "event.granted", "event": "authority_granted", "at": "2026-09-18T14:31:00+09:00", "actor": "project_owner", "details": "권한 부여"}
            )
            packet["responses"] = [{
                "response_id": "response.source", "question_id": question["question_id"],
                "session_id": packet["session"]["session_id"], "answer_mode": "free_text",
                "exact_text": "모르겠습니다", "answered_by": "project_owner",
                "answered_at": "2026-09-18T14:35:00+09:00",
                "authority_used": "department_confirmation", "authorization_event_id": "event.granted",
                "normalization_status": "accepted"
            }]

        errors = self.mutate(change)
        self.assertTrue(any("cannot be accepted without a normalized choice" in error for error in errors))

    def test_closed_session_requires_terminal_audit_events(self) -> None:
        def change(packet):
            packet["state"] = "closed"
            packet["session"].update(
                state="closed", ended_at="2026-09-18T14:36:00+09:00", end_reason="user_closed"
            )
            packet["session"]["authority"]["status"] = "expired"

        errors = self.mutate(change)
        self.assertTrue(any("lacks matching authority_expired" in error for error in errors))
        self.assertTrue(any("lacks a closed event" in error for error in errors))

    def test_authority_events_follow_legal_transitions(self) -> None:
        def decline_while_waiting(packet):
            packet["audit_events"].append(
                {"event_id": "event.declined", "event": "authority_declined", "at": "2026-09-18T14:31:00+09:00", "actor": "project_owner", "details": "거절"}
            )

        errors = self.mutate(decline_while_waiting)
        self.assertTrue(any("cannot contain authority transitions" in error for error in errors))

        def expire_without_grant(packet):
            packet["state"] = "closed"
            packet["session"].update(
                state="closed", ended_at="2026-09-18T14:36:00+09:00", end_reason="completed"
            )
            packet["session"]["authority"]["status"] = "expired"
            packet["audit_events"].extend([
                {"event_id": "event.expired", "event": "authority_expired", "at": "2026-09-18T14:36:00+09:00", "actor": "main", "details": "만료"},
                {"event_id": "event.closed", "event": "closed", "at": "2026-09-18T14:36:00+09:00", "actor": "main", "details": "종료"}
            ])

        errors = self.mutate(expire_without_grant)
        self.assertTrue(any("requires exactly one prior grant" in error for error in errors))

        def revoke_then_expire(packet):
            packet["state"] = "closed"
            packet["session"].update(
                state="closed", ended_at="2026-09-18T14:36:00+09:00", end_reason="completed"
            )
            packet["session"]["authority"]["status"] = "expired"
            packet["audit_events"].extend([
                {"event_id": "event.granted", "event": "authority_granted", "at": "2026-09-18T14:31:00+09:00", "actor": "project_owner", "details": "권한 부여"},
                {"event_id": "event.revoked", "event": "authority_revoked", "at": "2026-09-18T14:35:00+09:00", "actor": "main", "details": "철회"},
                {"event_id": "event.expired", "event": "authority_expired", "at": "2026-09-18T14:36:00+09:00", "actor": "main", "details": "만료"},
                {"event_id": "event.closed", "event": "closed", "at": "2026-09-18T14:36:00+09:00", "actor": "main", "details": "종료"}
            ])

        errors = self.mutate(revoke_then_expire)
        self.assertTrue(any("terminal events are not mutually exclusive" in error for error in errors))

    def test_ready_packet_cannot_claim_nonqueued_batch(self) -> None:
        def change(packet):
            packet["batches"][0]["status"] = "answered"

        errors = self.mutate(change)
        self.assertTrue(any("ready packet must have queued batches" in error for error in errors))

    def test_partial_and_presented_states_require_matching_progress(self) -> None:
        def partial(packet):
            packet["state"] = "partially_answered"

        errors = self.mutate(partial)
        self.assertTrue(any("partially_answered packet needs some but not all" in error for error in errors))

        def presented(packet):
            packet["state"] = "awaiting_user"
            packet["session"]["state"] = "review_only"
            packet["session"]["authority"]["status"] = "declined"
            packet["batches"][0]["status"] = "presented"

        errors = self.mutate(presented)
        self.assertTrue(any("presented batch" in error for error in errors))

    def test_choice_response_must_use_accepted_normalization(self) -> None:
        def change(packet):
            question = packet["batches"][0]["questions"][0]
            question["status"] = "presented"
            packet["audit_events"].append(
                {"event_id": "event.granted", "event": "authority_granted", "at": "2026-09-18T14:31:00+09:00", "actor": "project_owner", "details": "권한 부여"}
            )
            packet["responses"] = [{
                "response_id": "response.source", "question_id": question["question_id"],
                "session_id": packet["session"]["session_id"], "answer_mode": "choice",
                "selected_choice_id": "confirm", "exact_text": "아마 승인",
                "answered_by": "project_owner", "answered_at": "2026-09-18T14:35:00+09:00",
                "authority_used": "department_confirmation", "authorization_event_id": "event.granted",
                "normalization_status": "needs_clarification"
            }]

        errors = self.mutate(change)
        self.assertTrue(any("explicit choice response" in error for error in errors))

    def test_response_requires_correlated_audit_events(self) -> None:
        def change(packet):
            question = packet["batches"][0]["questions"][0]
            question["status"] = "accepted"
            packet["audit_events"].append(
                {"event_id": "event.granted", "event": "authority_granted", "at": "2026-09-18T14:31:00+09:00", "actor": "project_owner", "details": "권한 부여"}
            )
            packet["responses"] = [{
                "response_id": "response.source", "question_id": question["question_id"],
                "session_id": packet["session"]["session_id"], "answer_mode": "choice",
                "selected_choice_id": "confirm", "exact_text": "승인",
                "answered_by": "project_owner", "answered_at": "2026-09-18T14:35:00+09:00",
                "authority_used": "department_confirmation", "authorization_event_id": "event.granted",
                "normalization_status": "accepted"
            }]

        errors = self.mutate(change)
        self.assertTrue(any("question_presented event" in error for error in errors))
        self.assertTrue(any("answer_recorded event" in error for error in errors))

    def test_pre_session_presentation_event_is_rejected(self) -> None:
        def change(packet):
            question = packet["batches"][0]["questions"][0]
            question["status"] = "accepted"
            packet["audit_events"].extend([
                {"event_id": "event.granted", "event": "authority_granted", "at": "2026-09-18T14:31:00+09:00", "actor": "project_owner", "details": "권한 부여"},
                {"event_id": "event.presented", "event": "question_presented", "at": "2020-01-01T00:00:00+09:00", "actor": "main", "question_id": question["question_id"], "details": "잘못된 과거 사건"},
                {"event_id": "event.answered", "event": "answer_recorded", "at": "2026-09-18T14:35:00+09:00", "actor": "main", "question_id": question["question_id"], "response_id": "response.source", "details": "답변 기록"}
            ])
            packet["responses"] = [{
                "response_id": "response.source", "question_id": question["question_id"],
                "session_id": packet["session"]["session_id"], "answer_mode": "choice",
                "selected_choice_id": "confirm", "exact_text": "승인",
                "answered_by": "project_owner", "answered_at": "2026-09-18T14:35:00+09:00",
                "authority_used": "department_confirmation", "authorization_event_id": "event.granted",
                "normalization_status": "accepted"
            }]

        errors = self.mutate(change)
        self.assertTrue(any("outside the session window" in error for error in errors))
        self.assertTrue(any("prior question_presented" in error for error in errors))

    def test_audit_events_cannot_reference_unknown_objects(self) -> None:
        def change(packet):
            packet["audit_events"].append(
                {"event_id": "event.unknown", "event": "answer_recorded", "at": "2026-09-18T14:31:00+09:00", "actor": "main", "question_id": "question.unknown", "response_id": "response.unknown", "details": "잘못된 참조"}
            )

        errors = self.mutate(change)
        self.assertTrue(any("does not match a current response" in error for error in errors))

    def test_fabricated_conflict_is_rejected(self) -> None:
        def change(packet):
            question = packet["batches"][0]["questions"][0]
            configure_conflict_question(question)
            packet["conflicts"] = [{
                "conflict_id": "conflict.fake",
                "subject_ids": question["subject_ids"],
                "higher_source_id": "missing.higher",
                "lower_claim_source": "missing.lower",
                "higher_locator": "invented",
                "lower_locator": "invented",
                "higher_claim": "invented",
                "lower_claim": "invented",
                "status": "preserved",
                "resolution_required": "official_higher_or_equal_authority_source"
            }]

        errors = self.mutate(change)
        self.assertTrue(any("references unknown sources" in error for error in errors))

    def test_conflict_sides_must_differ(self) -> None:
        def change(packet):
            question = packet["batches"][0]["questions"][0]
            configure_conflict_question(question)
            evidence = question["evidence"][0]
            packet["conflicts"] = [{
                "conflict_id": "conflict.identical",
                "subject_ids": question["subject_ids"],
                "higher_source_id": evidence["source_id"],
                "lower_claim_source": evidence["source_id"],
                "higher_locator": evidence["locator"],
                "lower_locator": evidence["locator"],
                "higher_claim": evidence["claim"],
                "lower_claim": evidence["claim"],
                "status": "preserved",
                "resolution_required": "official_higher_or_equal_authority_source"
            }]

        errors = self.mutate(change)
        self.assertTrue(any("has identical sides" in error for error in errors))
        self.assertTrue(any("has no contradictory claims" in error for error in errors))

    def test_source_excerpt_conflict_is_representable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            packet_path = self.make_project(project)
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            question = packet["batches"][1]["questions"][0]
            subject_id = "cwnu.cs.2026.credits.general-foundation"
            rule_path = project / "knowledge" / "rules" / f"{subject_id}.json"
            rule = json.loads(rule_path.read_text(encoding="utf-8"))
            alternate = {
                "source_id": "cwnu.curriculum.2026.changwon-undergraduate",
                "locator": "PDF p.23 (printed p.15), p.261 (printed p.253), p.577 (printed p.569): 대조 표기",
                "evidence_type": "explicit_text",
                "excerpt": "기초교양 8",
            }
            rule["evidence"].append(alternate)
            rule_path.write_text(json.dumps(rule, ensure_ascii=False), encoding="utf-8")
            snapshot = next(
                item for item in packet["input_snapshots"]
                if item["artifact_id"] == f"rule:{subject_id}"
            )
            snapshot["sha256"] = canonical_sha256(rule)
            question["evidence"].append({
                "subject_id": subject_id,
                "source_id": alternate["source_id"],
                "authority": "curriculum",
                "locator": alternate["locator"],
                "claim": alternate["excerpt"],
            })
            configure_conflict_question(question)
            original = next(
                item for item in question["evidence"]
                if item["subject_id"] == subject_id and item["claim"] == "기초교양 9"
            )
            packet["conflicts"] = [{
                "conflict_id": "conflict.representable",
                "subject_ids": [subject_id],
                "higher_source_id": original["source_id"],
                "lower_claim_source": alternate["source_id"],
                "higher_locator": original["locator"],
                "lower_locator": alternate["locator"],
                "higher_claim": original["claim"],
                "lower_claim": alternate["excerpt"],
                "status": "preserved",
                "resolution_required": "official_higher_or_equal_authority_source",
            }]
            packet_path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(validate_clarifications(project), [])

            alternate["excerpt"] = "기초교양 9학점"
            snapshot["sha256"] = canonical_sha256(rule)
            question["evidence"][-1]["claim"] = alternate["excerpt"]
            packet["conflicts"][0]["lower_claim"] = alternate["excerpt"]
            rule_path.write_text(json.dumps(rule, ensure_ascii=False), encoding="utf-8")
            packet_path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
            errors = validate_clarifications(project)
            self.assertTrue(any("has no contradictory claims" in error for error in errors))

    def test_conflict_triage_cannot_offer_or_apply_approval(self) -> None:
        def change(packet):
            question = packet["batches"][0]["questions"][0]
            question["purpose"] = "conflict_triage"
            question["status"] = "accepted"

        errors = self.mutate(change)
        self.assertTrue(any("approve" in error for error in errors))
        self.assertTrue(any("accepted" in error for error in errors))

    def test_draft_packet_cannot_contain_active_review(self) -> None:
        def change(packet):
            packet["state"] = "draft"
            packet["session"]["state"] = "active"
            packet["session"]["authority"]["status"] = "granted"
            packet["audit_events"].append(
                {"event_id": "event.granted", "event": "authority_granted", "at": "2026-09-18T14:31:00+09:00", "actor": "project_owner", "details": "권한 부여"}
            )

        errors = self.mutate(change)
        self.assertTrue(any("draft packet must be queued" in error for error in errors))

    def test_single_answer_needing_clarification_has_representable_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            path = self.make_project(project)
            packet = json.loads(path.read_text(encoding="utf-8"))
            question = packet["batches"][0]["questions"][0]
            packet["state"] = "partially_answered"
            packet["batches"][0]["status"] = "answered"
            question["status"] = "answered"
            packet["session"]["state"] = "active"
            packet["session"]["authority"]["status"] = "granted"
            packet["audit_events"].extend([
                {"event_id": "event.granted", "event": "authority_granted", "at": "2026-09-18T14:31:00+09:00", "actor": "project_owner", "details": "권한 부여"},
                {"event_id": "event.presented", "event": "question_presented", "at": "2026-09-18T14:32:00+09:00", "actor": "main", "question_id": question["question_id"], "details": "질문 제시"},
                {"event_id": "event.answered", "event": "answer_recorded", "at": "2026-09-18T14:35:00+09:00", "actor": "main", "question_id": question["question_id"], "response_id": "response.source", "details": "답변 기록"}
            ])
            packet["responses"] = [{
                "response_id": "response.source", "question_id": question["question_id"],
                "session_id": packet["session"]["session_id"], "answer_mode": "free_text",
                "exact_text": "잘 모르겠습니다", "answered_by": "project_owner",
                "answered_at": "2026-09-18T14:35:00+09:00",
                "authority_used": "department_confirmation", "authorization_event_id": "event.granted",
                "normalization_status": "needs_clarification"
            }]
            path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(validate_clarifications(project), [])

    def test_terminal_audit_events_require_main_actor(self) -> None:
        def change(packet):
            packet["state"] = "closed"
            packet["session"].update(
                state="closed", ended_at="2026-09-18T14:36:00+09:00", end_reason="user_closed"
            )
            packet["session"]["authority"]["status"] = "declined"
            packet["audit_events"].extend([
                {"event_id": "event.declined", "event": "authority_declined", "at": "2026-09-18T14:36:00+09:00", "actor": "academic_rule_modeler", "details": "거절"},
                {"event_id": "event.closed", "event": "closed", "at": "2026-09-18T14:36:00+09:00", "actor": "project_owner", "details": "종료"}
            ])

        errors = self.mutate(change)
        self.assertTrue(any("must be recorded by" in error for error in errors))

    def test_comment_required_choice_needs_separate_comment(self) -> None:
        def change(packet):
            question = packet["batches"][0]["questions"][0]
            question["status"] = "needs_revision"
            packet["audit_events"].append(
                {"event_id": "event.granted", "event": "authority_granted", "at": "2026-09-18T14:31:00+09:00", "actor": "project_owner", "details": "권한 부여"}
            )
            packet["responses"] = [{
                "response_id": "response.source", "question_id": question["question_id"],
                "session_id": packet["session"]["session_id"], "answer_mode": "choice",
                "selected_choice_id": "correct", "exact_text": "수정 필요",
                "answered_by": "project_owner", "answered_at": "2026-09-18T14:35:00+09:00",
                "authority_used": "department_confirmation", "authorization_event_id": "event.granted",
                "normalization_status": "accepted"
            }]

        errors = self.mutate(change)
        self.assertTrue(any("requires a comment" in error for error in errors))

    def test_pending_subject_cannot_be_suppressed_as_approved(self) -> None:
        def change(packet):
            first = packet["batches"][0]["questions"][0]
            hidden = [
                subject
                for batch in packet["batches"]
                for question in batch["questions"]
                for subject in question["subject_ids"]
                if subject not in first["subject_ids"]
            ]
            packet["batches"] = [packet["batches"][0]]
            packet["suppressed"] = [{
                "subject_ids": hidden,
                "reason": "already_approved",
                "evidence_locators": ["invented"]
            }]

        errors = self.mutate(change)
        self.assertTrue(any("pending subject cannot be suppressed" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
