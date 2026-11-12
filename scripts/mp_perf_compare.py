#!/usr/bin/env python3
"""
mp_perf_compare.py - Compare performance runs across runs and operating systems
==============================================================================

Reads the JSON reports that ``mp_perf_bench.py``, ``mp_benchmark_mode.py`` and
``mp_soak_orchestrator.py`` write under ``REPORTS/PERF/`` and produces:

  * a per-OS comparison table (Linux, macOS, Fedora, FreeBSD, Windows)
  * a before/after comparison for two runs of the same host
  * a Markdown and a plain-text report
  * a noise-aware verdict: a metric only counts as changed when it moves past
    ``--threshold`` percent

Usage
-----
    ./scripts/mp_perf_compare.py                        # all runs, grouped by OS
    ./scripts/mp_perf_compare.py --group-by-os
    ./scripts/mp_perf_compare.py --before A.json --after B.json
    ./scripts/mp_perf_compare.py --markdown out.md
    ./scripts/mp_perf_compare.py --list
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
REPORT_ROOT = ROOT / "REPORTS" / "PERF"

# Metrics where a lower number is the better outcome.
LOWER_BETTER = (
    "ms_per_frame", "ms_per_request", "cpu_ms_per_frame", "scene_cpu_ms_per_frame",
    "import_ms", "subprocess_ms", "rss_", "_us", "ms", "elapsed_sec", "ready_sec",
    "total_wall_sec", "all_ready_sec", "slowest_ready_sec", "duration_sec",
    "cpu_sec_peak", "rss_peak_mb", "crashes", "failed",
)
# Metrics where a higher number is better.
HIGHER_BETTER = ("fps", "req_per_sec", "passed", "shortcuts_survived", "scripts_passed")


def _better(metric: str, before: float, after: float) -> Optional[bool]:
    if before == after:
        return None
    leaf = metric.rsplit(".", 1)[-1]
    if any(k in leaf for k in HIGHER_BETTER):
        return after > before
    if any(k in leaf for k in LOWER_BETTER):
        return after < before
    return None


def _flatten(data: Any, prefix: str = "") -> Dict[str, float]:
    flat: Dict[str, float] = {}
    if not isinstance(data, dict):
        return flat
    for key, value in data.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(_flatten(value, name))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            flat[name] = float(value)
    return flat


def load_runs() -> List[Dict[str, Any]]:
    """Every stored run, newest last."""
    runs: List[Dict[str, Any]] = []
    if not REPORT_ROOT.is_dir():
        return runs
    for path in sorted(REPORT_ROOT.rglob("*.json")):
        if path.name in ("latest.json",):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        data["_path"] = str(path)
        runs.append(data)
    return runs


def run_label(run: Dict[str, Any]) -> str:
    """A short, stable name: OS-arch plus the timestamp."""
    env = run.get("env") or {}
    tag = run.get("tag") or env.get("tag")
    if not tag:
        system = (env.get("system") or env.get("platform") or sys.platform)
        machine = env.get("machine") or ""
        tag = f"{system}-{machine}".strip("-").lower().replace(" ", "")
    stamp = run.get("started_at", "")
    mode = run.get("mode", "perf")
    return f"{tag} [{mode}] {stamp}"


def _markdown_table(headers: List[str], rows: List[List[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def compare_two(before: Dict[str, Any], after: Dict[str, Any],
                threshold: float) -> Dict[str, Any]:
    fb, fa = _flatten(before), _flatten(after)
    shared = sorted(set(fb) & set(fa))
    rows: List[Dict[str, Any]] = []
    regressions: List[str] = []
    improvements: List[str] = []

    for metric in shared:
        b, a = fb[metric], fa[metric]
        if b == 0:
            continue
        pct = (a - b) / abs(b) * 100.0
        better = _better(metric, b, a)
        row = {
            "metric": metric,
            "before": b,
            "after": a,
            "pct": round(pct, 2),
            "better": better,
            "significant": abs(pct) >= threshold,
        }
        rows.append(row)
        if abs(pct) >= threshold:
            label = metric.rsplit(".", 1)[-1]
            if better is False:
                regressions.append(f"{label}: {pct:+.1f}%")
            elif better is True:
                improvements.append(f"{label}: {pct:+.1f}%")

    return {
        "before": run_label(before),
        "after": run_label(after),
        "threshold": threshold,
        "rows": rows,
        "improvements": improvements,
        "regressions": regressions,
    }


def group_by_os(runs: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for run in runs:
        env = run.get("env") or {}
        system = (env.get("system") or env.get("platform") or "unknown").lower()
        machine = (env.get("machine") or "").lower()
        if system.startswith("darwin") or "mac" in system:
            key = "macOS"
        elif "freebsd" in system:
            key = "FreeBSD"
        elif system.startswith("linux"):
            key = "Linux"
        elif "windows" in system or sys.platform == "win32":
            key = "Windows"
        else:
            key = system or "unknown"
        if machine:
            key = f"{key} ({machine})"
        groups.setdefault(key, []).append(run)
    return groups


def build_os_table(runs: List[Dict[str, Any]]) -> List[List[str]]:
    """One row per run: the headline numbers for that OS."""
    rows: List[List[str]] = []
    for run in runs:
        env = run.get("env") or {}
        results = run.get("results") or {}
        summary = run.get("summary") or {}
        render = (results.get("visualizer") or {}).get("render") or {}
        micro = results.get("_micro") or {}
        server = (results.get("web-companion") or {}).get("server") or {}
        rows.append([
            run_label(run),
            str(env.get("cpu_count", "?")),
            f"{render.get('fps', 0):.1f}" if render else "-",
            f"{render.get('cpu_ms_per_frame', 0):.2f}" if render else "-",
            f"{micro.get('fft_block_us', 0):.1f}" if micro else "-",
            f"{server.get('req_per_sec', 0):.1f}" if server else "-",
            f"{summary.get('total_wall_sec', 0):.2f}" if summary else "-",
            str(summary.get("verdict", "-")) if summary else "-",
        ])
    return rows


def render_markdown(groups: Dict[str, List[Dict[str, Any]]],
                    comparisons: List[Dict[str, Any]]) -> str:
    lines = [
        "# MP Mix Manager — Performance Comparison",
        "",
        f"Generated {datetime.now().isoformat(timespec='seconds')}",
        "",
    ]
    for os_name, runs in sorted(groups.items()):
        lines.append(f"## {os_name}")
        lines.append("")
        table = build_os_table(runs)
        lines.append(_markdown_table(
            ["run", "cores", "fps", "cpu ms/frame", "fft us", "web req/s",
             "load total s", "soak"],
            [[str(c) for c in row] for row in table],
        ))
        lines.append("")
        # First vs last run on this OS is the before/after pair.
        if len(runs) >= 2:
            cmp = compare_two(runs[0], runs[-1], 10.0)
            lines.append(f"### {os_name}: first vs latest run")
            lines.append("")
            if cmp["improvements"]:
                lines.append("**Faster:** " + ", ".join(cmp["improvements"]))
                lines.append("")
            if cmp["regressions"]:
                lines.append("**Slower:** " + ", ".join(cmp["regressions"]))
                lines.append("")
            if not cmp["improvements"] and not cmp["regressions"]:
                lines.append("No metric moved past the 10% noise threshold.")
                lines.append("")
    return "\n".join(lines)


def render_text(groups: Dict[str, List[Dict[str, Any]]]) -> str:
    out: List[str] = []
    out.append("=" * 92)
    out.append("  MP MIX MANAGER — PERFORMANCE COMPARISON")
    out.append("=" * 92)
    for os_name, runs in sorted(groups.items()):
        out.append("")
        out.append(f"  {os_name}  ({len(runs)} run{'s' if len(runs) != 1 else ''})")
        out.append("-" * 92)
        out.append(f"  {'run':<44} {'fps':>6} {'cpu/f':>7} {'fft us':>7} {'req/s':>7}")
        for run in runs:
            results = run.get("results") or {}
            render = (results.get("visualizer") or {}).get("render") or {}
            micro = results.get("_micro") or {}
            server = (results.get("web-companion") or {}).get("server") or {}
            label = run_label(run)[:44]
            fps = f"{render['fps']:.1f}" if "fps" in render else "-"
            cpuf = f"{render['cpu_ms_per_frame']:.2f}" if "cpu_ms_per_frame" in render else "-"
            fft = f"{micro['fft_block_us']:.1f}" if "fft_block_us" in micro else "-"
            req = f"{server['req_per_sec']:.1f}" if "req_per_sec" in server else "-"
            out.append(f"  {label:<44} {fps:>6} {cpuf:>7} {fft:>7} {req:>7}")
        if len(runs) >= 2:
            cmp = compare_two(runs[0], runs[-1], 10.0)
            out.append("")
            out.append("    first vs latest run (noise threshold 10%):")
            if cmp["improvements"]:
                for item in cmp["improvements"]:
                    out.append(f"      ✓ {item}")
            if cmp["regressions"]:
                for item in cmp["regressions"]:
                    out.append(f"      ✗ {item}")
            if not cmp["improvements"] and not cmp["regressions"]:
                out.append("      (no significant change)")
    out.append("")
    out.append("=" * 92)
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mp_perf_compare.py",
        description="Compare MP performance runs across runs and operating systems.",
    )
    ap.add_argument("--list", action="store_true", help="list stored runs")
    ap.add_argument("--before", default="", help="baseline JSON path")
    ap.add_argument("--after", default="", help="new JSON path")
    ap.add_argument("--group-by-os", action="store_true",
                    help="group the summary by operating system (default on)")
    ap.add_argument("--threshold", type=float, default=10.0,
                    help="percent change that counts as a real difference")
    ap.add_argument("--markdown", default="", help="write a Markdown report here")
    ap.add_argument("--text", default="", help="write a text report here")
    args = ap.parse_args(argv)

    runs = load_runs()
    if args.list:
        if not runs:
            print(f"no runs under {REPORT_ROOT}")
            return 0
        for run in runs:
            print(run_label(run))
        return 0

    if not runs:
        print(f"no runs under {REPORT_ROOT}", file=sys.stderr)
        return 1

    if args.before and args.after:
        try:
            before = json.loads(Path(args.before).read_text(encoding="utf-8"))
            after = json.loads(Path(args.after).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"cannot read comparison inputs: {exc}", file=sys.stderr)
            return 1
        cmp = compare_two(before, after, args.threshold)
        print(f"before: {cmp['before']}")
        print(f"after : {cmp['after']}")
        print("-" * 78)
        for row in cmp["rows"]:
            if not row["significant"]:
                continue
            mark = "✓" if row["better"] else ("✗" if row["better"] is False else " ")
            print(f"  {row['metric'][:52]:<52} {row['before']:>10.2f} "
                  f"{row['after']:>10.2f} {mark} {row['pct']:+6.1f}%")
        print("-" * 78)
        print(f"improved: {len(cmp['improvements'])}  "
              f"regressed: {len(cmp['regressions'])}")
        return 0

    groups = group_by_os(runs)
    text = render_text(groups)
    print(text)

    if args.text:
        Path(args.text).parent.mkdir(parents=True, exist_ok=True)
        Path(args.text).write_text(text, encoding="utf-8")
        print(f"text report: {args.text}")

    if args.markdown:
        md = render_markdown(groups, [])
        Path(args.markdown).parent.mkdir(parents=True, exist_ok=True)
        Path(args.markdown).write_text(md, encoding="utf-8")
        print(f"markdown report: {args.markdown}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
