#!/usr/bin/env python3
"""
hallucination_surface.py — version-sensitive API usage detection.

LLMs are trained on a snapshot of documentation and code. They can confidently
generate calls to APIs that existed in an older version, were renamed, or were
changed in a breaking way. This script flags imports and attribute accesses
against a known list of version-sensitive patterns.

This is a first-pass heuristic. The known_risky list should grow over time
as new version-sensitivity cases are discovered.

Usage: python hallucination_surface.py file.py [file2.py ...]
"""

from __future__ import annotations

import ast
import json
import pathlib as _hos_pl

# self-bootstrap: ensure this file's dir (with schema.py) is importable
# regardless of caller cwd/PYTHONPATH (run_validators, run_panel, direct).
import sys
import sys as _hos_sys
from pathlib import Path

_hos_sys.path.insert(0, str(_hos_pl.Path(__file__).resolve().parent))
from schema import WEIGHTS, make_finding, make_result, normalize  # noqa: E402

# (module_pattern, attribute_or_None, reason)
_KNOWN_RISKY: list[tuple[str, str | None, str]] = [
    # Django version-sensitive APIs
    (
        "django.utils.encoding",
        "force_text",
        "renamed to force_str in Django 4.0; force_text removed in 4.0",
    ),
    ("django.utils.translation", "ugettext", "renamed to gettext in Django 4.0; ugettext removed"),
    ("django.utils.translation", "ugettext_lazy", "renamed to gettext_lazy in Django 4.0"),
    ("django.conf.urls", "url", "removed in Django 4.0; use re_path or path"),
    (
        "django.contrib.auth",
        "get_user_model",
        "safe but commonly misused in migrations — should use settings.AUTH_USER_MODEL",
    ),
    ("django.utils.decorators", "available_attrs", "removed in Django 3.0"),
    # django-encrypted-model-fields — version-sensitive
    (
        "encrypted_model_fields",
        None,
        "version-sensitive: verify field names and key handling match installed version",
    ),
    # pyotp — TOTP library
    ("pyotp", "TOTP", "verify window parameter default changed between versions; check tolerance"),
    # DRF version-sensitive
    (
        "rest_framework",
        "serializers",
        "DRF field behaviour changed across versions; verify nullable/required handling",
    ),
    # Celery
    ("celery", "task", "task decorator changed significantly between Celery 4 and 5"),
    # General Python version-sensitive patterns
    ("asyncio", "coroutine", "@asyncio.coroutine removed in Python 3.11; use async def"),
    (
        "collections",
        None,
        "collections.MutableMapping etc. moved to collections.abc in Python 3.10+",
    ),
]

# Import aliases that are commonly used and version-sensitive
_RISKY_IMPORT_NAMES = {
    "force_text",
    "ugettext",
    "ugettext_lazy",
    "url",
    "coroutine",
    "MutableMapping",
    "MutableSequence",
}


class _HallucinationVisitor(ast.NodeVisitor):
    def __init__(self, filename: str):
        self.filename = filename
        self.findings: list[dict] = []
        self._imports: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._imports.add(alias.name)
            self._check_module(alias.name, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        self._imports.add(module)
        self._check_module(module, node.lineno)
        for alias in node.names:
            if alias.name in _RISKY_IMPORT_NAMES:
                self.findings.append(
                    {
                        "file": self.filename,
                        "line": node.lineno,
                        "pattern": f"{module}.{alias.name}",
                        "reason": (
                            f"'{alias.name}' is version-sensitive — "
                            "verify it exists in installed version"
                        ),
                        "severity": "medium",
                    }
                )

    def _check_module(self, module: str, lineno: int) -> None:
        for mod_pattern, attr, reason in _KNOWN_RISKY:
            if module == mod_pattern or module.startswith(mod_pattern + "."):
                if attr is None:
                    self.findings.append(
                        {
                            "file": self.filename,
                            "line": lineno,
                            "pattern": module,
                            "reason": reason,
                            "severity": "medium",
                        }
                    )

    def visit_Attribute(self, node: ast.Attribute) -> None:
        for _, attr, reason in _KNOWN_RISKY:
            if attr and node.attr == attr:
                self.findings.append(
                    {
                        "file": self.filename,
                        "line": getattr(node, "lineno", 0),
                        "pattern": f"?.{node.attr}",
                        "reason": reason,
                        "severity": "medium",
                    }
                )
                break
        self.generic_visit(node)


def analyse_files(file_paths: list[str]) -> dict:
    all_findings: list[dict] = []

    for path in file_paths:
        try:
            source = Path(path).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=path)
            v = _HallucinationVisitor(path)
            v.visit(tree)
            all_findings.extend(v.findings)
        except Exception:
            pass

    # Deduplicate by (file, line, pattern)
    seen: set[tuple] = set()
    unique: list[dict] = []
    for f in all_findings:
        key = (f["file"], f["line"], f["pattern"])
        if key not in seen:
            seen.add(key)
            unique.append(f)

    count = len(unique)
    score = normalize(count, 0, 6)

    evidence = [
        make_finding(f["file"], f["line"], f"⚠ VERIFY: {f['pattern']} — {f['reason']}", "medium")
        for f in unique[:10]
    ]

    checklist = [f"⚠ VERIFY: {f['pattern']} — {f['reason']}" for f in unique[:5]]

    return make_result(
        dimension="hallucination_surface",
        score=score,
        raw_value={"version_sensitive_count": count, "findings": unique},
        weight=WEIGHTS["hallucination_surface"],
        evidence=evidence,
        checklist_items=checklist,
    )


def main() -> None:
    files = [f for f in sys.argv[1:] if f.endswith(".py") and Path(f).exists()]
    if not files:
        print(
            json.dumps(
                make_result(
                    "hallucination_surface",
                    0.0,
                    {"error": "no input"},
                    weight=WEIGHTS["hallucination_surface"],
                    error="no input files",
                ),
                indent=2,
            )
        )
        return
    print(json.dumps(analyse_files(files), indent=2))


if __name__ == "__main__":
    main()
