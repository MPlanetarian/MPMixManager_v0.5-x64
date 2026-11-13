"""Audio conversion core for the MP Mix Manager conversion suite.

The bash wizards in Make_SOF_FLAC_CONVERSION.sh and convert_audio_format.sh are
good at orchestration but poor at tables, capability checks and JSON. This module
owns the parts that benefit from real data structures:

  * the output format registry and its encoder profiles (one place, so the menu
    count and the actual ffmpeg lane can never disagree)
  * the capability matrix that decides which source -> target conversions are
    worth attempting
  * the previous-conversion statistics table
  * discovery of the folders that already hold converted audio
  * metadata presets, custom fields, cover art and disc-set tagging
  * the ffmpeg command builder, including cover embedding limits

Every encoder profile here was verified against the shipped FFmpeg. A format is
listed as an output only when this build can actually write it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from audio_formats import AUDIO_EXTENSIONS, LOSSLESS_EXTENSIONS
except ImportError:  # imported as scripts.audio_conversion_suite
    from scripts.audio_formats import AUDIO_EXTENSIONS, LOSSLESS_EXTENSIONS

CONFIG_DIR = Path.home() / ".config" / "mix-manager"
PRESETS_FILE = CONFIG_DIR / "conversion_metadata_presets.json"
STATS_LOG_NAME = "MP_Mix_Manager_Conversion_Log.jsonl"

# Directory suffix used for every converted output folder.
OUTPUT_DIR_SUFFIX = "_CONVERTED_OUTPUTS"


@dataclass(frozen=True)
class OutputFormat:
    """One target the converter can write at the high preset."""

    key: str                 # menu key / log name, e.g. "flac"
    label: str               # human label, e.g. "FLAC 24-bit"
    extension: str           # written file extension, without the dot
    codec_args: Tuple[str, ...]
    lossless: bool = False
    supports_cover: bool = False   # embedded front cover via attached_pic
    supports_tags: bool = True
    muxer: Optional[str] = None    # explicit -f when the extension is ambiguous
    vbr_quality: Optional[str] = None  # short note for the UI, e.g. "320 kbps"
    note: str = ""                  # limitation shown in the picker

    def ffmpeg_output_args(self) -> List[str]:
        args = list(self.codec_args)
        if self.muxer:
            args += ["-f", self.muxer]
        return args


# Verified against FFmpeg 8.1 on this machine. Anything not writable by this
# build (ape, tak, mpc, mp1, dsf, dff) is deliberately absent: the converter
# must never offer an output it cannot produce.
OUTPUT_FORMATS: Tuple[OutputFormat, ...] = (
    OutputFormat("flac", "FLAC 24-bit Lossless", "flac",
                 ("-c:a", "flac", "-sample_fmt", "s32", "-compression_level", "12"),
                 lossless=True, supports_cover=True, vbr_quality="max compression"),
    OutputFormat("wav", "WAV 24-bit PCM", "wav", ("-c:a", "pcm_s24le"),
                 lossless=True, note="no embedded cover art"),
    OutputFormat("w64", "Sony Wave64 24-bit", "w64", ("-c:a", "pcm_s24le"),
                 lossless=True, note="no embedded cover art"),
    OutputFormat("aiff", "AIFF 24-bit PCM", "aiff", ("-c:a", "pcm_s24be"),
                 lossless=True, supports_cover=True, note="cover via ID3 chunk"),
    OutputFormat("caf", "Apple CAF 24-bit", "caf", ("-c:a", "pcm_s24le"),
                 lossless=True, note="no embedded cover art"),
    OutputFormat("au", "Sun AU 24-bit", "au", ("-c:a", "pcm_s24be", "-f", "au"),
                 lossless=True, note="no embedded cover art"),
    OutputFormat("mp3", "MP3 320 kbps CBR", "mp3",
                 ("-c:a", "libmp3lame", "-b:a", "320k"), supports_cover=True,
                 vbr_quality="320 kbps"),
    OutputFormat("ogg", "Ogg Vorbis Q10", "ogg", ("-c:a", "libvorbis", "-q:a", "10"),
                 vbr_quality="~500 kbps", note="no embedded cover art"),
    OutputFormat("opus", "Opus 256 kbps", "opus",
                 ("-c:a", "libopus", "-b:a", "256k", "-vbr", "on",
                  "-compression_level", "10"),
                 vbr_quality="256 kbps", note="no embedded cover art"),
    OutputFormat("m4a", "AAC 320 kbps (M4A)", "m4a",
                 ("-c:a", "aac", "-b:a", "320k", "-f", "ipod"),
                 supports_cover=True, vbr_quality="320 kbps"),
    OutputFormat("alac", "Apple ALAC Lossless (M4A)", "m4a",
                 ("-c:a", "alac", "-f", "ipod"), lossless=True,
                 vbr_quality="lossless",
                 note="ALAC shares the .m4a extension; cover art not embedded"),
    OutputFormat("wma", "Windows Media 320 kbps", "wma",
                 ("-c:a", "wmav2", "-b:a", "320k"), supports_cover=True,
                 vbr_quality="320 kbps"),
    OutputFormat("wv", "WavPack Lossless", "wv",
                 ("-c:a", "wavpack", "-compression_level", "8"), lossless=True,
                 note="no embedded cover art"),
    OutputFormat("tta", "TTA Lossless", "tta",
                 ("-c:a", "tta", "-compression_level", "8"), lossless=True,
                 note="no embedded cover art"),
    OutputFormat("mka", "Matroska FLAC Lossless", "mka",
                 ("-c:a", "flac", "-sample_fmt", "s32", "-compression_level", "12"),
                 lossless=True, supports_cover=True),
    OutputFormat("ac3", "Dolby Digital AC-3 640k", "ac3",
                 ("-c:a", "ac3", "-b:a", "640k"), vbr_quality="640 kbps",
                 note="lossy cinema codec, no cover"),
    OutputFormat("eac3", "Dolby Digital Plus 640k", "eac3",
                 ("-c:a", "eac3", "-b:a", "640k"), vbr_quality="640 kbps",
                 note="lossy cinema codec, no cover"),
    OutputFormat("dts", "DTS 1536k", "dts",
                 ("-c:a", "dca", "-b:a", "1536k", "-strict", "-2"),
                 vbr_quality="1536 kbps", note="experimental encoder, no cover"),
    OutputFormat("mp2", "MPEG-1 Layer II 384k", "mp2",
                 ("-c:a", "mp2", "-b:a", "384k"), vbr_quality="384 kbps",
                 note="broadcast codec, no cover"),
    OutputFormat("voc", "Creative Voice 16-bit", "voc",
                 ("-c:a", "pcm_s16le"), lossless=True,
                 note="legacy mono/stereo PCM, no cover"),
    OutputFormat("amr", "AMR-NB 8 kHz Speech", "amr",
                 ("-c:a", "libopencore_amrnb", "-ar", "8000", "-ac", "1"),
                 vbr_quality="12.2 kbps", note="mono speech only, 8 kHz"),
)

FORMAT_BY_KEY: Dict[str, OutputFormat] = {f.key: f for f in OUTPUT_FORMATS}

# Source extensions the converter will read. Includes formats the player can
# decode but that cannot be written back (ape, tak, dsf, dff...).
READABLE_EXTENSIONS: Tuple[str, ...] = tuple(sorted(
    set(AUDIO_EXTENSIONS) | {".wma", ".dff", ".dsf", ".ape", ".tak", ".mpc", ".mpp"}
))

# Extensions that exist in the library but have no encoder in FFmpeg. Choosing
# one of these as the *target* is refused with a clear reason.
UNWRITABLE_REASONS = {
    ".ape": "FFmpeg has a Monkey's Audio decoder but no encoder",
    ".tak": "FFmpeg has a TAK decoder but no encoder",
    ".mpc": "FFmpeg has a Musepack decoder but no encoder",
    ".mpp": "FFmpeg has a Musepack decoder but no encoder",
    ".dsf": "FFmpeg has a DSF demuxer but no DSD encoder",
    ".dff": "FFmpeg has no DSDIFF demuxer or DSD encoder",
    ".ofr": "FFmpeg has no OptimFROG decoder or encoder",
    ".ofs": "FFmpeg has no OptimFROG decoder or encoder",
    ".mp1": "FFmpeg has no MPEG-1 Layer I encoder",
    ".spx": "Speex encoding needs 16 kHz mono and is offered via OGG/Opus instead",
}


def output_format_count() -> int:
    return len(OUTPUT_FORMATS)


def format_choices() -> List[Tuple[str, str, str]]:
    """(key, label, note) rows for the bash pickers."""
    return [(f.key, f.label, f.note or f.vbr_quality or "") for f in OUTPUT_FORMATS]


def describe_formats_table(theme: Optional[Dict[str, str]] = None) -> str:
    t = theme or {}
    bold, nc = t.get("bold", ""), t.get("nc", "")
    cyan, green, yellow = t.get("cyan", ""), t.get("green", ""), t.get("yellow", "")
    dim = t.get("dim", "")

    # Pad before colouring: ANSI escapes are zero-width but non-zero length, so
    # padding an already-coloured string breaks every column after it.
    cells: List[Tuple[str, str, bool, bool]] = []
    for fmt in OUTPUT_FORMATS:
        tag = f"{fmt.key.upper():<5}"
        cover = "cover" if fmt.supports_cover else ""
        cells.append((tag, fmt.label, fmt.lossless, fmt.supports_cover))

    lines = [f"  {bold}{cyan}The converter writes {len(OUTPUT_FORMATS)} audio output formats at the high preset:{nc}"]
    for index in range(0, len(cells), 2):
        parts = []
        for tag, label, lossless, has_cover in cells[index:index + 2]:
            kind_plain = "lossless" if lossless else "lossy"
            kind = f"{green}{kind_plain:<9}{nc}" if lossless else f"{yellow}{kind_plain:<9}{nc}"
            cover_mark = f"{dim}+cover{nc}" if has_cover else f"{dim}{'':<6}{nc}"
            # Pad the plain label before colouring anything.
            parts.append(f"  {bold}{tag}{nc} {label:<30} {kind} {cover_mark}")
        lines.append("".join(parts))
    return "\n".join(lines)


# ------------------------------------------------------------------------------
# Capability matrix
# ------------------------------------------------------------------------------
@dataclass
class ConversionPlan:
    source: str
    target_key: str
    allowed: bool
    reason: str = ""
    quality_note: str = ""

    @property
    def target(self) -> OutputFormat:
        return FORMAT_BY_KEY[self.target_key]


def plan_conversion(source_path: str, target_key: str) -> ConversionPlan:
    """Decide whether one source -> target conversion is worth attempting."""
    ext = os.path.splitext(str(source_path))[1].lower()
    if target_key not in FORMAT_BY_KEY:
        return ConversionPlan(source_path, target_key, False,
                              f"'{target_key}' is not a writable output format")
    target = FORMAT_BY_KEY[target_key]
    if ext == f".{target.extension}" and target_key not in ("alac",):
        return ConversionPlan(source_path, target_key, True,
                              quality_note="same container; re-encode refreshes tags")
    source_lossless = ext in LOSSLESS_EXTENSIONS
    if not source_lossless and target.lossless:
        return ConversionPlan(
            source_path, target_key, True,
            quality_note=(
                f"{ext} is already lossy: writing {target.label} keeps the "
                "existing loss, it cannot restore quality"),
        )
    return ConversionPlan(source_path, target_key, True)


def unwritable_reason(target_ext: str) -> str:
    return UNWRITABLE_REASONS.get(str(target_ext).lower(), "")


# ------------------------------------------------------------------------------
# Previous-conversion statistics
# ------------------------------------------------------------------------------
def _stats_log_candidates(explicit: Optional[str] = None) -> List[Path]:
    candidates: List[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_root = os.environ.get("MP_CONVERSION_LOG")
    if env_root:
        candidates.append(Path(env_root))
    bases = [Path.cwd()]
    for key in ("MIX_ARCHIVE_DIR",):
        if value := os.environ.get(key):
            bases.append(Path(value))
    app_root = Path(__file__).resolve().parent.parent
    bases.append(app_root)
    for base in bases:
        candidates.append(base / STATS_LOG_NAME)
        candidates.append(base / "scripts" / STATS_LOG_NAME)
    seen, unique = set(), []
    for c in candidates:
        try:
            resolved = str(c.resolve())
        except OSError:
            resolved = str(c)
        if resolved not in seen:
            seen.add(resolved)
            unique.append(c)
    return unique


@dataclass
class ConversionStats:
    counts: Dict[str, int] = field(default_factory=dict)
    bytes: Dict[str, int] = field(default_factory=dict)
    last_timestamp: str = ""
    log_path: str = ""
    entries: int = 0

    @property
    def total_files(self) -> int:
        return sum(self.counts.values())

    @property
    def total_bytes(self) -> int:
        return sum(self.bytes.values())


def read_conversion_stats(explicit_log: Optional[str] = None) -> ConversionStats:
    """Aggregate every completed conversion recorded in the JSONL log."""
    stats = ConversionStats()
    for candidate in _stats_log_candidates(explicit_log):
        if not candidate.is_file():
            continue
        try:
            lines = candidate.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        stats.log_path = str(candidate)
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("event", "")).upper() != "CONVERSION_COMPLETED":
                continue
            fmt = str(row.get("format", "")).strip().upper()
            if not fmt:
                continue
            stats.entries += 1
            stats.counts[fmt] = stats.counts.get(fmt, 0) + 1
            try:
                stats.bytes[fmt] = stats.bytes.get(fmt, 0) + int(row.get("size_bytes") or 0)
            except (TypeError, ValueError):
                pass
            ts = str(row.get("timestamp", ""))
            if ts > stats.last_timestamp:
                stats.last_timestamp = ts
        if stats.entries:
            break
    return stats


def human_bytes(value: int) -> str:
    size = float(max(0, int(value)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def render_stats_table(stats: ConversionStats, theme: Optional[Dict[str, str]] = None) -> str:
    """Themed table of previously converted counts per format plus a master total."""
    t = theme or {}
    bold, nc = t.get("bold", ""), t.get("nc", "")
    cyan, green, yellow = t.get("cyan", ""), t.get("green", ""), t.get("yellow", "")
    blue, dim = t.get("blue", ""), t.get("dim", "")
    rule = t.get("rule", "=" * 66)

    if not stats.total_files:
        return "\n".join([
            f"  {bold}{cyan}PREVIOUS CONVERSIONS{nc}  {dim}no completed conversions recorded yet{nc}",
            f"  {dim}{rule}{nc}",
            f"  {dim}The first batch will populate this table.{nc}",
        ])

    rows = sorted(stats.counts.items(), key=lambda kv: (-kv[1], kv[0]))
    name_w = max(12, max((len(k) for k, _ in rows), default=0) + 2)
    total_files = stats.total_files
    lines = [
        f"  {bold}{cyan}PREVIOUS CONVERSIONS ACROSS THE MIX ARCHIVE{nc}"
        f"  {dim}({total_files} recorded file(s)){nc}",
        f"  {dim}{rule}{nc}",
        f"  {bold}{blue}{'FORMAT':<{name_w}}{'FILES':>8}{'TOTAL SIZE':>14}{'SHARE':>10}{nc}",
        f"  {dim}{'-' * (name_w + 32)}{nc}",
    ]
    for fmt, count in rows:
        size = stats.bytes.get(fmt, 0)
        share = (count / total_files * 100.0) if total_files else 0.0
        bar_len = int(round(share / 5.0))
        bar = f"{green}{'█' * bar_len}{dim}{'░' * (20 - bar_len)}{nc}"
        lines.append(
            f"  {bold}{green}{fmt.upper():<{name_w}}{nc}"
            f"{yellow}{count:>8}{nc}"
            f"{cyan}{human_bytes(size):>14}{nc}"
            f"{share:>9.1f}%  {bar}"
        )
    lines.append(f"  {dim}{'-' * (name_w + 32)}{nc}")
    lines.append(
        f"  {bold}{blue}{'MASTER TOTAL':<{name_w}}{nc}"
        f"{bold}{green}{total_files:>8}{nc}"
        f"{bold}{cyan}{human_bytes(stats.total_bytes):>14}{nc}"
        f"{bold}{'100.0%':>10}{nc}"
    )
    if stats.last_timestamp:
        lines.append(f"  {dim}Last recorded conversion: {stats.last_timestamp}{nc}")
    if stats.log_path:
        lines.append(f"  {dim}Log: {stats.log_path}{nc}")
    return "\n".join(lines)


# ------------------------------------------------------------------------------
# Converted output folders
# ------------------------------------------------------------------------------
def _archive_roots() -> List[Path]:
    roots: List[Path] = []
    seen = set()

    def add(raw: Optional[str]) -> None:
        if not raw:
            return
        for part in str(raw).replace(";", "\n").replace(",", "\n").split("\n"):
            part = part.strip().strip('"').strip("'")
            if not part:
                continue
            try:
                path = Path(part).expanduser().resolve()
            except (OSError, ValueError):
                continue
            if path.is_dir() and str(path) not in seen:
                seen.add(str(path))
                roots.append(path)

    add(os.environ.get("MIX_ARCHIVE_DIR"))
    add(os.environ.get("EXTRA_MIX_ARCHIVE_DIRS"))
    app_root = Path(__file__).resolve().parent.parent
    add(str(app_root))
    return roots


@dataclass
class ConvertedFolder:
    path: str
    format_label: str
    count: int
    total_bytes: int

    @property
    def label(self) -> str:
        return self.format_label

    def as_row(self) -> Tuple[str, str, str]:
        return (self.path, f"{self.count} file(s)", f"{self.format_label} • {human_bytes(self.total_bytes)}")


def find_converted_folders(roots: Optional[Sequence[Path]] = None) -> List[ConvertedFolder]:
    """Every folder that already holds audio produced by a previous conversion."""
    folders: List[ConvertedFolder] = []
    seen = set()
    for root in (roots if roots is not None else _archive_roots()):
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            name = entry.name
            if not _is_conversion_output_dir(entry.path):
                continue
            real = os.path.realpath(entry.path)
            if real in seen:
                continue
            count, total, common = 0, 0, ""
            try:
                for item in os.scandir(entry.path):
                    try:
                        if not item.is_file(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    ext = os.path.splitext(item.name)[1].lower()
                    if ext not in READABLE_EXTENSIONS:
                        continue
                    try:
                        total += item.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
                    count += 1
                    common = ext
            except OSError:
                continue
            if count == 0:
                continue
            seen.add(real)
            label = name.replace(OUTPUT_DIR_SUFFIX, "").replace("_", " ").title()
            if common:
                label = f"{label} ({common.lstrip('.').upper()})"
            folders.append(ConvertedFolder(entry.path, label, count, total))
    folders.sort(key=lambda f: (-f.count, f.path.lower()))
    return folders


def render_folders_table(folders: Sequence[ConvertedFolder],
                         theme: Optional[Dict[str, str]] = None) -> str:
    t = theme or {}
    bold, nc = t.get("bold", ""), t.get("nc", "")
    cyan, green, dim, yellow = t.get("cyan", ""), t.get("green", ""), t.get("dim", ""), t.get("yellow", "")
    rule = t.get("rule", "=" * 66)
    if not folders:
        return "\n".join([
            f"  {bold}{cyan}CONVERTED AUDIO FOLDERS{nc}",
            f"  {dim}{rule}{nc}",
            f"  {yellow}No converted audio folders were found yet.{nc}",
        ])
    lines = [f"  {bold}{cyan}CONVERTED AUDIO FOLDERS ({len(folders)}){nc}", f"  {dim}{rule}{nc}"]
    for index, folder in enumerate(folders, start=1):
        lines.append(f"  {bold}{green}[{index:>2}]{nc} {bold}{folder.path}{nc}")
        lines.append(f"        {dim}{folder.count} file(s) • {human_bytes(folder.total_bytes)} • {folder.format_label}{nc}")
    return "\n".join(lines)


# ------------------------------------------------------------------------------
# Metadata presets and per-file metadata
# ------------------------------------------------------------------------------
DEFAULT_METADATA: Dict[str, str] = {
    "artist": "MPlanetarian",
    "album": "Stream of Frequency",
    "genre": "Electronic / Trance",
    "comment": "Stream of Frequency Mix Archive",
}

# Keys the converter writes into every container when present.
STANDARD_FIELDS: Tuple[str, ...] = (
    "title", "artist", "album_artist", "album", "date", "genre", "comment",
    "composer", "track", "copyright", "disc", "grouping", "lyrics",
)

# Disc-set fields the user asked for: disc 1 of 2, or "1/2".
DISC_FIELDS: Tuple[str, ...] = ("disc", "disctotal")


def load_presets() -> Dict[str, Any]:
    if not PRESETS_FILE.is_file():
        return {"presets": {}, "custom_fields": []}
    try:
        data = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"presets": {}, "custom_fields": []}
    if not isinstance(data, dict):
        return {"presets": {}, "custom_fields": []}
    data.setdefault("presets", {})
    data.setdefault("custom_fields", [])
    return data


def save_presets(data: Dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PRESETS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PRESETS_FILE)


def list_presets() -> List[Tuple[str, Dict[str, str]]]:
    data = load_presets()
    presets = data.get("presets") or {}
    if not isinstance(presets, dict):
        return []
    return sorted((str(name), dict(values)) for name, values in presets.items()
                  if isinstance(values, dict))


def save_preset(name: str, values: Dict[str, str]) -> None:
    data = load_presets()
    data.setdefault("presets", {})[str(name)] = {str(k): str(v) for k, v in values.items()}
    save_presets(data)


def delete_preset(name: str) -> bool:
    data = load_presets()
    presets = data.setdefault("presets", {})
    if str(name) in presets:
        presets.pop(str(name))
        save_presets(data)
        return True
    return False


def add_custom_field(field_name: str) -> None:
    name = str(field_name or "").strip()
    if not name:
        return
    data = load_presets()
    fields = [str(f) for f in data.get("custom_fields") or []]
    if name not in fields:
        fields.append(name)
        data["custom_fields"] = fields
        save_presets(data)


def custom_fields() -> List[str]:
    return [str(f) for f in (load_presets().get("custom_fields") or [])]


def normalize_disc(value: str, total: str) -> Dict[str, str]:
    """Accept "1/2", "2", or disc + total and return disc tags for ffmpeg."""
    raw = str(value or "").strip()
    raw_total = str(total or "").strip()
    disc, disc_total = raw, raw_total
    if "/" in raw:
        left, right = raw.split("/", 1)
        disc, disc_total = left.strip(), right.strip()
    out: Dict[str, str] = {}
    if disc:
        out["disc"] = disc
    if disc_total:
        out["disctotal"] = disc_total
    return out


def build_tag_args(metadata: Dict[str, str]) -> List[str]:
    """-metadata pairs for the standard and custom fields present in metadata."""
    args: List[str] = []
    for key, value in metadata.items():
        if value is None:
            continue
        text = str(value)
        if not text:
            continue
        name = str(key)
        if name == "disctotal":
            args += ["-metadata", f"disc={metadata.get('disc', '')}/{text}".rstrip("/")]
            continue
        if name == "disc" and metadata.get("disctotal"):
            continue  # written together with disctotal above
        args += ["-metadata", f"{name}={text}"]
    return args


# ------------------------------------------------------------------------------
# ffmpeg command builder
# ------------------------------------------------------------------------------
@dataclass
class ConversionRequest:
    source: str
    target_key: str
    output_path: str
    metadata: Dict[str, str] = field(default_factory=dict)
    front_cover: str = ""
    back_cover: str = ""
    disc_image: str = ""


def build_ffmpeg_command(request: ConversionRequest) -> List[str]:
    """Assemble the ffmpeg argv for one conversion, honouring cover limits."""
    target = FORMAT_BY_KEY[request.target_key]
    cmd: List[str] = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                      "-i", request.source]
    cover_inputs: List[str] = []
    if target.supports_cover:
        for candidate in (request.front_cover, request.back_cover, request.disc_image):
            if candidate and os.path.isfile(candidate):
                cover_inputs.append(candidate)
    for cover in cover_inputs:
        cmd += ["-i", cover]
    cmd += ["-map", "0:a"]
    for index in range(len(cover_inputs)):
        cmd += ["-map", f"{index + 1}:v"]
    cmd += ["-c:v", "mjpeg"] if cover_inputs else []
    for index in range(len(cover_inputs)):
        comment = "Cover (front)" if index == 0 else ("Cover (back)" if index == 1 else "Disc image")
        cmd += [f"-metadata:s:v:{index}", f"comment={comment}"]
        cmd += [f"-disposition:v:{index}", "attached_pic"]
    cmd += target.ffmpeg_output_args()
    cmd += build_tag_args(request.metadata)
    cmd += [request.output_path]
    return cmd


def describe_command(request: ConversionRequest) -> str:
    return " ".join(_quote_sh(part) for part in build_ffmpeg_command(request))


def _quote_sh(value: str) -> str:
    text = str(value)
    if not text or any(ch in text for ch in " \t\"'$`\\!&|;<>()*?[]#~"):
        return "'" + text.replace("'", "'\\''") + "'"
    return text


# ------------------------------------------------------------------------------
# Conversion execution with progress
# ------------------------------------------------------------------------------
def probe_duration(path: str) -> float:
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            stderr=subprocess.DEVNULL, timeout=15,
        ).decode("utf-8", "ignore").strip()
        return float(out) if out else 0.0
    except (subprocess.SubprocessError, ValueError, OSError):
        return 0.0


def run_conversion(request: ConversionRequest, quiet: bool = False,
                   progress_interval: float = 0.2) -> Tuple[bool, str]:
    """Run one conversion, printing an advanced progress line to stderr."""
    cmd = build_ffmpeg_command(request)
    os.makedirs(os.path.dirname(request.output_path) or ".", exist_ok=True)
    if shutil.which("ffmpeg") is None:
        return False, "ffmpeg is not installed"
    total = probe_duration(request.source)
    if quiet:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return proc.returncode == 0, (proc.stderr or "").strip()[-400:]

    import tempfile
    import time

    with tempfile.NamedTemporaryFile("r", suffix=".progress", delete=False) as handle:
        progress_path = handle.name
    run_cmd = cmd[:1] + ["-nostats", "-progress", progress_path] + cmd[1:]
    started = time.time()
    proc = subprocess.Popen(run_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    spin_index = 0
    try:
        while proc.poll() is None:
            pos, speed = _read_progress(progress_path)
            elapsed = max(0.0, time.time() - started)
            fraction = (pos / total) if total > 0 else 0.0
            fraction = max(0.0, min(1.0, fraction))
            eta = (elapsed * (1 - fraction) / fraction) if fraction > 0.01 else None
            bar_len = 26
            filled = int(fraction * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            speed_txt = f"{speed:.2f}x" if speed else "--"
            eta_txt = _clock(eta) if eta is not None else "--:--:--"
            sys.stderr.write(
                f"\r\033[K  ⟳ [{bar}] {fraction * 100:5.1f}%  "
                f"{_clock(pos)}/{_clock(total)}  ETA {eta_txt}  {speed_txt}"
            )
            sys.stderr.flush()
            spin_index = (spin_index + 1) % len(spin)
            time.sleep(progress_interval)
        proc.wait()
    finally:
        try:
            os.unlink(progress_path)
        except OSError:
            pass
        if proc.poll() is None:
            proc.kill()
    err = (proc.stderr.read() if proc.stderr else "") or ""
    if proc.returncode == 0:
        sys.stderr.write(f"\r\033[K  ✓ {Path(request.output_path).name}\n")
        return True, ""
    return False, err.strip()[-400:]


def _read_progress(path: str) -> Tuple[float, float]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            lines = handle.readlines()[-40:]
    except OSError:
        return 0.0, 0.0
    pos, speed = 0.0, 0.0
    for line in lines:
        if line.startswith("out_time_us="):
            value = line.split("=", 1)[1].strip()
            if value.isdigit():
                pos = int(value) / 1_000_000.0
        elif line.startswith("speed="):
            value = line.split("=", 1)[1].strip().rstrip("x")
            try:
                speed = float(value)
            except ValueError:
                speed = 0.0
    return pos, speed


def _clock(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0 or seconds != seconds:
        return "--:--:--"
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def estimate_batch(total_files: int, done: int, elapsed: float,
                   remaining_duration: float) -> Tuple[Optional[float], float]:
    """(ETA seconds, throughput files/sec) from the work finished so far."""
    if done <= 0 or elapsed <= 0:
        return None, 0.0
    rate = done / elapsed
    remaining_files = max(0, total_files - done)
    if rate <= 0:
        return None, 0.0
    eta = remaining_files / rate
    if remaining_duration > 0:
        eta = max(eta, remaining_duration)
    return eta, rate


# ------------------------------------------------------------------------------
# Batch conversion
# ------------------------------------------------------------------------------
def discover_sources(roots: Sequence[Path], extensions: Optional[Iterable[str]] = None,
                     include_converted: bool = False) -> List[str]:
    """Audio files to convert.

    Converted output folders are skipped by default: re-ingesting our own
    output would multiply the archive on every run.
    """
    wanted = {e.lower() for e in (extensions if extensions is not None else READABLE_EXTENSIONS)}
    found: List[str] = []
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if _is_conversion_output_dir(dirpath):
                dirnames[:] = []
                if not include_converted:
                    continue
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext not in wanted:
                    continue
                path = os.path.join(dirpath, name)
                real = os.path.realpath(path)
                if real in seen:
                    continue
                seen.add(real)
                found.append(path)
    return sorted(found)


def _is_conversion_output_dir(path: str) -> bool:
    name = Path(path).name.upper()
    return name.endswith(OUTPUT_DIR_SUFFIX) or name == "CONVERTED_WAV_FILES"


def group_by_format(paths: Sequence[str]) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {}
    for path in paths:
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        groups.setdefault(ext, []).append(path)
    return dict(sorted(groups.items()))


def batch_plan(paths: Sequence[str], target_keys: Sequence[str]) -> Tuple[List[ConversionPlan], List[str]]:
    """Plans for every source x target, plus human-readable refusals."""
    plans: List[ConversionPlan] = []
    refusals: List[str] = []
    for path in paths:
        for key in target_keys:
            plan = plan_conversion(path, key)
            if plan.allowed:
                plans.append(plan)
            else:
                refusals.append(f"{Path(path).name} -> {key}: {plan.reason}")
    return plans, refusals


# ------------------------------------------------------------------------------
# CLI used by the bash wizards
# ------------------------------------------------------------------------------
def _theme_from_env() -> Dict[str, str]:
    keys = ("bold", "dim", "nc", "cyan", "green", "yellow", "blue")
    theme = {k: os.environ.get(f"SOF_{k.upper()}", "") for k in keys}
    if not theme["nc"] and not os.environ.get("NO_COLOR"):
        theme = {
            "bold": "\033[1m", "dim": "\033[2m", "nc": "\033[0m",
            "cyan": "\033[38;2;0;193;228m", "green": "\033[38;2;0;229;255m",
            "yellow": "\033[38;2;255;170;0m", "blue": "\033[38;2;155;89;182m",
        }
    theme.setdefault("rule", "═" * 66)
    return theme


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MP Mix Manager audio conversion core")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("count", help="print how many output formats are available")
    sub.add_parser("choices", help="print key|label|note for the format picker")
    sub.add_parser("show-formats", help="print the themed output format table")

    stats_p = sub.add_parser("stats", help="print the previous conversion statistics table")
    stats_p.add_argument("--log", default=None)
    stats_p.add_argument("--json", action="store_true")

    sub.add_parser("folders", help="print the converted output folders")
    folders_p = sub.add_parser("folder-rows", help="print index|path|count|summary rows")
    folders_p.add_argument("--root", action="append", default=[])

    sub.add_parser("presets", help="print saved metadata presets as JSON")
    sub.add_parser("custom-fields", help="print saved custom metadata field names")
    add_p = sub.add_parser("add-custom-field")
    add_p.add_argument("name")

    plan_p = sub.add_parser("plan", help="check one source -> target conversion")
    plan_p.add_argument("source")
    plan_p.add_argument("target")

    cmd_p = sub.add_parser("command", help="print the ffmpeg command for one conversion")
    cmd_p.add_argument("source")
    cmd_p.add_argument("target")
    cmd_p.add_argument("output")
    cmd_p.add_argument("--metadata", default="{}")
    cmd_p.add_argument("--front-cover", default="")
    cmd_p.add_argument("--back-cover", default="")
    cmd_p.add_argument("--disc-image", default="")

    run_p = sub.add_parser("run", help="run one conversion")
    run_p.add_argument("source")
    run_p.add_argument("target")
    run_p.add_argument("output")
    run_p.add_argument("--metadata", default="{}")
    run_p.add_argument("--front-cover", default="")
    run_p.add_argument("--back-cover", default="")
    run_p.add_argument("--disc-image", default="")
    run_p.add_argument("--quiet", action="store_true")

    batch_p = sub.add_parser("batch-plan", help="report a multi-format batch plan")
    batch_p.add_argument("root", action="append")
    batch_p.add_argument("--target", action="append", default=[])
    batch_p.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    theme = _theme_from_env()

    if args.command == "count":
        print(output_format_count())
        return 0
    if args.command == "choices":
        for key, label, note in format_choices():
            print(f"{key}|{label}|{note}")
        return 0
    if args.command == "show-formats":
        print(describe_formats_table(theme))
        return 0
    if args.command == "stats":
        stats = read_conversion_stats(args.log)
        if args.json:
            print(json.dumps({
                "counts": stats.counts, "bytes": stats.bytes,
                "total_files": stats.total_files, "total_bytes": stats.total_bytes,
                "entries": stats.entries, "log": stats.log_path,
                "last": stats.last_timestamp,
            }))
        else:
            print(render_stats_table(stats, theme))
        return 0
    if args.command == "folders":
        print(render_folders_table(find_converted_folders(), theme))
        return 0
    if args.command == "folder-rows":
        roots = [Path(r) for r in args.root] if args.root else None
        for index, folder in enumerate(find_converted_folders(roots), start=1):
            print(f"{index}|{folder.path}|{folder.count}|{folder.label}|{folder.total_bytes}")
        return 0
    if args.command == "presets":
        print(json.dumps(load_presets(), ensure_ascii=False))
        return 0
    if args.command == "custom-fields":
        for name in custom_fields():
            print(name)
        return 0
    if args.command == "add-custom-field":
        add_custom_field(args.name)
        return 0
    if args.command == "plan":
        plan = plan_conversion(args.source, args.target)
        print(json.dumps({
            "allowed": plan.allowed, "reason": plan.reason,
            "quality_note": plan.quality_note,
        }))
        return 0 if plan.allowed else 1
    if args.command == "command":
        request = ConversionRequest(
            source=args.source, target_key=args.target, output_path=args.output,
            metadata=json.loads(args.metadata or "{}"),
            front_cover=args.front_cover, back_cover=args.back_cover,
            disc_image=args.disc_image,
        )
        print(describe_command(request))
        return 0
    if args.command == "run":
        request = ConversionRequest(
            source=args.source, target_key=args.target, output_path=args.output,
            metadata=json.loads(args.metadata or "{}"),
            front_cover=args.front_cover, back_cover=args.back_cover,
            disc_image=args.disc_image,
        )
        ok, detail = run_conversion(request, quiet=args.quiet)
        if not ok and detail:
            print(detail, file=sys.stderr)
        return 0 if ok else 1
    if args.command == "batch-plan":
        roots = [Path(r) for r in args.root]
        targets = args.target or [f.key for f in OUTPUT_FORMATS]
        sources = discover_sources(roots)
        groups = group_by_format(sources)
        plans, refusals = batch_plan(sources, targets)
        payload = {
            "sources": len(sources),
            "groups": {k: len(v) for k, v in groups.items()},
            "targets": targets,
            "conversions": len(plans),
            "refusals": refusals,
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"  Sources found: {payload['sources']}")
            for ext, count in groups.items():
                print(f"    {ext.upper():<6} {count}")
            print(f"  Targets: {', '.join(t.upper() for t in targets)}")
            print(f"  Planned conversions: {payload['conversions']}")
            if refusals:
                print("  Refused:")
                for reason in refusals[:20]:
                    print(f"    - {reason}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
