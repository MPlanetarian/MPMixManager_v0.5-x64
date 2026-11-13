#!/usr/bin/env python3
"""
mp_auto_tester_report.py - MP Auto Tester report builder
========================================================

Builds the cross-system report produced by ``MP_Auto_Tester_Flash.sh``:

  * a styled PDF (system-by-system test results + embedded screenshots)
  * a plain-text summary
  * a short spoken script for the MP Reporter Stephen Hawking voice

The MP Reporter runs this through ``scripts/mp_auto_tester_report.sh``, which
prints ``Report generated: <pdf>`` on stdout because that is the line
``MP_Reporter.sh`` parses to find the artifact it should narrate.

Usage:
    python3 scripts/mp_auto_tester_report.py \
        --results REPORTS/MP_AUTO_TESTER/latest/results.json \
        --out-dir REPORTS/MP_AUTO_TESTER/latest \
        --screenshots REPORTS/MP_TESTER_MIX_MANAGER_SCREENSHOTS/latest

    python3 scripts/mp_auto_tester_report.py --print-summary    # text only
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"

# The four desktop shortcuts, in the order the screenshots are required.
SHORTCUTS = (
    ("visualiser", "MP Liquid Visualiser"),
    ("gui", "MP Gui"),
    ("liquid_player", "MP Liquid Player"),
    ("mix_manager", "MP Mix Manager"),
)

SYSTEM_ORDER = ("local", "fedora", "mac", "windows", "freebsd")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def read_version() -> str:
    try:
        return VERSION_FILE.read_text(encoding="utf-8").strip() or "0.5"
    except OSError:
        return "0.5"


def load_results(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def system_rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every system in a stable order, reachable or not."""
    by_key = {s.get("key"): s for s in data.get("systems", [])}
    rows: List[Dict[str, Any]] = []
    for key in SYSTEM_ORDER:
        if key in by_key:
            rows.append(by_key[key])
    for key, system in by_key.items():
        if key not in SYSTEM_ORDER:
            rows.append(system)
    return rows


