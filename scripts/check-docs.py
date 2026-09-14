#!/usr/bin/env python3
"""Fail when a local Markdown link points to a missing file."""

import re
from pathlib import Path

files = [Path("README.md"), Path("console/README.md"), *Path("docs").glob("*.md")]
missing = []
for source in files:
    for target in re.findall(r"\[[^]]*\]\(([^)]+)\)", source.read_text()):
        target = target.split("#", 1)[0]
        if not target or "://" in target or target.startswith("mailto:"):
            continue
        if not (source.parent / target).exists():
            missing.append(f"{source}: {target}")

if missing:
    raise SystemExit("Missing documentation links:\n" + "\n".join(missing))
print(f"documentation links passed ({len(files)} files)")
