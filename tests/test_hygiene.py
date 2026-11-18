"""Guards against internal references, secrets and data files entering the repository.

Scans every file git would track (committed or not, minus .gitignore matches).
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
MAX_BYTES = 1_000_000
FORBIDDEN_SUFFIXES = {".pdf", ".xlsx", ".xls", ".csv", ".log", ".mp4", ".gif", ".zip"}

# Built from fragments so this file does not match its own patterns.
FORBIDDEN = {
    "internal hostname": r"\bfrb\b|frb\.gov|rsma",
    "internal AI service": r"martin" + r"ai|MARTIN" + r"_API",
    "username": "m1" + r"arb00",
    "internal path": r"/hre" + r"f/|/if/appl",
    "employer name": r"Federal" + r" Reserve|Internal" + r" FR\b|policy" + r"Plot",
    "internal email": r"research@|university\.edu",
    "user-agent spoofing": r"\bLynx\b|Firefox/86",
    "company coefficients": r"\b0\.96\b|\b0\.962\b|\b0\.57\b|\b0\.35\b",
    "API key literal": r"sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}",
}


def tracked_files() -> list[Path]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout  # fmt: skip
        return [ROOT / line for line in out.splitlines() if (ROOT / line).is_file()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        skip = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
        return [
            p for p in ROOT.rglob("*")
            if p.is_file() and not skip & set(p.relative_to(ROOT).parts)
            and not any(part.endswith(".egg-info") for part in p.parts)
        ]  # fmt: skip


FILES = [p for p in tracked_files() if p != SELF]


@pytest.mark.parametrize("label", sorted(FORBIDDEN))
def test_no_forbidden_content(label):
    pattern = re.compile(FORBIDDEN[label], re.IGNORECASE if label != "API key literal" else 0)
    hits = []
    for path in FILES:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{number}")
    assert not hits, f"{label}: {hits[:10]}"


def test_no_data_files_or_large_files():
    problems = [
        str(p.relative_to(ROOT))
        for p in FILES
        if p.suffix.lower() in FORBIDDEN_SUFFIXES or p.stat().st_size > MAX_BYTES
    ]
    assert not problems, problems


def test_env_files_are_not_tracked():
    env_files = [p.name for p in FILES if p.name.startswith(".env") and p.name != ".env.example"]
    assert not env_files, env_files