def totals(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {"ran": 0, "failed": 0, "errored": 0, "skipped": 0,
           "systems_ok": 0, "systems_down": 0, "shots": 0}
    for row in rows:
        tests = row.get("tests") or {}
        if row.get("reachable"):
            out["ran"] += int(tests.get("ran") or 0)
            out["failed"] += int(tests.get("failed") or 0)
            out["errored"] += int(tests.get("errored") or 0)
            out["skipped"] += int(tests.get("skipped") or 0)
            out["systems_ok"] += 1
        else:
            out["systems_down"] += 1
        out["shots"] += len(row.get("screenshots") or [])
    return out


def verdict(row: Dict[str, Any]) -> str:
    if not row.get("reachable"):
        return "UNREACHABLE"
    tests = row.get("tests") or {}
    if not tests.get("available", True):
        return "NO TESTS"
    if int(tests.get("failed") or 0) or int(tests.get("errored") or 0):
        return "FAILURES"
    if not tests.get("ran"):
        return "NO TESTS"
    return "PASS"


def verdict_verbose(row: Dict[str, Any]) -> str:
    """Long form used in the PDF, where there is room to be precise."""
    code = verdict(row)
    if code == "FAILURES":
        tests = row.get("tests") or {}
        return (f"{tests.get('failed', 0)} failed / "
                f"{tests.get('errored', 0)} errored")
    if code == "UNREACHABLE":
        return "system not reachable from this host"
    if code == "NO TESTS":
        return "no runnable test suite on that system"
    return "all tests passed"


def missing_screenshots(row: Dict[str, Any]) -> List[str]:
    have = {shot.get("app") for shot in (row.get("screenshots") or [])}
    return [app for key, app in SHORTCUTS if key not in have and app not in have]


# ---------------------------------------------------------------------------
# plain text + spoken script
# ---------------------------------------------------------------------------
def build_text(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    agg = totals(rows)
    started = data.get("started", "unknown")
    finished = data.get("finished", "unknown")
    lines: List[str] = []
    lines.append("=" * 70)
    lines.append("MP MIX MANAGER 0.5 - AUTOMATED CROSS-SYSTEM TEST REPORT")
    lines.append("=" * 70)
    lines.append(f"Host of record : {data.get('host', 'unknown')}")
    lines.append(f"App version    : {data.get('version', read_version())}")
    lines.append(f"Started        : {started}")
    lines.append(f"Finished       : {finished}")
    lines.append(f"Settle time    : {data.get('wait_seconds', 20)}s before each screenshot")
    lines.append("")
    lines.append("-" * 70)
    lines.append("SYSTEM SUMMARY")
    lines.append("-" * 70)
    header = f"  {'SYSTEM':<26} {'STATE':<12} {'RAN':>6} {'FAIL':>5} {'ERR':>5} {'SHOTS':>6}"
    lines.append(header)
    for row in rows:
        tests = row.get("tests") or {}
        lines.append(
            f"  {str(row.get('label') or row.get('key'))[:25]:<26} "
            f"{verdict(row):<12} "
            f"{fmt_int(tests.get('ran')):>6} "
            f"{fmt_int(tests.get('failed')):>5} "
            f"{fmt_int(tests.get('errored')):>5} "
            f"{len(row.get('screenshots') or []):>6}"
        )
    lines.append("")
    lines.append(
        f"  Totals: {agg['systems_ok']} system(s) tested, "
        f"{agg['systems_down']} unreachable, "
        f"{fmt_int(agg['ran'])} tests run, "
        f"{fmt_int(agg['failed'])} failed, "
        f"{fmt_int(agg['errored'])} errored, "
        f"{agg['shots']} screenshot(s) captured."
    )
    lines.append("")
    for row in rows:
        lines.append("-" * 70)
        lines.append(f"{str(row.get('label')).upper()}  [{row.get('hostname', '?')}]")
        lines.append("-" * 70)
        lines.append(f"  State        : {verdict(row)} ({verdict_verbose(row)})")
        if row.get("address"):
            lines.append(f"  Address      : {row['address']}")
        tests = row.get("tests") or {}
        if tests.get("ran") or tests.get("failed") or tests.get("errored"):
            lines.append(
                f"  Unit tests   : {fmt_int(tests.get('ran'))} run, "
                f"{fmt_int(tests.get('failed'))} failed, "
                f"{fmt_int(tests.get('errored'))} errored, "
                f"{fmt_int(tests.get('skipped'))} skipped "
                f"in {tests.get('seconds', 0)}s"
            )
            if tests.get("command"):
                lines.append(f"  Command      : {tests['command']}")
        else:
            lines.append("  Unit tests   : not run on this system")
        missing = missing_screenshots(row)
        shots = row.get("screenshots") or []
        if shots:
            lines.append(f"  Screenshots  : {len(shots)} captured")
            for shot in shots:
                lines.append(f"                 {Path(shot.get('file', '')).name}")
        else:
            lines.append("  Screenshots  : none captured")
        if missing:
            lines.append(f"  Missing shots: {', '.join(missing)}")
        for note in row.get("notes") or []:
            lines.append(f"  Note         : {note}")
        lines.append("")
    lines.append("=" * 70)
    lines.append("Generated by MP Auto Tester (MP_Auto_Tester_Flash.sh)")
    lines.append("=" * 70)
    return "\n".join(lines) + "\n"


def build_spoken_script(data: Dict[str, Any], rows: List[Dict[str, Any]]) -> str:
    """Short, natural prose for the Stephen Hawking voice briefing."""
    agg = totals(rows)
    parts: List[str] = []
    parts.append(
        "MP Mix Manager automated cross system test report, "
        f"version {data.get('version', read_version())}."
    )
    parts.append(
        f"Five test systems were canvassed. {agg['systems_ok']} responded and "
        f"{agg['systems_down']} could not be reached."
    )
    parts.append(
        f"A total of {agg['ran']} unit tests were executed, with "
        f"{agg['failed']} failures and {agg['errored']} errors."
    )
    parts.append(
        f"{agg['shots']} desktop screenshots were captured after each "
        "application had fully loaded."
    )
    for row in rows:
        label = str(row.get("label") or row.get("key"))
        state = verdict(row)
        if state == "PASS":
            parts.append(f"{label}: all tests passed, screenshots captured.")
        elif state == "FAILURES":
            tests = row.get("tests") or {}
            parts.append(
                f"{label}: {tests.get('failed', 0)} failures and "
                f"{tests.get('errored', 0)} errors require attention."
            )
        elif state == "UNREACHABLE":
            parts.append(f"{label}: not reachable, so it was skipped.")
        else:
            parts.append(f"{label}: no runnable tests were found.")
    parts.append(
        "The full report, including every screenshot, is attached to this "
        "daily MP Reporter briefing."
    )
    return " ".join(parts)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def _thumbnails(shots: List[Dict[str, Any]], temp_dir: Path) -> List[tuple]:
    """(label, path, width, height) thumbnails, downscaled to keep the PDF small."""
    made: List[tuple] = []
    try:
        from PIL import Image  # type: ignore
    except Exception:
        Image = None  # type: ignore
    for index, shot in enumerate(shots, 1):
        src = Path(shot.get("path") or "")
        if not src.is_file():
            continue
        label = shot.get("app") or src.stem
        host = shot.get("hostname") or ""
        caption = f"{label} - {host}" if host else label
        if Image is None:
            made.append((caption, str(src), None, None))
            continue
        dest = temp_dir / f"shot_{index:03d}.jpg"
        try:
            with Image.open(src) as im:
                im = im.convert("RGB")
                im.thumbnail((1280, 1280), Image.LANCZOS)
                im.save(dest, "JPEG", quality=82, optimize=True)
            made.append((caption, str(dest), None, None))
        except Exception:
            made.append((caption, str(src), None, None))
    return made


def build_pdf(data: Dict[str, Any], rows: List[Dict[str, Any]], out_pdf: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        HRFlowable, Image, KeepTogether, PageBreak, Paragraph,
        SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "TitleX", parent=styles["Heading1"], fontName="Helvetica-Bold",
        fontSize=19, leading=23, textColor=colors.HexColor("#0f172a"), spaceAfter=4,
    )
    subtitle = ParagraphStyle(
        "SubX", parent=styles["Normal"], fontName="Helvetica",
        fontSize=9, leading=12, textColor=colors.HexColor("#64748b"), spaceAfter=12,
    )
    h2 = ParagraphStyle(
        "H2X", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=11.5, leading=15, textColor=colors.HexColor("#ff007f"),
        spaceBefore=12, spaceAfter=6,
    )
    body = ParagraphStyle(
        "BodyX", parent=styles["Normal"], fontName="Helvetica",
        fontSize=9, leading=12, textColor=colors.HexColor("#334155"),
    )
    cell = ParagraphStyle(
        "CellX", parent=body, fontSize=8, leading=10,
    )
    caption = ParagraphStyle(
        "CapX", parent=body, fontName="Helvetica-Bold", fontSize=8.5,
        leading=11, alignment=TA_CENTER, textColor=colors.HexColor("#0f172a"),
    )

    doc = SimpleDocTemplate(
        str(out_pdf), pagesize=letter,
        leftMargin=34, rightMargin=34, topMargin=40, bottomMargin=40,
        title="MP Mix Manager Auto Tester Report",
    )
    story: List[Any] = []
    agg = totals(rows)

    story.append(Paragraph("MP MIX MANAGER 0.5 - AUTOMATED CROSS-SYSTEM TEST REPORT", title))
    story.append(Paragraph(
        f"Generated {datetime.now().strftime('%A, %d %B %Y at %H:%M:%S')} "
        f"&nbsp;|&nbsp; Host of record: {data.get('host', 'unknown')} "
        f"&nbsp;|&nbsp; Version {data.get('version', read_version())} "
        f"&nbsp;|&nbsp; Settle time {data.get('wait_seconds', 20)}s",
        subtitle,
    ))
    story.append(HRFlowable(width="100%", thickness=1.5,
                            color=colors.HexColor("#ff007f"), spaceAfter=10))

    story.append(Paragraph("Executive summary", h2))
    story.append(Paragraph(
        f"Five test systems were canvassed: the local Bazzite workstation, the "
        f"Fedora 45 workstation, the macOS mini, the Windows 11 virtual machine "
        f"and the FreeBSD virtual machine. <b>{agg['systems_ok']}</b> responded; "
        f"<b>{agg['systems_down']}</b> could not be reached. "
        f"<b>{fmt_int(agg['ran'])}</b> unit tests ran with "
        f"<b>{fmt_int(agg['failed'])}</b> failures and "
        f"<b>{fmt_int(agg['errored'])}</b> errors. "
        f"<b>{agg['shots']}</b> desktop screenshots were taken at least "
        f"{data.get('wait_seconds', 20)} seconds after the four MP shortcuts "
        f"were launched, so every application had time to finish loading.",
        body,
    ))

    table_data = [[
        Paragraph("<b>System</b>", cell),
        Paragraph("<b>Hostname</b>", cell),
        Paragraph("<b>State</b>", cell),
        Paragraph("<b>Ran</b>", cell),
        Paragraph("<b>Fail</b>", cell),
        Paragraph("<b>Err</b>", cell),
        Paragraph("<b>Shots</b>", cell),
    ]]
    for row in rows:
        tests = row.get("tests") or {}
        table_data.append([
            Paragraph(str(row.get("label") or row.get("key")), cell),
            Paragraph(str(row.get("hostname") or "-"), cell),
            Paragraph(verdict(row), cell),
            Paragraph(fmt_int(tests.get("ran")), cell),
            Paragraph(fmt_int(tests.get("failed")), cell),
            Paragraph(fmt_int(tests.get("errored")), cell),
            Paragraph(str(len(row.get("screenshots") or [])), cell),
        ])
    table = Table(table_data, colWidths=[2.0 * inch, 1.35 * inch, 1.2 * inch,
                                         0.6 * inch, 0.55 * inch, 0.5 * inch, 0.55 * inch],
                  hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0e7490")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f1f5f9")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(Spacer(1, 6))
    story.append(table)

    story.append(Paragraph("Per-system detail", h2))
    for row in rows:
        tests = row.get("tests") or {}
        block: List[Any] = [Paragraph(
            f"{str(row.get('label')).upper()} &nbsp;"
            f"<font color='#64748b'>[{row.get('hostname', '?')}]</font>", h2)]
        block.append(Paragraph(f"<b>State:</b> {verdict(row)} - {verdict_verbose(row)}", body))
        if row.get("address"):
            block.append(Paragraph(f"<b>Address:</b> {row['address']}", body))
        if tests.get("ran") or tests.get("failed") or tests.get("errored"):
            block.append(Paragraph(
                f"<b>Unit tests:</b> {fmt_int(tests.get('ran'))} run, "
                f"{fmt_int(tests.get('failed'))} failed, "
                f"{fmt_int(tests.get('errored'))} errored, "
                f"{fmt_int(tests.get('skipped'))} skipped in "
                f"{tests.get('seconds', 0)}s", body))
        else:
            block.append(Paragraph("<b>Unit tests:</b> not run on this system", body))
        shots = row.get("screenshots") or []
        if shots:
            block.append(Paragraph(
                "<b>Screenshots:</b> " +
                ", ".join(Path(s.get("file", "")).name for s in shots), body))
        else:
            block.append(Paragraph("<b>Screenshots:</b> none captured", body))
        missing = missing_screenshots(row)
        if missing:
            block.append(Paragraph(
                f"<b>Missing:</b> {', '.join(missing)}", body))
        for note in row.get("notes") or []:
            block.append(Paragraph(f"<b>Note:</b> {note}", body))
        story.append(KeepTogether(block))

    story.append(PageBreak())
    story.append(Paragraph("Desktop shortcut screenshots", h2))
    story.append(Paragraph(
        "Each image was captured after the shortcut had been open for at least "
        f"{data.get('wait_seconds', 20)} seconds. Captures are grouped by system "
        "in the required order: MP Liquid Visualiser, MP Gui, MP Liquid Player, "
        "MP Mix Manager.", body))

    all_shots: List[Dict[str, Any]] = []
    for row in rows:
        for shot in row.get("screenshots") or []:
            entry = dict(shot)
            entry.setdefault("hostname", row.get("hostname"))
            all_shots.append(entry)

    with tempfile.TemporaryDirectory() as td:
        thumbs = _thumbnails(all_shots, Path(td))
        if not thumbs:
            story.append(Paragraph(
                "<i>No screenshots were captured during this run.</i>", body))
        for caption_text, path, _w, _h in thumbs:
            try:
                img = Image(path)
            except Exception:
                story.append(Paragraph(f"{caption_text} (image unreadable)", body))
                continue
            max_w = 6.4 * inch
            max_h = 3.6 * inch
            scale = min(max_w / float(img.imageWidth), max_h / float(img.imageHeight))
            img.drawWidth = img.imageWidth * scale
            img.drawHeight = img.imageHeight * scale
            story.append(KeepTogether([img, Spacer(1, 2),
                                       Paragraph(caption_text, caption),
                                       Spacer(1, 10)]))

        story.append(HRFlowable(width="100%", thickness=0.8,
                                color=colors.HexColor("#cbd5e1"), spaceBefore=6))
        story.append(Paragraph(
            "Report generated by MP_Auto_Tester_Flash.sh and delivered through the "
            "MP Reporter suite (text, PDF and Stephen Hawking voice briefing).", subtitle))

        # Build while the thumbnail directory still exists; ReportLab reads the
        # image files during doc.build, not when the flowable is created.
        doc.build(story)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="MP Auto Tester report builder")
    parser.add_argument("--results", help="results.json written by MP_Auto_Tester_Flash.sh")
    parser.add_argument("--out-dir", default="", help="directory for the PDF and text report")
    parser.add_argument("--screenshots", default="", help="screenshot directory to embed")
    parser.add_argument("--stamp", default="", help="timestamp tag for file names")
    parser.add_argument("--print-summary", action="store_true",
                        help="print the text report and exit")
    parser.add_argument("--speech", action="store_true",
                        help="print only the spoken script and exit")
    args = parser.parse_args()

    if not args.results:
        parser.error("--results is required")
    results_path = Path(args.results)
    if not results_path.is_file():
        print(f"Error: results file not found: {results_path}")
        return 1

    data = load_results(results_path)
    rows = system_rows(data)

    if args.print_summary:
        print(build_text(data, rows), end="")
        return 0
    if args.speech:
        print(build_spoken_script(data, rows))
        return 0

    stamp = args.stamp or data.get("stamp") or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = Path(args.out_dir) if args.out_dir else results_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    shots_dir = Path(args.screenshots) if args.screenshots else None

    # Attach every screenshot physically present in the run folder.
    if shots_dir and shots_dir.is_dir():
        for row in rows:
            fixed: List[Dict[str, Any]] = []
            for shot in row.get("screenshots") or []:
                entry = dict(shot)
                if not Path(entry.get("path") or "").is_file():
                    candidate = shots_dir / Path(entry.get("file", "")).name
                    if candidate.is_file():
                        entry["path"] = str(candidate)
                if Path(entry.get("path") or "").is_file():
                    fixed.append(entry)
            row["screenshots"] = fixed

    out_pdf = out_dir / f"MP_Auto_Tester_Report_{stamp}.pdf"
    out_txt = out_dir / f"MP_Auto_Tester_Report_{stamp}.txt"

    text = build_text(data, rows)
    out_txt.write_text(text, encoding="utf-8")
    try:
        build_pdf(data, rows, out_pdf)
    except Exception as exc:  # reportlab missing or a broken image
        print(f"Warning: PDF rendering failed ({exc}); the text report is still available.")
        return 1

    print(f"Report generated: {out_pdf}")
    print(f"Text summary generated: {out_txt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
