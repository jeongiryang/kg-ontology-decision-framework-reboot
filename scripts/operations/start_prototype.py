"""Start the private prototype from an ignored local configuration, never a public listener."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
KEYS = {
    "ACADEMIC_EVIDENCE_BACKEND", "NEO4J_URI", "NEO4J_DATABASE", "NEO4J_USER", "NEO4J_PASSWORD",
    "ACADEMIC_NEO4J_TIMEOUT_SECONDS", "ACADEMIC_LLM_PROVIDER", "ACADEMIC_LLM_BASE_URL",
    "ACADEMIC_LLM_MODEL", "ACADEMIC_LLM_API_KEY", "ACADEMIC_LLM_TIMEOUT_SECONDS",
    "ACADEMIC_LLM_MAX_RESPONSE_BYTES", "ACADEMIC_LLM_MIN_INTERVAL_SECONDS",
    "ACADEMIC_LLM_FAILURE_COOLDOWN_SECONDS", "ACADEMIC_SOURCE_MAP",
    "ACADEMIC_LLM_GROUNDED_GENERATION",
    "ACADEMIC_SEMANTIC_INTERVAL_SECONDS",
}


def _unique_settings(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate private configuration key")
        result[key] = value
    return result


def load_private_settings(path: Path) -> dict[str, str]:
    path = path.resolve()
    if not path.is_relative_to((ROOT / ".local").resolve()) or not path.is_file():
        raise ValueError("configuration must be a private project-local file")
    ignored = subprocess.run(["git", "check-ignore", "--quiet", "--", str(path)], cwd=ROOT, capture_output=True)
    if ignored.returncode != 0:
        raise ValueError("configuration must be ignored by Git")
    config = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_settings)
    if (not isinstance(config, dict) or set(config) - KEYS
            or any(not isinstance(v, str) or any(ord(c) < 32 for c in v) for v in config.values())):
        raise ValueError("invalid private configuration")
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / ".local" / "prototype.json")
    args = parser.parse_args()
    try:
        config = load_private_settings(args.config)
        # Do not inherit stale service credentials from a previous terminal.
        for key in KEYS:
            os.environ.pop(key, None)
        os.environ.update(config)
        sys.path.insert(0, str(ROOT / "src"))
        from academic_assistant.llm import LLMSettings
        from academic_assistant.grounded_generation import grounded_generation_enabled
        from academic_assistant.neo4j_evidence import Neo4jSettings
        LLMSettings.from_env()
        grounded_generation_enabled()
        Neo4jSettings.from_env()
    except (OSError, ValueError):
        print("Private prototype configuration unavailable.", file=sys.stderr)
        return 1
    import uvicorn
    uvicorn.run("academic_assistant.api:app", host="127.0.0.1", port=8000, workers=1, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
