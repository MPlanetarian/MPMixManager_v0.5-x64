#!/usr/bin/env python3
"""Every source file must run on the oldest Python the tree claims to support.

`scripts/mp_dependencies.py` accepts a 3.8 interpreter (`_works(..., minimum=
(3, 8))`), and Ubuntu 20.04 LTS — a supported platform — ships 3.8.10 as the
system Python. Two classes of syntax are newer than that and fail *at import*,
so a missing `from __future__ import annotations` or a backslash inside an
f-string expression turns a working install into `TypeError` on startup.

This module parses every shipped file as 3.8. It is a static check, so it runs
on any interpreter and needs no 3.8 installation.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files the application actually ships and runs.
SEARCH_ROOTS = (ROOT, ROOT / "scripts", ROOT / "tests", ROOT / "themes")

MINIMUM = (3, 8)


def shipped_files():
    seen = set()
    for root in SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.py")):
            if path.name.startswith(".") or "__pycache__" in path.parts:
                continue
            if path in seen:
                continue
            seen.add(path)
            yield path


class TestSourcesRunOnTheOldestSupportedPython(unittest.TestCase):
    def test_every_file_parses_as_python_38(self):
        broken = []
        for path in shipped_files():
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                broken.append(f"{path.relative_to(ROOT)}: unreadable ({exc})")
                continue
            try:
                ast.parse(source, filename=str(path), feature_version=MINIMUM)
            except SyntaxError as exc:
                broken.append(
                    f"{path.relative_to(ROOT)}:{exc.lineno}: {exc.msg}"
                )
        self.assertEqual(
            broken, [],
            "these files cannot be imported on Python 3.8, which Ubuntu 20.04 "
            "ships as its system Python:\n  " + "\n  ".join(broken),
        )

    def test_modules_that_use_new_typing_syntax_import_annotations(self):
        """`dict[...]` / `X | None` in an annotation need the future import.

        Without it the annotation is evaluated when the module is imported and
        raises TypeError on 3.8, so the feature silently stops existing.
        """
        offenders = []
        for path in shipped_files():
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "from __future__ import annotations" in source:
                continue
            tree = None
            try:
                tree = ast.parse(source, feature_version=MINIMUM)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                annotation = None
                if isinstance(node, ast.AnnAssign):
                    annotation = node.annotation
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    annotation = node.returns
                if annotation is None:
                    continue
                for child in ast.walk(annotation):
                    # Subscript of a builtin generic: dict[...], list[...] ...
                    if isinstance(child, ast.Subscript) and isinstance(child.value, ast.Name) \
                            and child.value.id in {"dict", "list", "tuple", "set", "type"}:
                        offenders.append(f"{path.relative_to(ROOT)}:{child.lineno}")
                        break
        self.assertEqual(
            offenders, [],
            "these files annotate with builtin generics but lack "
            "`from __future__ import annotations`, so they raise TypeError on "
            "Python 3.8:\n  " + "\n  ".join(sorted(set(offenders))),
        )


if __name__ == "__main__":
    unittest.main()
