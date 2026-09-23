#!/usr/bin/env python3
"""Read-only readiness checks for a private localhost academic-assistant pilot."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    return parser


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _git_ignored(project: Path, candidate: Path) -> bool:
    try:
        relative = candidate.relative_to(project)
    except ValueError:
        return False
    completed = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", relative.as_posix()],
        cwd=project,
        check=False,
        capture_output=True,
    )
    return completed.returncode == 0


def _writable_destination(candidate: Path) -> bool:
    if candidate.exists():
        return candidate.is_file() and os.access(candidate, os.W_OK)
    parent = candidate.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return parent.is_dir() and os.access(parent, os.W_OK)


def run_checks(project: Path) -> dict[str, object]:
    project = project.resolve()
    sys.path.insert(0, str(project / "src"))

    from academic_assistant.core import AnswerEngine
    from academic_assistant.feedback import feedback_path, feedback_private_root
    from academic_assistant.models import AcademicAnswerRequest
    from academic_assistant.registry import Registry

    checks: list[dict[str, object]] = []

    def record(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    record(
        "python-version",
        sys.version_info[:2] == (3, 12),
        "Python 3.12 required" if sys.version_info[:2] != (3, 12) else "Python 3.12",
    )

    destination = feedback_path()
    if not destination.is_absolute():
        destination = project / destination
    destination = destination.resolve()
    private_root = feedback_private_root()
    if not private_root.is_absolute():
        private_root = project / private_root
    private_root = private_root.resolve()
    local_root = (project / ".local").resolve()
    safe_destination = (
        destination != private_root
        and _inside(destination, private_root)
        and _inside(private_root, local_root)
    )
    record(
        "feedback-private-location",
        safe_destination,
        "destination is below the approved project .local root" if safe_destination else "pilot requires a file below the project .local private root",
    )
    record(
        "feedback-git-ignore",
        _git_ignored(project, destination),
        "feedback destination is not publishable by git",
    )
    record(
        "feedback-writable-destination",
        _writable_destination(destination),
        "existing file or nearest parent is writable" if _writable_destination(destination) else "feedback destination is not writable",
    )

    registry = Registry.load(project)
    engine = AnswerEngine(registry)
    supported = engine.answer(
        AcademicAnswerRequest(
            question="졸업학점 기준은 몇 학점인가요?",
            admission_year=2026,
            matched_curriculum_year=2026,
            department="컴퓨터공학과",
            earned_credits={},
        )
    )
    record(
        "supported-answer",
        supported.status == "supported" and bool(supported.evidence_packet.evidence),
        "approved rule and evidence connected",
    )
    withheld = engine.answer(
        AcademicAnswerRequest(
            question="PCCP 기준은 무엇인가요?",
            admission_year=2026,
            matched_curriculum_year=2026,
            department="컴퓨터공학과",
            earned_credits={},
        )
    )
    record(
        "protected-topic",
        withheld.status == "insufficient_evidence"
        and not withheld.evidence_packet.applied_rules
        and not withheld.evidence_packet.evidence,
        "unverified operating requirement remains fail-closed",
    )

    from fastapi.testclient import TestClient
    from academic_assistant.api import app

    client = TestClient(app)
    ready_response = client.get("/readyz")
    answer_response = client.post(
        "/v1/academic/answers",
        json={
            "schema_version": "1.0.0",
            "question": "졸업학점 기준은 몇 학점인가요?",
            "admission_year": 2026,
            "matched_curriculum_year": 2026,
            "department": "컴퓨터공학과",
            "earned_credits": {},
        },
    )
    rejected_feedback = client.post(
        "/v1/academic/feedback",
        json={
            "schema_version": "1.0.0",
            "packet_id": "academic-" + "0" * 32,
            "status": "insufficient_evidence",
            "question": "PCCP 기준은 무엇인가요?",
            "earned_credits": {},
            "category": "missing_evidence",
            "consent_to_store": False,
        },
    )
    record(
        "http-api-boundaries",
        ready_response.status_code == 200
        and answer_response.status_code == 200
        and answer_response.json().get("status") == "supported"
        and rejected_feedback.status_code == 422,
        "ready, supported answer, and no-consent feedback rejection verified",
    )

    ready = all(bool(check["passed"]) for check in checks)
    return {
        "schema_version": "1.0.0",
        "ready": ready,
        "bind_policy": "127.0.0.1-only",
        "feedback_records_present": destination.is_file() and destination.stat().st_size > 0,
        "checks": checks,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_checks(args.project)
    except (OSError, ValueError, RuntimeError):
        result = {
            "schema_version": "1.0.0",
            "ready": False,
            "bind_policy": "127.0.0.1-only",
            "feedback_records_present": False,
            "checks": [{"name": "startup", "passed": False, "detail": "readiness check failed closed"}],
        }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        for check in result["checks"]:
            marker = "PASS" if check["passed"] else "FAIL"
            print(f"[{marker}] {check['name']}: {check['detail']}")
        print("READY" if result["ready"] else "NOT READY")
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
