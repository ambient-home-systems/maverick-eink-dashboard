#!/usr/bin/env python3
"""Check every relative Markdown link in the repository resolves to a file.

Only relative links are followed. Anything with a scheme (``https:``, ``mailto:``)
is another host's problem, and checking it would need the network and a token,
which is exactly what CI should not need. Fragments are stripped: this answers
"does the file exist", not "does the heading exist".

Usage::

    python scripts/check_links.py          # exits 1 and lists every broken link
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)>\s]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
FENCE = re.compile(r"^\s*(```|~~~)")


def broken() -> list[str]:
    failures = []
    for page in sorted(ROOT.rglob("*.md")):
        if ".git" in page.parts:
            continue
        fenced = False
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
            if FENCE.match(line):
                fenced = not fenced
            if fenced:
                continue
            for target in LINK.findall(line):
                if ":" in target.split("/")[0] or target.startswith("#"):
                    continue
                if not (page.parent / target.split("#")[0]).exists():
                    failures.append(f"{page.relative_to(ROOT)}:{number}: {target}")
    return failures


if __name__ == "__main__":
    found = broken()
    print("\n".join(found) or "check_links: every relative link resolves")
    sys.exit(1 if found else 0)
