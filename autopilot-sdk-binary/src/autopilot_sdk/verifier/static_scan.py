"""Static pre-flight verification (doc §9).

Offline, runs before any network/auth. Convenience + audit signal —
runtime network guard (§10) is the actual enforcement.
"""
import ast
import re
from dataclasses import dataclass, field

from .denylist import (
    BANNED_IMPORT_ROOTS,
    BANNED_URL_SUBSTRINGS,
    OBFUSCATION_HEURISTICS,
    WARN_ONLY_IMPORT_ROOTS,
)


@dataclass
class VerificationReport:
    passed: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class _ImportVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.violations: list[str] = []
        self.warnings: list[str] = []

    def _check(self, module: str, lineno: int) -> None:
        root = module.split(".")[0]
        hit = None
        if root in BANNED_IMPORT_ROOTS or module in BANNED_IMPORT_ROOTS:
            hit = root if root in BANNED_IMPORT_ROOTS else module
        if hit is not None:
            msg = f"Line {lineno}: banned import '{module}'"
            if hit in WARN_ONLY_IMPORT_ROOTS:
                self.warnings.append(msg + " (warn-only: host/URL check is authoritative)")
            else:
                self.violations.append(msg)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self._check(alias.name, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.module:
            self._check(node.module, node.lineno)
        self.generic_visit(node)


def scan_source(source: str) -> VerificationReport:
    violations: list[str] = []
    warnings: list[str] = []

    # 1. Regex pass over raw text — catches URL literals even outside imports
    for needle in BANNED_URL_SUBSTRINGS:
        for m in re.finditer(re.escape(needle), source, re.IGNORECASE):
            line_no = source.count("\n", 0, m.start()) + 1
            violations.append(f"Line {line_no}: banned URL literal '{needle}' found")

    for pattern in OBFUSCATION_HEURISTICS:
        if re.search(pattern, source):
            warnings.append(
                f"Obfuscation-style pattern matched: '{pattern}' — flagged for manual review"
            )

    # 2. AST pass — catches aliased imports
    try:
        tree = ast.parse(source)
        visitor = _ImportVisitor()
        visitor.visit(tree)
        violations.extend(visitor.violations)
        warnings.extend(visitor.warnings)
    except SyntaxError as exc:
        violations.append(f"agent.py failed to parse: {exc}")

    return VerificationReport(
        passed=len(violations) == 0, violations=violations, warnings=warnings
    )


def scan_file(path: str) -> VerificationReport:
    with open(path, "r", encoding="utf-8-sig") as f:
        return scan_source(f.read())
