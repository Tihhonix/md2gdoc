"""Collect Markdown files from CLI inputs (files and/or directories)."""

from __future__ import annotations

import re
from pathlib import Path

MD_EXTENSIONS = {".md", ".markdown", ".mdown", ".mkd"}


def natural_key(path: Path) -> list:
    """Sort key so that '2-intro.md' comes before '10-outro.md'.

    Compares path components one by one, so a directory's files and
    subdirectories are ordered by name, depth-first.
    """
    key = []
    for part in path.parts:
        chunks = re.split(r"(\d+)", part.lower())
        key.append(tuple((0, int(c), "") if c.isdigit() else (1, 0, c) for c in chunks if c))
    return key


def _is_hidden(rel: Path) -> bool:
    return any(p.startswith(".") for p in rel.parts)


def collect_markdown(inputs: list[Path], recursive: bool = True) -> list[Path]:
    """Expand inputs into an ordered, de-duplicated list of Markdown files.

    - Files given explicitly keep the order they were passed in
      (any extension is accepted - the user asked for it).
    - Directories are expanded to Markdown files in natural sort order,
      skipping hidden files/directories (.git, .obsidian, ...).
    """
    result: list[Path] = []
    seen: set[Path] = set()

    for inp in inputs:
        if inp.is_dir():
            pattern = "**/*" if recursive else "*"
            files = [
                p
                for p in inp.glob(pattern)
                if p.is_file()
                and p.suffix.lower() in MD_EXTENSIONS
                and not _is_hidden(p.relative_to(inp))
            ]
            files.sort(key=lambda p: natural_key(p.relative_to(inp)))
        elif inp.is_file():
            files = [inp]
        else:
            raise FileNotFoundError(f"No such file or directory: {inp}")

        for f in files:
            resolved = f.resolve()
            if resolved not in seen:
                seen.add(resolved)
                result.append(f)

    return result
