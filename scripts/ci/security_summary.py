#!/usr/bin/env python3
"""Turn pip-audit / npm audit JSON reports into a job summary + exit code.

Policy (documented in docs/ci.md):
  * BLOCKING: a `critical` advisory in a *production* npm dependency tree
    (npm audit --omit=dev) of the dashboard or the root Incy sidecar.
  * NON-BLOCKING (reported as ::warning:: + summary + artifact): everything
    else — pip-audit findings (the PyPI advisory feed carries no severity, so
    they cannot be triaged automatically), npm high/moderate/low, and
    dev-only npm advisories (build tooling such as sharp never ships to prod).

Usage:
  security_summary.py --pip reports/pip-audit.json \
      --npm dashboard=reports/npm-dashboard.json \
      --npm sidecar=reports/npm-sidecar.json [--block-on critical]
Missing/unparseable report files are reported as warnings (the scanner itself
failed) and never silently ignored.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SEVERITIES = ["critical", "high", "moderate", "low", "info"]
LINES: list[str] = []


def out(line: str = "") -> None:
    LINES.append(line)
    print(line)


def annotate(level: str, msg: str) -> None:
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::{level}::{msg}")


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return "unparseable"


def pip_section(path: Path) -> int:
    out("#### Python (pip-audit, requirements.txt)")
    data = load(path)
    if data is None or data == "unparseable":
        out(f"- :warning: report `{path}` missing or unparseable — pip-audit did not complete")
        annotate("warning", f"pip-audit report {path} missing/unparseable")
        return 0
    deps = data.get("dependencies", data if isinstance(data, list) else [])
    vulns = [(d["name"], d.get("version"), v) for d in deps for v in d.get("vulns", [])]
    if not vulns:
        out(f"- :white_check_mark: no known vulnerabilities in {len(deps)} resolved packages")
        return 0
    out(f"- :warning: {len(vulns)} advisory(ies):")
    out("")
    out("| package | version | advisory | fixed in |")
    out("|---|---|---|---|")
    for name, version, v in vulns:
        fixes = ", ".join(v.get("fix_versions") or []) or "—"
        out(f"| {name} | {version} | {v.get('id')} | {fixes} |")
        annotate("warning", f"pip-audit: {name} {version} {v.get('id')} (fix: {fixes})")
    return 0


def npm_section(label: str, path: Path, block_on: set[str]) -> int:
    out(f"#### npm — {label}")
    data = load(path)
    if data is None or data == "unparseable":
        out(f"- :warning: report `{path}` missing or unparseable — npm audit did not complete")
        annotate("warning", f"npm audit report for {label} missing/unparseable")
        return 0
    counts = data.get("metadata", {}).get("vulnerabilities", {})
    total = counts.get("total", 0)
    if not total:
        out("- :white_check_mark: no known vulnerabilities (production dependencies)")
        return 0
    out("- " + ", ".join(f"{s}: {counts.get(s, 0)}" for s in SEVERITIES if counts.get(s)))
    out("")
    out("| package | severity | advisory |")
    out("|---|---|---|")
    blocking = 0
    for name, v in sorted(data.get("vulnerabilities", {}).items()):
        titles = [x.get("title", "") for x in v.get("via", []) if isinstance(x, dict)]
        via_pkgs = [x for x in v.get("via", []) if isinstance(x, str)]
        desc = "; ".join(titles[:2]) or f"via {', '.join(via_pkgs)}"
        sev = v.get("severity", "?")
        out(f"| {name} | {sev} | {desc} |")
        level = "error" if sev in block_on else "warning"
        annotate(level, f"npm audit ({label}): {name} [{sev}] {desc}")
        if sev in block_on:
            blocking += 1
    return blocking


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pip", type=Path)
    ap.add_argument("--npm", action="append", default=[], help="label=path/to/npm-audit.json (production deps, may block)")
    ap.add_argument("--npm-info", action="append", default=[], help="label=path (report only, e.g. dev deps)")
    ap.add_argument("--block-on", default="critical", help="comma list of npm severities that fail the job")
    args = ap.parse_args()
    block_on = {s.strip() for s in args.block_on.split(",") if s.strip()}

    out("### Security scan")
    out(f"Blocking policy: npm production advisories with severity in {sorted(block_on)}; everything else is reported only.")
    out("")
    blocking = 0
    if args.pip:
        blocking += pip_section(args.pip)
    for spec in args.npm:
        label, _, path = spec.partition("=")
        blocking += npm_section(label, Path(path), block_on)
    for spec in args.npm_info:
        label, _, path = spec.partition("=")
        npm_section(f"{label} (report only)", Path(path), set())

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(LINES) + "\n")
    if blocking:
        print(f"{blocking} blocking advisory(ies) — failing the job", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
