"""No real client name anywhere in the repo (roadmap 0.2).

This is a public repo, and naming a real client in it breaks our own anonymisation rule. Item 0.2
took the name out of the golden set and the fixtures; this stops it coming back.

**Why the forbidden strings are hashed.** A denylist written in plain text would put the client
name back into the repo — the exact thing it exists to prevent. It happened once already, in the
handoff document, in a sentence saying the name had been removed: writing "X is gone" puts X back.
So the terms live here only as SHA-256 digests. The scanner hashes what it finds and compares.

To add a term without ever committing it, hash it in a throwaway shell — not in a file::

    python3 -c "import hashlib,sys; \\
        print(hashlib.sha256(sys.argv[1].lower().encode()).hexdigest())" "the term"

Then paste the digest into ``FORBIDDEN`` with a label saying what *kind* of thing it is — never
what it is.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

#: SHA-256 of lowercased terms that must not appear. Never store the plaintext.
#: Labels say what kind of thing the term is, never what it is.
FORBIDDEN: dict[str, str] = {
    "8d8b9eea6077c5a34390a01c831e38930ff3bdfe208c62834138ddb4b1713fe1": "a client name",
    "e066308dfb48afa4c98ee56a9b437b62b66c661149a728a271e87bae8b97ceca": "an address (2 words)",
    "4f3fb37b1fbd45a2253bef575164de1709c8718cda2100330f946b7df66acabb": "an address (1 word)",
}

SCANNED_SUFFIXES = frozenset({".py", ".md", ".yaml", ".yml", ".json", ".jsonl", ".toml", ".txt"})
SKIPPED_DIRS = frozenset({".git", "__pycache__", "build", "node_modules", ".venv", "eval-out"})

_WORD = re.compile(r"[a-z0-9]+")


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _terms(text: str) -> set[str]:
    """Every unigram and bigram in the text, lowercased. Bigrams catch two-word names."""
    words = _WORD.findall(text.lower())
    return set(words) | {f"{a} {b}" for a, b in zip(words, words[1:], strict=False)}


def _scanned_files() -> list[Path]:
    return [
        p
        for p in REPO_ROOT.rglob("*")
        if p.is_file()
        and p.suffix in SCANNED_SUFFIXES
        and not SKIPPED_DIRS & set(p.relative_to(REPO_ROOT).parts)
    ]


def test_no_real_client_name_anywhere_in_the_repo() -> None:
    """Covers the whole tree — code, fixtures, docs and this session's handoff alike.

    Documents are not exempt. The one recurrence so far was in prose, not data.
    """
    files = _scanned_files()
    assert len(files) > 50, f"only scanned {len(files)} files — the walk is probably broken"

    offenders: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for term in _terms(text):
            if (what := FORBIDDEN.get(_digest(term))) is not None:
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()} contains {what}")

    assert not offenders, (
        "real-world identifiers found in a public repo (roadmap 0.2):\n  "
        + "\n  ".join(sorted(set(offenders)))
        + "\nReplace with an invented placeholder. Do not name the term in the fix, including "
        "in a commit message or a comment explaining the removal."
    )


def test_the_scanner_would_actually_catch_something() -> None:
    """Guards the guard. A broken tokeniser makes this file silently useless.

    Uses an invented phrase, not a real term — naming one here would be the very leak this file
    exists to stop, which is why the check below is structural rather than a known-value test.
    """
    # Bigrams must be produced, or a two-word name walks straight past the scanner.
    assert _terms("Hello Wetherby Vale there") >= {"wetherby vale", "hello", "there"}
    # Case and punctuation must not defeat it.
    assert "wetherby vale" in _terms("...WETHERBY VALE!")
    assert _digest("wetherby vale") not in FORBIDDEN

    assert FORBIDDEN, "denylist is empty"
    for digest in FORBIDDEN:
        assert re.fullmatch(r"[0-9a-f]{64}", digest), f"{digest} is not a sha256 hex digest"
