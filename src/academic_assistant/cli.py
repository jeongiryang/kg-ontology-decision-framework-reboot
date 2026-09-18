from __future__ import annotations

import argparse
import sys

from pydantic import ValidationError

from .core import AnswerEngine, canonical_response_json, validate_request_safety
from .models import AcademicAnswerRequest
from .registry import RegistryUnavailable


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        self.exit(64, "invalid request\n")


def _parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(prog="academic-assistant")
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask", help="승인된 2026 학사 규칙에 질문합니다.")
    ask.add_argument("--question", required=True, help="1~500자의 학사 질문")
    ask.add_argument("--year", type=int, help="입학연도와 교육과정 연도를 함께 지정")
    ask.add_argument("--admission-year", type=int, help="입학연도(호환 옵션)")
    ask.add_argument("--curriculum-year", type=int, help="교육과정 연도(호환 옵션)")
    ask.add_argument("--department", required=True, help="학과")
    ask.add_argument("--credits", action="append", default=[], metavar="METRIC=VALUE", help="현재 이수학점(반복 가능)")
    ask.add_argument("--earned-credit", action="append", default=[], metavar="METRIC=VALUE", help="--credits의 호환 옵션")
    ask.add_argument("--json", action="store_true", help="canonical JSON 출력")
    return parser


def _credits(values: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        metric, separator, raw = value.partition("=")
        if not separator or not metric or metric in result:
            raise ValueError("invalid --earned-credit")
        result[metric] = int(raw)
    return result


def _years(args: argparse.Namespace) -> tuple[int, int]:
    if args.year is not None:
        if args.admission_year not in {None, args.year} or args.curriculum_year not in {None, args.year}:
            raise ValueError("conflicting year values")
        return args.year, args.year
    if args.admission_year is None or args.curriculum_year is None:
        raise ValueError("year scope is required")
    return args.admission_year, args.curriculum_year


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        args = _parser().parse_args(argv)
        admission_year, curriculum_year = _years(args)
        request = AcademicAnswerRequest(
            question=args.question,
            admission_year=admission_year,
            matched_curriculum_year=curriculum_year,
            department=args.department,
            earned_credits=_credits([*args.credits, *args.earned_credit]),
        )
        validate_request_safety(request)
        result = AnswerEngine().answer(request)
    except RegistryUnavailable:
        print("academic registry unavailable", file=sys.stderr)
        return 3
    except (ValueError, ValidationError):
        print("invalid request", file=sys.stderr)
        return 64
    print(canonical_response_json(result) if args.json else result.answer)
    return 0 if result.status == "supported" else 2


if __name__ == "__main__":
    raise SystemExit(main())
