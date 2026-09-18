"""Validate, sanitize, and render CompletionReport documents.

The public JSON is the sanitized source of truth for Markdown and PDF output.
The module intentionally does not collect prompts, model reasoning, or raw logs.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from html import escape as xml_escape
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "1.0.0"
REQUIRED_FIELDS = {
    "schema_version",
    "run_id",
    "title",
    "request",
    "summary",
    "harness_version",
    "git",
    "changes",
    "agents",
    "checks",
    "academic_sources_changed",
    "issues",
    "publication",
}
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SECRET_KEY_RE = re.compile(
    r"(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth(?:orization)?|"
    r"private[_-]?key|ssh[_-]?key|cookie|client[_-]?secret)",
    re.IGNORECASE,
)
STUDENT_KEY_RE = re.compile(
    r"(?:student[_-]?(?:id|number|name)|studentNo|학번|학생[_ ]?(?:이름|성명|식별))",
    re.IGNORECASE,
)
HIDDEN_REASONING_KEY_RE = re.compile(
    r"(?:chain[_-]?of[_-]?thought|hidden[_-]?reasoning|system[_-]?prompt|raw[_-]?prompt)",
    re.IGNORECASE,
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
STUDENT_VALUE_PATTERNS = (
    re.compile(r"(?:학번|student\s*(?:id|number))\s*[:=#-]?\s*\d{6,12}", re.IGNORECASE),
    re.compile(r"(?:학생\s*(?:이름|성명)|student\s*name)\s*[:=]\s*[^\s,;]{2,40}", re.IGNORECASE),
)
SERVER_CONNECTION_KEY_RE = re.compile(
    r"(?:ssh[_-]?(?:user|host|hostname|address)|server[_-]?(?:host|hostname|address)|"
    r"(?:host|hostname|ip)[_-]?address|connection[_-]?(?:host|address))",
    re.IGNORECASE,
)
SERVER_CONNECTION_VALUE_PATTERNS = (
    re.compile(r"\b(?:ssh|scp|sftp|rsync)\b[^\r\n]{0,200}?\b[A-Za-z_][\w.-]*@[A-Za-z0-9][A-Za-z0-9.-]*", re.IGNORECASE),
    re.compile(
        r"\bssh(?:\s+-[A-Za-z]\s+\S+)*\s+[A-Za-z0-9][A-Za-z0-9.-]*",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?<![\w@.-])[A-Za-z_][\w.-]*@"
        r"[A-Za-z0-9](?:[A-Za-z0-9-]*\.)+[A-Za-z]{2,63}(?::\S+)?"
    ),
    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    re.compile(
        r"(?<![\w:])(?=[0-9A-Fa-f:]*:)[0-9A-Fa-f]{0,4}"
        r"(?::[0-9A-Fa-f]{0,4}){2,7}(?![\w:])"
    ),
    re.compile(
        r"(?:ssh\s+(?:host|hostname|user)|server\s+(?:host|hostname|address)|"
        r"host(?:name)?|ip\s*address|서버\s*(?:주소|호스트)|SSH\s*(?:사용자|호스트))"
        r"\s*[:=]\s*[^\s,;]+",
        re.IGNORECASE,
    ),
)
WINDOWS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/](?:[^\s<>\"'`|]+[\\/]?)+)"
)
UNC_PATH_RE = re.compile(r"(?<![A-Za-z0-9])(?:\\\\[^\s<>\"'`|]+(?:\\[^\s<>\"'`|]+)+)")
UNIX_PATH_RE = re.compile(
    r"(?<![:A-Za-z0-9])/(?:home|Users|root|tmp|var|etc|opt|mnt|srv)(?:/[^\s<>\"'`]+)+"
)

ALLOWED_CHECK_STATUSES = {"passed", "failed", "not_run"}
ALLOWED_CHANGE_KINDS = {"added", "changed", "fixed", "removed", "security", "documentation"}


class ReportError(Exception):
    """Base class for public report generation failures."""


class ReportValidationError(ReportError):
    """The CompletionReport does not satisfy its public contract."""


class SensitiveDataError(ReportError):
    """The input contains data that must not enter a public report."""


def _json_pointer(parts: Sequence[str]) -> str:
    return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def _walk(value: Any, parts: tuple[str, ...] = ()):
    yield parts, value
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _walk(child, (*parts, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, (*parts, str(index)))


def assert_no_sensitive_data(report: Mapping[str, Any]) -> None:
    """Fail closed for secrets, student identifiers, and hidden-reasoning fields."""

    findings: list[str] = []
    for parts, value in _walk(report):
        key = parts[-1] if parts else ""
        if value not in (None, "", [], {}):
            if SECRET_KEY_RE.search(key):
                findings.append(f"비밀정보 필드 {_json_pointer(parts)}")
            if STUDENT_KEY_RE.search(key):
                findings.append(f"학생 식별 필드 {_json_pointer(parts)}")
            if HIDDEN_REASONING_KEY_RE.search(key):
                findings.append(f"비공개 추론/프롬프트 필드 {_json_pointer(parts)}")
            if SERVER_CONNECTION_KEY_RE.fullmatch(key):
                findings.append(f"서버 접속정보 필드 {_json_pointer(parts)}")
        if isinstance(value, str):
            if any(pattern.search(value) for pattern in SECRET_VALUE_PATTERNS):
                findings.append(f"비밀값 패턴 {_json_pointer(parts)}")
            if any(pattern.search(value) for pattern in STUDENT_VALUE_PATTERNS):
                findings.append(f"학생 식별값 패턴 {_json_pointer(parts)}")
            if any(pattern.search(value) for pattern in SERVER_CONNECTION_VALUE_PATTERNS):
                findings.append(f"서버 접속값 패턴 {_json_pointer(parts)}")

    if findings:
        unique = ", ".join(dict.fromkeys(findings))
        raise SensitiveDataError(f"공개 보고서 생성을 중단했습니다: {unique}")


def redact_absolute_paths(text: str) -> str:
    """Replace common Windows, UNC, and Unix absolute paths in narrative text."""

    redacted = WINDOWS_PATH_RE.sub("<redacted-path>", text)
    redacted = UNC_PATH_RE.sub("<redacted-path>", redacted)
    return UNIX_PATH_RE.sub("<redacted-path>", redacted)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_absolute_paths(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _sanitize_value(child) for key, child in value.items()}
    return value


def sanitize_report(report: Mapping[str, Any]) -> dict[str, Any]:
    assert_no_sensitive_data(report)
    sanitized = _sanitize_value(copy.deepcopy(dict(report)))
    assert_no_absolute_paths(sanitized)
    return sanitized


def find_absolute_paths(value: Any) -> list[str]:
    findings: list[str] = []
    for parts, child in _walk(value):
        if not isinstance(child, str):
            continue
        if WINDOWS_PATH_RE.search(child) or UNC_PATH_RE.search(child) or UNIX_PATH_RE.search(child):
            findings.append(_json_pointer(parts))
    return findings


def assert_no_absolute_paths(value: Any) -> None:
    findings = find_absolute_paths(value)
    if findings:
        raise SensitiveDataError(
            "절대경로 정제에 실패했습니다: " + ", ".join(findings)
        )


def _require_nonempty_string(container: Mapping[str, Any], field: str) -> None:
    if not isinstance(container.get(field), str) or not container[field].strip():
        raise ReportValidationError(f"{field} must be a non-empty string")


def _validate_relative_repo_path(path_value: Any, pointer: str) -> None:
    if not isinstance(path_value, str) or not path_value.strip():
        raise ReportValidationError(f"{pointer} must be a non-empty repository-relative path")
    normalized = path_value.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or re.match(r"^[A-Za-z]:/", normalized) or normalized.startswith("//"):
        raise ReportValidationError(f"{pointer} must not be absolute")
    if ".." in pure.parts:
        raise ReportValidationError(f"{pointer} must not escape the repository")


def _validate_internal_contract(report: Mapping[str, Any]) -> None:
    missing = sorted(REQUIRED_FIELDS.difference(report))
    if missing:
        raise ReportValidationError("missing required fields: " + ", ".join(missing))

    if report.get("schema_version") != SCHEMA_VERSION:
        raise ReportValidationError(f"schema_version must be {SCHEMA_VERSION}")
    _require_nonempty_string(report, "run_id")
    if not RUN_ID_RE.fullmatch(report["run_id"]):
        raise ReportValidationError("run_id contains unsafe filename characters")
    _require_nonempty_string(report, "title")
    _require_nonempty_string(report, "request")
    _require_nonempty_string(report, "summary")
    _require_nonempty_string(report, "harness_version")

    git_info = report.get("git")
    if not isinstance(git_info, Mapping):
        raise ReportValidationError("git must be an object")
    for field in ("branch", "base_commit", "head_commit"):
        _require_nonempty_string(git_info, field)
    for field in ("base_commit", "head_commit"):
        if not re.fullmatch(r"[0-9a-fA-F]{7,64}", git_info[field]):
            raise ReportValidationError(f"git/{field} must be a 7-64 character hexadecimal commit")

    changes = report.get("changes")
    if not isinstance(changes, list):
        raise ReportValidationError("changes must be an array")
    for index, change in enumerate(changes):
        if not isinstance(change, Mapping):
            raise ReportValidationError(f"changes/{index} must be an object")
        kind = change.get("category", change.get("kind", change.get("type")))
        if kind is not None and kind not in ALLOWED_CHANGE_KINDS:
            raise ReportValidationError(f"changes/{index} has unsupported category {kind!r}")
        if "summary" not in change and "description" not in change:
            raise ReportValidationError(f"changes/{index} requires summary or description")
        if "path" in change:
            _validate_relative_repo_path(change["path"], f"changes/{index}/path")

    agents = report.get("agents")
    if not isinstance(agents, list):
        raise ReportValidationError("agents must be an array")
    for index, agent in enumerate(agents):
        if not isinstance(agent, Mapping):
            raise ReportValidationError(f"agents/{index} must be an object")
        for field in ("role", "task_id", "status", "summary"):
            _require_nonempty_string(agent, field)
        if agent["status"] not in {"completed", "blocked", "failed"}:
            raise ReportValidationError(
                f"agents/{index}/status must be completed, blocked, or failed"
            )

    checks = report.get("checks")
    if not isinstance(checks, list):
        raise ReportValidationError("checks must be an array")
    for index, check in enumerate(checks):
        if not isinstance(check, Mapping):
            raise ReportValidationError(f"checks/{index} must be an object")
        _require_nonempty_string(check, "name")
        if check.get("status") not in ALLOWED_CHECK_STATUSES:
            raise ReportValidationError(
                f"checks/{index}/status must be passed, failed, or not_run"
            )
        if not isinstance(check.get("required"), bool):
            raise ReportValidationError(f"checks/{index}/required must be boolean")
        if check.get("status") == "passed" and not str(check.get("evidence", "")).strip():
            raise ReportValidationError(f"checks/{index} passed without evidence")
        if check.get("status") == "not_run" and not str(check.get("evidence", "")).strip():
            raise ReportValidationError(f"checks/{index} not_run requires a reason in evidence")

    academic_sources_changed = report.get("academic_sources_changed")
    if not isinstance(academic_sources_changed, list):
        raise ReportValidationError("academic_sources_changed must be an array")
    for index, source_change in enumerate(academic_sources_changed):
        if not isinstance(source_change, str) or not source_change.strip():
            raise ReportValidationError(
                f"academic_sources_changed/{index} must be a non-empty string"
            )

    if not isinstance(report.get("issues"), list):
        raise ReportValidationError("issues must be an array")
    if not isinstance(report.get("publication"), Mapping):
        raise ReportValidationError("publication must be an object")


def _validate_json_schema(report: Mapping[str, Any], schema_path: Path) -> None:
    if not schema_path.is_file():
        raise ReportValidationError(f"CompletionReport schema not found: {schema_path}")
    try:
        import jsonschema
    except ImportError as exc:
        raise ReportError(
            "jsonschema is required when a contract schema is supplied; "
            "install scripts/reporting/requirements.txt"
        ) from exc

    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
        errors = sorted(validator_cls(schema).iter_errors(report), key=lambda item: list(item.path))
    except (OSError, json.JSONDecodeError, jsonschema.SchemaError) as exc:
        raise ReportValidationError(f"invalid CompletionReport schema: {exc}") from exc
    if errors:
        rendered = []
        for error in errors[:10]:
            pointer = _json_pointer(tuple(str(part) for part in error.absolute_path))
            rendered.append(f"{pointer}: {error.message}")
        raise ReportValidationError("schema validation failed: " + "; ".join(rendered))


def validate_completion_report(
    report: Mapping[str, Any], schema_path: Path | None = None
) -> None:
    if not isinstance(report, Mapping):
        raise ReportValidationError("CompletionReport root must be an object")
    _validate_internal_contract(report)
    assert_no_sensitive_data(report)
    if schema_path is not None:
        _validate_json_schema(report, schema_path)


def publication_requires_pdf(report: Mapping[str, Any]) -> bool:
    publication = report.get("publication", {})
    if not isinstance(publication, Mapping):
        return False
    return any(
        publication.get(key) is True
        for key in ("pdf_required", "major", "major_change", "generate_pdf")
    )


def _display(value: Any, default: str = "-") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _md_cell(value: Any) -> str:
    return _display(value).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def _change_fields(change: Mapping[str, Any]) -> tuple[str, str, str]:
    kind = change.get("category", change.get("kind", change.get("type", "changed")))
    path = change.get("path", "-")
    summary = change.get("summary", change.get("description", "-"))
    return _display(kind), _display(path), _display(summary)


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        f"# {_display(report['title'])}",
        "",
        f"- 실행 ID: `{_display(report['run_id'])}`",
        f"- 계약 버전: `{_display(report['schema_version'])}`",
        f"- 하네스 버전: `{_display(report['harness_version'])}`",
    ]
    git_info = report["git"]
    lines.extend(
        [
            f"- 브랜치: `{_display(git_info['branch'])}`",
            f"- 기준 커밋: `{_display(git_info['base_commit'])}`",
            f"- 결과 커밋: `{_display(git_info['head_commit'])}`",
            "",
            "## 요청",
            "",
            _display(report["request"]),
            "",
            "## 요약",
            "",
            _display(report["summary"]),
            "",
            "## 변경 사항",
            "",
        ]
    )

    changes = report.get("changes", [])
    if changes:
        lines.extend(["| 구분 | 파일 | 내용 |", "|---|---|---|"])
        for change in changes:
            kind, path, summary = _change_fields(change)
            lines.append(f"| {_md_cell(kind)} | `{_md_cell(path)}` | {_md_cell(summary)} |")
    else:
        lines.append("변경 사항 없음.")

    agents = report["agents"]
    lines.extend(["", "## 에이전트 결과", ""])
    if agents:
        lines.extend(["| 역할 | 작업 | 상태 | 결과 |", "|---|---|---|---|"])
        for agent in agents:
            lines.append(
                "| "
                + " | ".join(
                    _md_cell(value)
                    for value in (
                        agent["role"],
                        agent["task_id"],
                        agent["status"],
                        agent["summary"],
                    )
                )
                + " |"
            )
    else:
        lines.append("참여 에이전트 없음.")

    lines.extend(["", "## 검사 결과", ""])
    checks = report.get("checks", [])
    if checks:
        lines.extend(["| 검사 | 필수 | 상태 | 명령 | 근거 |", "|---|---:|---|---|---|"])
        for check in checks:
            lines.append(
                "| "
                + " | ".join(
                    _md_cell(value)
                    for value in (
                        check.get("name"),
                        check.get("required"),
                        check.get("status"),
                        check.get("command"),
                        check.get("evidence"),
                    )
                )
                + " |"
            )
    else:
        lines.append("기록된 검사 없음.")

    evidence_changes = report["academic_sources_changed"]
    lines.extend(["", "## 학사 근거 변경", ""])
    if evidence_changes:
        for item in evidence_changes:
            lines.append(f"- {_md_cell(item)}")
    else:
        lines.append("학사 근거 변경 없음.")

    lines.extend(["", "## 이슈 및 남은 작업", ""])
    issues = report.get("issues", [])
    if issues:
        for issue in issues:
            if isinstance(issue, Mapping):
                label = issue.get("severity", issue.get("status", "issue"))
                detail = issue.get("message", issue.get("summary", issue.get("description", issue)))
                lines.append(f"- **{_md_cell(label)}**: {_md_cell(detail)}")
            else:
                lines.append(f"- {_md_cell(issue)}")
    else:
        lines.append("알려진 미해결 이슈 없음.")

    publication = report.get("publication", {})
    lines.extend(
        [
            "",
            "## 공개 정보",
            "",
            f"- 주요 작업: `{_display(publication.get('major', publication.get('major_change', False)))}`",
            f"- PDF 필요: `{_display(publication_requires_pdf(report))}`",
            "",
        ]
    )
    return "\n".join(lines)


def _pdf_paragraph(text: Any, style):
    from reportlab.platypus import Paragraph

    safe = xml_escape(_display(text)).replace("\n", "<br/>")
    return Paragraph(safe, style)


def render_pdf(report: Mapping[str, Any], output_path: Path) -> None:
    try:
        from reportlab import rl_config
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfgen import canvas
        from reportlab.platypus import KeepTogether, PageBreak, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise ReportError(
            "reportlab is required for major-work PDFs; "
            "install scripts/reporting/requirements.txt"
        ) from exc

    rl_config.invariant = 1
    for font_name in ("HYSMyeongJo-Medium", "HYGothic-Medium"):
        try:
            pdfmetrics.getFont(font_name)
        except KeyError:
            pdfmetrics.registerFont(UnicodeCIDFont(font_name))

    styles = getSampleStyleSheet()
    body = ParagraphStyle(
        "KoreanBody",
        parent=styles["BodyText"],
        fontName="HYSMyeongJo-Medium",
        fontSize=9,
        leading=13,
        wordWrap="CJK",
        spaceAfter=3 * mm,
    )
    heading = ParagraphStyle(
        "KoreanHeading",
        parent=styles["Heading2"],
        fontName="HYGothic-Medium",
        fontSize=14,
        leading=18,
        spaceBefore=5 * mm,
        spaceAfter=3 * mm,
    )
    title_style = ParagraphStyle(
        "KoreanTitle",
        parent=heading,
        fontSize=20,
        leading=25,
        alignment=TA_CENTER,
        spaceAfter=7 * mm,
    )
    table_header = ParagraphStyle("KoreanTableHeader", parent=body, fontName="HYGothic-Medium")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent, delete=False
    )
    temp_file.close()
    temp_path = Path(temp_file.name)

    def invariant_canvas(*args, **kwargs):
        kwargs["invariant"] = 1
        kwargs["pageCompression"] = 1
        return canvas.Canvas(*args, **kwargs)

    def page_footer(pdf_canvas, doc):
        pdf_canvas.saveState()
        pdf_canvas.setFont("Helvetica", 8)
        pdf_canvas.setFillColor(colors.HexColor("#5B6472"))
        pdf_canvas.drawCentredString(A4[0] / 2, 11 * mm, f"{report['run_id']}  ·  {doc.page}")
        pdf_canvas.restoreState()

    try:
        doc = SimpleDocTemplate(
            str(temp_path),
            pagesize=A4,
            rightMargin=16 * mm,
            leftMargin=16 * mm,
            topMargin=18 * mm,
            bottomMargin=18 * mm,
            title=_display(report["title"]),
            author="KG Ontology Decision Framework Reboot",
            subject=f"CompletionReport {_display(report['run_id'])}",
        )
        story = [
            _pdf_paragraph(report["title"], title_style),
            _pdf_paragraph(f"실행 ID: {report['run_id']}", body),
            _pdf_paragraph(f"계약 버전: {report['schema_version']}", body),
            _pdf_paragraph(f"하네스 버전: {report['harness_version']}", body),
            _pdf_paragraph(f"브랜치: {report['git']['branch']}", body),
            _pdf_paragraph(f"기준 커밋: {report['git']['base_commit']}", body),
            _pdf_paragraph(f"결과 커밋: {report['git']['head_commit']}", body),
            _pdf_paragraph("요청", heading),
            _pdf_paragraph(report["request"], body),
            _pdf_paragraph("요약", heading),
            _pdf_paragraph(report["summary"], body),
            _pdf_paragraph("변경 사항", heading),
        ]

        change_rows = [[_pdf_paragraph(label, table_header) for label in ("구분", "파일", "내용")]]
        for change in report.get("changes", []):
            change_rows.append([_pdf_paragraph(value, body) for value in _change_fields(change)])
        if len(change_rows) == 1:
            change_rows.append([_pdf_paragraph("-", body), _pdf_paragraph("-", body), _pdf_paragraph("변경 사항 없음", body)])
        changes_table = Table(change_rows, colWidths=[26 * mm, 56 * mm, 92 * mm], repeatRows=1)
        changes_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF5")),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#9AA6B2")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.extend([changes_table, Spacer(1, 4 * mm), _pdf_paragraph("에이전트 결과", heading)])

        agent_rows = [[_pdf_paragraph(label, table_header) for label in ("역할", "작업", "상태", "결과")]]
        for agent in report["agents"]:
            agent_rows.append(
                [
                    _pdf_paragraph(agent["role"], body),
                    _pdf_paragraph(agent["task_id"], body),
                    _pdf_paragraph(agent["status"], body),
                    _pdf_paragraph(agent["summary"], body),
                ]
            )
        if len(agent_rows) == 1:
            agent_rows.append(
                [
                    _pdf_paragraph("-", body),
                    _pdf_paragraph("-", body),
                    _pdf_paragraph("-", body),
                    _pdf_paragraph("참여 에이전트 없음", body),
                ]
            )
        agents_table = Table(agent_rows, colWidths=[36 * mm, 43 * mm, 25 * mm, 70 * mm], repeatRows=1)
        agents_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF5")),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#9AA6B2")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.extend([agents_table, Spacer(1, 4 * mm), _pdf_paragraph("검사 결과", heading)])

        check_rows = [[_pdf_paragraph(label, table_header) for label in ("검사", "필수", "상태", "근거")]]
        for check in report.get("checks", []):
            check_rows.append(
                [
                    _pdf_paragraph(check.get("name"), body),
                    _pdf_paragraph(check.get("required"), body),
                    _pdf_paragraph(check.get("status"), body),
                    _pdf_paragraph(check.get("evidence"), body),
                ]
            )
        if len(check_rows) == 1:
            check_rows.append([_pdf_paragraph("-", body) for _ in range(4)])
        checks_table = Table(check_rows, colWidths=[42 * mm, 18 * mm, 26 * mm, 88 * mm], repeatRows=1)
        checks_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF5")),
                    ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#9AA6B2")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.extend([checks_table, Spacer(1, 4 * mm), _pdf_paragraph("학사 근거 변경", heading)])
        academic_sources_changed = report["academic_sources_changed"]
        if academic_sources_changed:
            for source_change in academic_sources_changed:
                story.append(_pdf_paragraph(f"• {source_change}", body))
        else:
            story.append(_pdf_paragraph("학사 근거 변경 없음.", body))

        story.append(_pdf_paragraph("이슈 및 남은 작업", heading))
        issues = report.get("issues", [])
        if issues:
            for issue in issues:
                if isinstance(issue, Mapping):
                    detail = issue.get("message", issue.get("summary", issue.get("description", issue)))
                else:
                    detail = issue
                story.append(_pdf_paragraph(f"• {detail}", body))
        else:
            story.append(_pdf_paragraph("알려진 미해결 이슈 없음.", body))

        story.append(
            KeepTogether([
                _pdf_paragraph(
                    "공개 정보 · "
                    f"주요 작업: {_display(report['publication']['major'])} · "
                    f"PDF 필요: {_display(report['publication']['pdf_required'])}",
                    body,
                ),
            ])
        )

        doc.build(
            story,
            onFirstPage=page_footer,
            onLaterPages=page_footer,
            canvasmaker=invariant_canvas,
        )
        os.replace(temp_path, output_path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    )
    temp_path = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def generate_public_report(
    report: Mapping[str, Any],
    reports_dir: Path,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Create sanitized JSON/Markdown and an optional PDF atomically."""

    validate_completion_report(report, schema_path=schema_path)
    sanitized = sanitize_report(report)
    run_id = sanitized["run_id"]
    run_dir = reports_dir / "runs"
    pdf_dir = reports_dir / "pdf"
    json_path = run_dir / f"{run_id}.json"
    markdown_path = run_dir / f"{run_id}.md"
    pdf_path = pdf_dir / f"{run_id}.pdf"

    json_bytes = (json.dumps(sanitized, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    markdown_bytes = render_markdown(sanitized).encode("utf-8")

    # Sensitive data and PDF dependencies are validated before public text output is written.
    staged_pdf: Path | None = None
    staging_directory: tempfile.TemporaryDirectory[str] | None = None
    if publication_requires_pdf(sanitized):
        reports_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_directory = tempfile.TemporaryDirectory(
            prefix=".completion-report-", dir=reports_dir.parent
        )
        staged_pdf = Path(staging_directory.name) / pdf_path.name
        render_pdf(sanitized, staged_pdf)

    _atomic_write(json_path, json_bytes)
    _atomic_write(markdown_path, markdown_bytes)
    if staged_pdf is not None:
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_pdf, pdf_path)
        staging_directory.cleanup()
    elif pdf_path.exists():
        pdf_path.unlink()

    artifacts = [json_path, markdown_path]
    if publication_requires_pdf(sanitized):
        artifacts.append(pdf_path)
    return {
        "run_id": run_id,
        "artifacts": [str(path) for path in artifacts],
        "sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts
        },
        "pdf_generated": publication_requires_pdf(sanitized),
    }
