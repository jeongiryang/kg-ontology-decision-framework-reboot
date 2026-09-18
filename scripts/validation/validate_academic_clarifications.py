#!/usr/bin/env python3
"""Validate academic clarification packets and their session-scoped authority."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

try:
    from .validate_academic_knowledge import canonical_sha256
except ImportError:  # Direct script execution from this directory.
    from validate_academic_knowledge import canonical_sha256


def _load(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{path.as_posix()}: cannot load JSON: {exc}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{path.as_posix()}: JSON root must be an object")
        return None
    return value


def _duplicates(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _pages(values: list[str]) -> set[int]:
    pages: set[int] = set()
    for value in values:
        logical = re.sub(r"\(printed\s+p\.\d+\)", "", value, flags=re.IGNORECASE)
        pages.update(int(match) for match in re.findall(r"(?:PDF\s+)?p\.(\d+)", logical))
    return pages


def _claim_signature(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"(?<=\d)\s*학점", "", normalized)
    return re.sub(r"[^0-9a-z가-힣]+", "", normalized)


def _expected_evidence(
    subject_id: str,
    subject: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    rules: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if subject["subject_type"] == "source":
        source = sources.get(subject_id, {})
        applicability = source.get("applicability", {})
        claim = (
            f"title={source.get('title')}; sha256={source.get('sha256')}; "
            f"authority={source.get('authority')}; "
            f"curriculum_years={','.join(map(str, applicability.get('curriculum_years', [])))}; "
            f"admission_years={','.join(map(str, applicability.get('admission_years', [])))}; "
            f"departments={','.join(applicability.get('departments', []))}"
        )
        return [{
            "subject_id": subject_id,
            "source_id": subject_id,
            "authority": source.get("authority"),
            "locator": " | ".join(subject.get("evidence_locators", [])),
            "claim": claim,
        }]
    rule = rules.get(subject_id, {})
    expected: list[dict[str, Any]] = []
    for item in rule.get("evidence", []):
        source = sources.get(item.get("source_id"), {})
        expected.append({
            "subject_id": subject_id,
            "source_id": item.get("source_id"),
            "authority": source.get("authority"),
            "locator": item.get("locator"),
            # Compare conflicts against each source's actual wording, not the
            # normalized RuleFact decision that may reconcile several sources.
            "claim": item.get("excerpt") or rule.get("decision", {}).get("statement"),
        })
    return expected


def validate_clarifications(project: Path) -> list[str]:
    project = project.resolve()
    errors: list[str] = []
    schema = json.loads(
        (project / "contracts" / "academic-clarification-packet.schema.json").read_text(
            encoding="utf-8"
        )
    )
    validator = Draft202012Validator(schema, format_checker=FormatChecker())

    reviews: dict[str, tuple[dict[str, Any], Path]] = {}
    for review_path in sorted((project / "reviews" / "academic").glob("*.json")):
        review = _load(review_path, errors)
        if review is not None and isinstance(review.get("review_id"), str):
            reviews[review["review_id"]] = (review, review_path)

    sources: dict[str, dict[str, Any]] = {}
    rules: dict[str, dict[str, Any]] = {}
    for source_path in sorted((project / "knowledge" / "sources").glob("*.json")):
        source = _load(source_path, errors)
        if source is not None and isinstance(source.get("source_id"), str):
            sources[source["source_id"]] = source
    for rule_path in sorted((project / "knowledge" / "rules").glob("*.json")):
        rule = _load(rule_path, errors)
        if rule is not None and isinstance(rule.get("rule_id"), str):
            rules[rule["rule_id"]] = rule

    packet_dir = project / "reviews" / "academic" / "clarifications"
    packet_paths = sorted(packet_dir.glob("*.json")) if packet_dir.is_dir() else []
    if not packet_paths:
        errors.append("reviews/academic/clarifications: at least one packet is required")

    for path in packet_paths:
        relative = path.relative_to(project).as_posix()
        packet = _load(path, errors)
        if packet is None:
            continue
        schema_errors = list(validator.iter_errors(packet))
        for error in sorted(schema_errors, key=lambda item: list(item.path)):
            location = ".".join(str(part) for part in error.path) or "<root>"
            errors.append(f"{relative}:{location}: {error.message}")
        if schema_errors:
            continue

        review_record = reviews.get(packet["review_id"])
        if review_record is None:
            errors.append(f"{relative}: unknown review_id {packet['review_id']}")
            continue
        review, _ = review_record
        if packet["scope"] != review["scope"]:
            errors.append(f"{relative}: scope does not match review {packet['review_id']}")

        expected_snapshot = f"review:{packet['review_id']}"
        snapshots = {
            item["artifact_id"]: item["sha256"] for item in packet["input_snapshots"]
        }
        snapshot_ids = [item["artifact_id"] for item in packet["input_snapshots"]]
        for duplicate in _duplicates(snapshot_ids):
            errors.append(f"{relative}: duplicate input snapshot artifact_id {duplicate}")
        verification_snapshots = snapshots
        if packet["state"] == "applied":
            output_snapshots = packet["application"]["output_snapshots"]
            output_ids = [item["artifact_id"] for item in output_snapshots]
            for duplicate in _duplicates(output_ids):
                errors.append(f"{relative}: duplicate output snapshot artifact_id {duplicate}")
            verification_snapshots = {
                item["artifact_id"]: item["sha256"] for item in output_snapshots
            }
        if verification_snapshots.get(expected_snapshot) != canonical_sha256(review):
            errors.append(f"{relative}: review snapshot hash mismatch for {expected_snapshot}")

        review_subject_map = {item["subject_id"]: item for item in review["subjects"]}
        review_subjects = set(review_subject_map)
        objects = {**sources, **rules}
        expected_current_snapshot_ids = {expected_snapshot} | {
            f"{subject['subject_type']}:{subject_id}"
            for subject_id, subject in review_subject_map.items()
        }
        if packet["state"] == "applied" and set(verification_snapshots) != expected_current_snapshot_ids:
            errors.append(
                f"{relative}: applied output snapshots must exactly cover the review and all subjects"
            )
        covered: list[str] = []
        directly_questioned: set[str] = set()
        questions: dict[str, dict[str, Any]] = {}
        canonical_evidence_rows: list[dict[str, Any]] = []
        batch_ids = [batch["batch_id"] for batch in packet["batches"]]
        ordinals = [str(batch["ordinal"]) for batch in packet["batches"]]
        for duplicate in _duplicates(batch_ids):
            errors.append(f"{relative}: duplicate batch_id {duplicate}")
        for duplicate in _duplicates(ordinals):
            errors.append(f"{relative}: duplicate batch ordinal {duplicate}")

        for batch in packet["batches"]:
            for question in batch["questions"]:
                question_id = question["question_id"]
                if question_id in questions:
                    errors.append(f"{relative}: duplicate question_id {question_id}")
                questions[question_id] = question
                choice_ids = [choice["choice_id"] for choice in question["choices"]]
                for duplicate in _duplicates(choice_ids):
                    errors.append(f"{relative}: question {question_id} duplicates choice {duplicate}")
                if question["recommended_choice_id"] not in choice_ids:
                    errors.append(
                        f"{relative}: question {question_id} recommends an unknown choice"
                    )
                if set(question["blocking_subject_ids"]) != set(question["subject_ids"]):
                    errors.append(
                        f"{relative}: question {question_id} blocking subjects must equal subjects"
                    )
                covered.extend(question["subject_ids"])
                directly_questioned.update(question["subject_ids"])

                for subject_id in question["subject_ids"]:
                    subject = review_subject_map.get(subject_id)
                    if subject is None:
                        continue
                    prefix = subject["subject_type"]
                    artifact_id = f"{prefix}:{subject_id}"
                    current = objects.get(subject_id)
                    if current is None:
                        errors.append(f"{relative}: missing current object for {artifact_id}")
                    elif verification_snapshots.get(artifact_id) != canonical_sha256(current):
                        errors.append(f"{relative}: subject snapshot hash mismatch for {artifact_id}")
                    elif packet["state"] == "applied" and prefix in {"source", "rule"}:
                        preapproval = copy.deepcopy(current)
                        preapproval["review"] = {
                            "status": "needs_review",
                            "mode": "human",
                            "scope": "full",
                        }
                        if snapshots.get(artifact_id) != canonical_sha256(preapproval):
                            errors.append(
                                f"{relative}: approved subject content changed outside review metadata: "
                                f"{artifact_id}"
                            )
                for evidence in question["evidence"]:
                    source_id = evidence["source_id"]
                    source = sources.get(source_id)
                    artifact_id = f"source:{source_id}"
                    if source is None:
                        errors.append(f"{relative}: question {question_id} cites unknown source {source_id}")
                    elif verification_snapshots.get(artifact_id) != canonical_sha256(source):
                        errors.append(f"{relative}: evidence snapshot hash mismatch for {artifact_id}")
                expected_evidence: list[dict[str, Any]] = []
                for subject_id in question["subject_ids"]:
                    subject = review_subject_map.get(subject_id)
                    if subject is not None:
                        expected_evidence.extend(
                            _expected_evidence(subject_id, subject, sources, rules)
                        )
                evidence_key = lambda item: (
                    item.get("subject_id", ""), item.get("source_id", ""), item.get("locator", "")
                )
                if sorted(question["evidence"], key=evidence_key) != sorted(
                    expected_evidence, key=evidence_key
                ):
                    errors.append(
                        f"{relative}: question {question_id} evidence is not canonically bound to current subjects"
                    )
                canonical_evidence_rows.extend(expected_evidence)
                cited_pages = _pages([item["locator"] for item in question["evidence"]])
                cited_sources = {item["source_id"] for item in question["evidence"]}
                for subject_id in question["subject_ids"]:
                    subject = review_subject_map.get(subject_id)
                    if subject is None:
                        continue
                    if subject["subject_type"] == "rule":
                        rule = rules.get(subject_id, {})
                        registered_evidence = rule.get("evidence", [])
                        registered_pages = _pages(
                            [item.get("locator", "") for item in registered_evidence]
                        )
                        registered_sources = {
                            item.get("source_id") for item in registered_evidence
                        }
                    else:
                        registered_pages = _pages(subject.get("evidence_locators", []))
                        registered_sources = {subject_id}
                    if cited_pages != registered_pages:
                        errors.append(
                            f"{relative}: question {question_id} page locators do not match subject {subject_id}"
                        )
                    if not registered_sources.issubset(cited_sources):
                        errors.append(
                            f"{relative}: question {question_id} sources do not match subject {subject_id}"
                        )

        for suppressed in packet["suppressed"]:
            reason = suppressed["reason"]
            for subject_id in suppressed["subject_ids"]:
                subject = review_subject_map.get(subject_id)
                current = objects.get(subject_id)
                if reason == "duplicate_grouped":
                    if subject_id not in directly_questioned:
                        errors.append(
                            f"{relative}: duplicate_grouped subject is not present in a question: {subject_id}"
                        )
                    continue
                covered.append(subject_id)
                if subject is None or current is None:
                    continue
                if reason in {"already_approved", "resolved_by_higher_authority"}:
                    if subject.get("status") != "approved" or current.get("review", {}).get(
                        "status"
                    ) != "approved":
                        errors.append(
                            f"{relative}: pending subject cannot be suppressed as {reason}: {subject_id}"
                        )
                    artifact_id = f"{subject['subject_type']}:{subject_id}"
                    if verification_snapshots.get(artifact_id) != canonical_sha256(current):
                        errors.append(
                            f"{relative}: suppressed subject snapshot mismatch for {artifact_id}"
                        )
                elif reason == "out_of_scope" and subject_id in review_subjects:
                    errors.append(
                        f"{relative}: in-scope review subject cannot be suppressed as out_of_scope: {subject_id}"
                    )
        for duplicate in _duplicates(covered):
            errors.append(f"{relative}: review subject covered more than once: {duplicate}")
        unknown_subjects = set(covered) - review_subjects
        for subject_id in sorted(unknown_subjects):
            errors.append(f"{relative}: unknown review subject {subject_id}")
        if packet["coverage_mode"] == "full_review" and set(covered) != review_subjects:
            missing = ", ".join(sorted(review_subjects - set(covered))) or "none"
            errors.append(f"{relative}: full_review coverage is incomplete; missing: {missing}")

        events = packet["audit_events"]
        event_ids = [event["event_id"] for event in events]
        response_ids = [response["response_id"] for response in packet["responses"]]
        for duplicate in _duplicates(event_ids):
            errors.append(f"{relative}: duplicate audit event {duplicate}")
        for duplicate in _duplicates(response_ids):
            errors.append(f"{relative}: duplicate response {duplicate}")
        event_map = {event["event_id"]: event for event in events}
        expected_event_actors = {
            "authority_granted": "project_owner",
            "authority_declined": "project_owner",
            "question_presented": "main",
            "answer_recorded": "main",
            "applied": "academic_rule_modeler",
            "authority_expired": "main",
            "authority_revoked": "main",
            "closed": "main",
        }
        for event in events:
            expected_actor = expected_event_actors.get(event["event"])
            if expected_actor is not None and event["actor"] != expected_actor:
                errors.append(
                    f"{relative}: audit event {event['event_id']} must be recorded by {expected_actor}"
                )
        grant_events = [event for event in events if event["event"] == "authority_granted"]
        terminal_events = [
            event
            for event in events
            if event["event"] in {
                "authority_declined", "authority_expired", "authority_revoked", "closed"
            }
        ]
        decline_events = [event for event in events if event["event"] == "authority_declined"]
        expiry_events = [event for event in events if event["event"] == "authority_expired"]
        revoke_events = [event for event in events if event["event"] == "authority_revoked"]
        created_events = [event for event in events if event["event"] == "created"]
        closed_events = [event for event in events if event["event"] == "closed"]
        applied_events = [event for event in events if event["event"] == "applied"]
        answered_questions: set[str] = set()
        session_id = packet["session"]["session_id"]
        session_started = _time(packet["session"]["started_at"])
        session_ended = (
            _time(packet["session"]["ended_at"])
            if packet["session"].get("ended_at")
            else None
        )
        session_state = packet["session"]["state"]
        authority_status = packet["session"]["authority"]["status"]
        if len(created_events) != 1 or created_events[0]["actor"] != "main" or _time(
            created_events[0]["at"]
        ) != session_started:
            errors.append(f"{relative}: session requires one main-created event at started_at")
        for label, matching in (
            ("authority_granted", grant_events),
            ("authority_declined", decline_events),
            ("authority_expired", expiry_events),
            ("authority_revoked", revoke_events),
            ("closed", closed_events),
            ("applied", applied_events),
        ):
            if len(matching) > 1:
                errors.append(f"{relative}: session contains multiple {label} events")
        for event in events:
            event_at = _time(event["at"])
            if event_at < session_started or (
                session_ended is not None and event_at > session_ended
            ):
                errors.append(f"{relative}: audit event {event['event_id']} is outside the session window")
        if session_state == "awaiting_authority" and (
            grant_events or decline_events or expiry_events or revoke_events or packet["responses"]
        ):
            errors.append(
                f"{relative}: awaiting_authority session cannot contain authority transitions or responses"
            )
        if session_state == "active" and (
            authority_status != "granted"
            or len(grant_events) != 1
            or grant_events[0]["actor"] != "project_owner"
            or terminal_events
        ):
            errors.append(f"{relative}: active session requires a live project_owner grant")
        if session_state == "review_only" and any(
            response["authority_used"] == "department_confirmation"
            for response in packet["responses"]
        ):
            errors.append(f"{relative}: review_only session cannot use department_confirmation")
        if session_state == "review_only" and (
            len(decline_events) != 1
            or decline_events[0]["actor"] != "project_owner"
            or grant_events
            or expiry_events
            or revoke_events
        ):
            errors.append(f"{relative}: review_only session requires an authority_declined event")
        for event in grant_events:
            event_at = _time(event["at"])
            if event["actor"] != "project_owner" or event_at < session_started or (
                session_ended is not None and event_at > session_ended
            ):
                errors.append(f"{relative}: invalid authority_granted event {event['event_id']}")
        for response in packet["responses"]:
            question_id = response["question_id"]
            question = questions.get(question_id)
            if question is None:
                errors.append(f"{relative}: response references unknown question {question_id}")
                continue
            if response["session_id"] != session_id:
                errors.append(f"{relative}: response {response['response_id']} uses another session")
            if question_id in answered_questions:
                errors.append(f"{relative}: multiple responses for question {question_id}")
            answered_questions.add(question_id)
            if response["authority_used"] != question["required_authority"]:
                errors.append(
                    f"{relative}: response {response['response_id']} does not use the question's required authority"
                )
            answered_at = _time(response["answered_at"])
            if answered_at < session_started or (
                session_ended is not None and answered_at > session_ended
            ):
                errors.append(
                    f"{relative}: response {response['response_id']} is outside the session time window"
                )
            if response["answer_mode"] == "choice":
                choices = {choice["choice_id"]: choice for choice in question["choices"]}
                selected = choices.get(response.get("selected_choice_id"))
                if selected is None:
                    errors.append(
                        f"{relative}: response {response['response_id']} selects an unknown choice"
                    )
                else:
                    if selected.get("comment_required") and not response.get("comment", "").strip():
                        errors.append(
                            f"{relative}: response {response['response_id']} requires a comment"
                        )
                    expected_status = {
                        "approve": "accepted",
                        "pending": "deferred",
                        "needs_revision": "needs_revision",
                        "provide_evidence": "needs_revision",
                    }[selected["disposition"]]
                    if response["normalization_status"] != "accepted":
                        errors.append(
                            f"{relative}: explicit choice response {response['response_id']} must be normalized as accepted"
                        )
                    if question["status"] != expected_status:
                        errors.append(
                            f"{relative}: response {response['response_id']} disposition requires question status {expected_status}"
                        )
            elif response["normalization_status"] == "accepted":
                errors.append(
                    f"{relative}: free-text response {response['response_id']} cannot be accepted without a normalized choice"
                )
            if response["normalization_status"] == "needs_clarification" and question["status"] not in {
                "presented", "answered"
            }:
                errors.append(
                    f"{relative}: ambiguous response {response['response_id']} cannot finalize a question"
                )
            if response["normalization_status"] == "conflict_preserved":
                conflict_map = {item["conflict_id"]: item for item in packet["conflicts"]}
                conflict = conflict_map.get(response.get("conflict_id"))
                if (
                    question["status"] != "conflict_preserved"
                    or conflict is None
                    or not set(conflict["subject_ids"]).issubset(question["subject_ids"])
                ):
                    errors.append(
                        f"{relative}: conflict response {response['response_id']} requires a preserved conflict"
                    )
            if response["normalization_status"] == "unprocessed" and question["status"] not in {
                "presented", "answered"
            }:
                errors.append(
                    f"{relative}: unprocessed response {response['response_id']} cannot finalize a question"
                )
            if response["authority_used"] == "department_confirmation":
                if response["answered_by"] != "project_owner":
                    errors.append(
                        f"{relative}: response {response['response_id']} department confirmation must be answered by project_owner"
                    )
                event = event_map.get(response.get("authorization_event_id"))
                if (
                    event is None
                    or event["event"] != "authority_granted"
                    or event["actor"] != "project_owner"
                ):
                    errors.append(
                        f"{relative}: response {response['response_id']} lacks a valid authority_granted event"
                    )
                    continue
                if _time(event["at"]) > answered_at:
                    errors.append(
                        f"{relative}: response {response['response_id']} predates its authority grant"
                    )
                if any(_time(item["at"]) <= answered_at for item in terminal_events):
                    errors.append(
                        f"{relative}: response {response['response_id']} was recorded after authority ended"
                    )
            presentation_events = [
                event for event in events
                if event["event"] == "question_presented"
                and event.get("question_id") == question_id
                and _time(event["at"]) >= session_started
                and _time(event["at"]) <= answered_at
            ]
            answer_events = [
                event for event in events
                if event["event"] == "answer_recorded"
                and event.get("question_id") == question_id
                and event.get("response_id") == response["response_id"]
                and event["at"] == response["answered_at"]
            ]
            if len(presentation_events) != 1:
                errors.append(
                    f"{relative}: response {response['response_id']} requires exactly one prior question_presented event"
                )
            if len(answer_events) != 1:
                errors.append(
                    f"{relative}: response {response['response_id']} requires exactly one matching answer_recorded event"
                )

        response_map = {response["response_id"]: response for response in packet["responses"]}
        for event in events:
            if event["event"] == "question_presented" and event.get("question_id") not in questions:
                errors.append(
                    f"{relative}: audit event {event['event_id']} references an unknown question"
                )
            if event["event"] == "answer_recorded":
                response = response_map.get(event.get("response_id"))
                if (
                    response is None
                    or event.get("question_id") not in questions
                    or response.get("question_id") != event.get("question_id")
                    or response.get("answered_at") != event.get("at")
                ):
                    errors.append(
                        f"{relative}: audit event {event['event_id']} does not match a current response"
                    )

        authority_rank = {
            "university_statute": 0,
            "curriculum": 1,
            "department_guidance": 2,
            "validation_aid": 3,
        }
        evidence_tuples = {
            (row["subject_id"], row["source_id"], row["locator"], row["claim"])
            for row in canonical_evidence_rows
        }
        conflict_ids: set[str] = set()
        for conflict in packet["conflicts"]:
            conflict_id = conflict["conflict_id"]
            if conflict_id in conflict_ids:
                errors.append(f"{relative}: duplicate conflict_id {conflict_id}")
            conflict_ids.add(conflict_id)
            higher_side = (
                conflict["higher_source_id"], conflict["higher_locator"], conflict["higher_claim"]
            )
            lower_side = (
                conflict["lower_claim_source"], conflict["lower_locator"], conflict["lower_claim"]
            )
            if higher_side == lower_side:
                errors.append(f"{relative}: conflict {conflict_id} has identical sides")
            if _claim_signature(conflict["higher_claim"]) == _claim_signature(
                conflict["lower_claim"]
            ):
                errors.append(f"{relative}: conflict {conflict_id} has no contradictory claims")
            if not set(conflict["subject_ids"]).issubset(review_subjects):
                errors.append(f"{relative}: conflict {conflict_id} references unknown subjects")
            higher = sources.get(conflict["higher_source_id"])
            lower = sources.get(conflict["lower_claim_source"])
            if higher is None or lower is None:
                errors.append(f"{relative}: conflict {conflict_id} references unknown sources")
                continue
            for source_id, source in (
                (conflict["higher_source_id"], higher),
                (conflict["lower_claim_source"], lower),
            ):
                if verification_snapshots.get(f"source:{source_id}") != canonical_sha256(source):
                    errors.append(f"{relative}: conflict {conflict_id} source snapshot mismatch")
            if authority_rank[higher["authority"]] > authority_rank[lower["authority"]]:
                errors.append(f"{relative}: conflict {conflict_id} reverses source precedence")
            for subject_id in conflict["subject_ids"]:
                higher_tuple = (
                    subject_id, conflict["higher_source_id"], conflict["higher_locator"],
                    conflict["higher_claim"]
                )
                lower_tuple = (
                    subject_id, conflict["lower_claim_source"], conflict["lower_locator"],
                    conflict["lower_claim"]
                )
                if higher_tuple not in evidence_tuples or lower_tuple not in evidence_tuples:
                    errors.append(
                        f"{relative}: conflict {conflict_id} is not bound to canonical subject evidence"
                    )
            if not any(
                question["purpose"] == "conflict_triage"
                and set(conflict["subject_ids"]).issubset(question["subject_ids"])
                for question in questions.values()
            ):
                errors.append(f"{relative}: conflict {conflict_id} has no conflict-triage question")

        response_required = {
            "answered", "accepted", "needs_revision", "deferred", "conflict_preserved"
        }
        for question_id, question in questions.items():
            if question["status"] in response_required and question_id not in answered_questions:
                errors.append(f"{relative}: question {question_id} status requires a response")
            if question["purpose"] == "conflict_triage" and not packet["conflicts"]:
                errors.append(f"{relative}: conflict-triage question {question_id} requires a preserved conflict")
            if question["purpose"] == "conflict_triage" and any(
                choice["disposition"] == "approve" for choice in question["choices"]
            ):
                errors.append(
                    f"{relative}: conflict-triage question {question_id} cannot offer approve"
                )
            if question["purpose"] == "conflict_triage" and question["status"] == "accepted":
                errors.append(
                    f"{relative}: conflict-triage question {question_id} cannot be accepted"
                )
            if question_id not in answered_questions:
                for subject_id in question["subject_ids"]:
                    subject = review_subject_map.get(subject_id, {})
                    current = objects.get(subject_id, {})
                    if subject.get("status") not in {"pending", "needs_revision"}:
                        errors.append(
                            f"{relative}: unanswered subject is not fail-closed in review queue: {subject_id}"
                        )
                    if current.get("review", {}).get("status") not in {
                        "pending", "needs_review"
                    }:
                        errors.append(
                            f"{relative}: unanswered subject is not fail-closed in current object: {subject_id}"
                        )
            if question["status"] == "presented":
                presented = [
                    event for event in events
                    if event["event"] == "question_presented"
                    and event.get("question_id") == question_id
                    and session_started <= _time(event["at"])
                    and (session_ended is None or _time(event["at"]) <= session_ended)
                ]
                if len(presented) != 1:
                    errors.append(
                        f"{relative}: presented question {question_id} requires exactly one in-session presentation event"
                    )

        terminal_question_statuses = {
            "accepted", "needs_revision", "deferred", "conflict_preserved"
        }
        for batch in packet["batches"]:
            statuses = {question["status"] for question in batch["questions"]}
            if batch["status"] == "queued" and statuses != {"queued"}:
                errors.append(f"{relative}: queued batch {batch['batch_id']} has non-queued questions")
            if batch["status"] in {"applied", "closed"} and not statuses.issubset(
                terminal_question_statuses
            ):
                errors.append(
                    f"{relative}: terminal batch {batch['batch_id']} has unfinished questions"
                )
            if batch["status"] == "presented" and (
                "presented" not in statuses
                or not statuses.issubset({"queued", "presented"})
            ):
                errors.append(f"{relative}: presented batch {batch['batch_id']} has invalid question states")
            batch_question_ids = {question["question_id"] for question in batch["questions"]}
            batch_response_count = len(batch_question_ids & answered_questions)
            if batch["status"] == "answered" and (
                batch_response_count != len(batch_question_ids)
                or not statuses.issubset(terminal_question_statuses | {"answered"})
            ):
                errors.append(
                    f"{relative}: answered batch {batch['batch_id']} requires a response for every question"
                )
            if batch["status"] == "partially_answered" and not (
                0 < batch_response_count < len(batch_question_ids)
            ):
                errors.append(
                    f"{relative}: partially_answered batch {batch['batch_id']} needs some but not all responses"
                )
        packet_state = packet["state"]
        batch_statuses = {batch["status"] for batch in packet["batches"]}
        if packet_state == "ready" and (
            batch_statuses != {"queued"} or session_state != "awaiting_authority"
        ):
            errors.append(f"{relative}: ready packet must have queued batches and awaiting authority")
        if packet_state == "draft" and (
            batch_statuses != {"queued"}
            or session_state != "awaiting_authority"
            or packet["responses"]
            or grant_events
        ):
            errors.append(
                f"{relative}: draft packet must be queued, unanswered, and awaiting authority"
            )
        if packet_state == "answered" and set(questions) != answered_questions:
            errors.append(f"{relative}: answered packet has unanswered questions")
        if packet_state == "applied" and batch_statuses != {"applied"}:
            errors.append(f"{relative}: applied packet requires every batch to be applied")
        if packet_state == "applied":
            application = packet["application"]
            matching_application_events = [
                event for event in applied_events
                if event["event_id"] == application["application_event_id"]
                and event["at"] == application["applied_at"]
            ]
            if len(matching_application_events) != 1:
                errors.append(f"{relative}: applied packet lacks its matching application audit event")
            if session_state != "closed":
                errors.append(f"{relative}: applied packet requires a closed session")
            if session_ended is not None and _time(application["applied_at"]) > session_ended:
                errors.append(f"{relative}: application occurred after the session ended")
            if packet["responses"] and _time(application["applied_at"]) < max(
                _time(response["answered_at"]) for response in packet["responses"]
            ):
                errors.append(f"{relative}: application predates a recorded response")
            response_snapshots = {
                item["response_id"]: item["sha256"]
                for item in application["response_snapshots"]
            }
            if len(response_snapshots) != len(application["response_snapshots"]):
                errors.append(f"{relative}: duplicate applied response snapshot")
            if set(response_snapshots) != set(response_map):
                errors.append(f"{relative}: applied response snapshots must exactly cover responses")
            for response_id, response in response_map.items():
                if response_snapshots.get(response_id) != canonical_sha256(response):
                    errors.append(f"{relative}: applied response snapshot mismatch for {response_id}")

            subject_question = {
                subject_id: question_id
                for question_id, question in questions.items()
                for subject_id in question["subject_ids"]
            }
            response_by_question = {
                response["question_id"]: response for response in packet["responses"]
            }
            attestations = application["subject_attestations"]
            attestation_map = {item["subject_id"]: item for item in attestations}
            if len(attestation_map) != len(attestations):
                errors.append(f"{relative}: duplicate subject attestation")
            if set(attestation_map) != review_subjects:
                errors.append(f"{relative}: subject attestations must exactly cover review subjects")
            for subject_id in sorted(review_subjects):
                attestation = attestation_map.get(subject_id)
                if attestation is None:
                    continue
                question_id = subject_question.get(subject_id)
                response = response_by_question.get(question_id)
                current = objects.get(subject_id)
                review_subject = review_subject_map.get(subject_id)
                if (
                    question_id is None
                    or response is None
                    or attestation["question_id"] != question_id
                    or attestation["response_id"] != response["response_id"]
                ):
                    errors.append(f"{relative}: invalid question/response binding for {subject_id}")
                if current is not None and attestation["object_review_sha256"] != canonical_sha256(
                    current.get("review", {})
                ):
                    errors.append(f"{relative}: object review attestation mismatch for {subject_id}")
                if review_subject is not None and attestation[
                    "review_subject_sha256"
                ] != canonical_sha256(review_subject):
                    errors.append(f"{relative}: review queue attestation mismatch for {subject_id}")
        if packet_state == "closed" and session_state != "closed":
            errors.append(f"{relative}: closed packet requires a closed session")
        if packet_state == "awaiting_user" and (
            session_state not in {"active", "review_only"}
            or not any(batch["status"] == "presented" for batch in packet["batches"])
        ):
            errors.append(f"{relative}: awaiting_user packet requires an active presented batch")
        if packet_state == "partially_answered" and not (
            0 < len(answered_questions) < len(questions)
        ):
            errors.append(f"{relative}: partially_answered packet needs some but not all responses")

        if session_state == "closed":
            ended_at = packet["session"]["ended_at"]
            expected_authority_event = {
                "expired": "authority_expired",
                "revoked": "authority_revoked",
                "declined": "authority_declined",
            }[authority_status]
            if not any(
                event["event"] == expected_authority_event and event["at"] == ended_at
                for event in events
            ):
                errors.append(
                    f"{relative}: closed session lacks matching {expected_authority_event} event at ended_at"
                )
            if not any(event["event"] == "closed" and event["at"] == ended_at for event in events):
                errors.append(f"{relative}: closed session lacks a closed event at ended_at")
            if authority_status in {"expired", "revoked"} and len(grant_events) != 1:
                errors.append(
                    f"{relative}: closed {authority_status} session requires exactly one prior grant"
                )
            ending_events = expiry_events if authority_status == "expired" else revoke_events
            if authority_status in {"expired", "revoked"} and grant_events and ending_events:
                if _time(grant_events[0]["at"]) >= _time(ending_events[0]["at"]):
                    errors.append(
                        f"{relative}: closed {authority_status} session has invalid authority event order"
                    )
            if authority_status == "declined" and (
                len(decline_events) != 1 or grant_events
            ):
                errors.append(
                    f"{relative}: closed declined session requires one decline and no grant"
                )
            expected_terminal_counts = {
                "declined": (1, 0, 0),
                "expired": (0, 1, 0),
                "revoked": (0, 0, 1),
            }[authority_status]
            actual_terminal_counts = (
                len(decline_events), len(expiry_events), len(revoke_events)
            )
            if actual_terminal_counts != expected_terminal_counts:
                errors.append(
                    f"{relative}: closed session authority terminal events are not mutually exclusive"
                )

    return sorted(set(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    errors = validate_clarifications(args.project)
    if errors:
        for error in errors:
            print(f"academic-clarifications: error: {error}", file=sys.stderr)
        return 1
    print("academic-clarifications: packet coverage and session authority passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
