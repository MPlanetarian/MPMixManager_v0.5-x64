#!/usr/bin/env python3
"""
MP_Mix_Manager_v0.3 - Custom Playlists Creator & Multi-Player Launcher
Allows users to:
  - Create and edit custom playlists (.m3u, .m3u8, .xspf) from mixes in the archive
  - Search, filter, and add audio mixes by title, episode number, or date
  - Re-order or remove tracks within playlists
  - Launch any installed audio player (cliamp, Strawberry, VLC, MPV, foobar2000, Kodi)
    directly loaded with the custom playlist!
"""

import os
import sys
import subprocess
import shutil
import re
import time
import argparse
from pathlib import Path

# --- ANSI Colors ---
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[0;32m"
YELLOW = "\033[0;33m"
RED = "\033[0;31m"
BLUE = "\033[0;34m"
MAGENTA = "\033[0;35m"
CYAN = "\033[0;36m"
WHITE = "\033[1;37m"
NC = "\033[0m"

AUDIO_EXTS = ['.flac', '.wav', '.mp3', '.m4a', '.aif', '.ogg']

# One playlist module for every container, shared with MP_Audio_Player.py.
# Imported lazily through a path insert so this script keeps working when it is
# copied beside the player instead of into scripts/.
def _playlist_formats_module():
    here = Path(__file__).resolve().parent
    for candidate in (here / "scripts", here, here.parent / "scripts"):
        if (candidate / "playlist_formats.py").is_file():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            break
    import playlist_formats
    return playlist_formats

PLAYLIST_SUFFIXES = ('.m3u', '.m3u8', '.xspf', '.pls', '.json')

def get_base_dir():
    return Path(__file__).resolve().parent

def get_playlists_dir():
    p_dir = get_base_dir() / "playlists"
    p_dir.mkdir(parents=True, exist_ok=True)
    return p_dir

def list_archive_mixes():
    base_dir = get_base_dir()
    env_dir = os.environ.get("MIX_ARCHIVE_DIR")
    arch_dir = Path(env_dir) if env_dir and Path(env_dir).is_dir() else (base_dir / "MIX_ARCHIVE")
    scan_paths = [
        arch_dir / "FLAC_CONVERTED_OUTPUTS",
        arch_dir,
        base_dir / "MIX_ARCHIVE" / "FLAC_CONVERTED_OUTPUTS",
        base_dir / "FLAC_CONVERTED_OUTPUTS",
        base_dir / "CONVERTED_WAV_FILES",
        base_dir
    ]
    extra_env = os.environ.get("EXTRA_MIX_ARCHIVE_DIRS")
    if not extra_env:
        cfg = base_dir / "config.env"
        if cfg.is_file():
            try:
                with open(cfg, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if line.startswith("EXTRA_MIX_ARCHIVE_DIRS="):
                            extra_env = line.split("=", 1)[1].strip().strip('"').strip("'")
            except Exception:
                pass
    if extra_env:
        raw_extra = extra_env.strip()
        extras = []
        for sep in ('\n', ';', ','):
            if sep in raw_extra:
                extras = [x.strip().strip('"').strip("'") for x in raw_extra.split(sep) if x.strip().strip('"').strip("'")]
                break
        else:
            if re.search(r':[a-zA-Z]:', raw_extra):
                _fixed = re.sub(r':([a-zA-Z]:)', r';\1', raw_extra)
                extras = [x.strip().strip('"').strip("'") for x in _fixed.split(';') if x.strip().strip('"').strip("'")]
            elif ':' in raw_extra and not re.match(r'^[a-zA-Z]:', raw_extra):
                extras = [x.strip().strip('"').strip("'") for x in raw_extra.split(':') if x.strip().strip('"').strip("'")]
            elif raw_extra:
                extras = [raw_extra.strip('"').strip("'")]
        for ed in extras:
            p = Path(ed)
            if (p / "FLAC_CONVERTED_OUTPUTS").is_dir():
                scan_paths.append(p / "FLAC_CONVERTED_OUTPUTS")
            if p.is_dir():
                scan_paths.append(p)
    seen = set()
    mixes = []
    for sdir in scan_paths:
        if not sdir.is_dir():
            continue
        try:
            for entry in os.scandir(str(sdir)):
                if entry.is_file():
                    lower = entry.name.lower()
                    if any(lower.endswith(ext) for ext in AUDIO_EXTS):
                        if entry.name not in seen and entry.stat().st_size >= 20 * 1024 * 1024:
                            seen.add(entry.name)
                            mixes.append({
                                "filename": entry.name,
                                "path": entry.path,
                                "size": entry.stat().st_size
                            })
        except Exception:
            pass
    mixes.sort(key=lambda x: x["filename"].lower())
    return mixes

def get_generated_playlists_dir():
    g_dir = get_base_dir() / "PLAYLISTS_GENERATED"
    g_dir.mkdir(parents=True, exist_ok=True)
    return g_dir

def list_playlists():
    p_dir = get_playlists_dir()
    g_dir = get_generated_playlists_dir()
    playlists = []
    seen_names = set()
    for d in (g_dir, p_dir):
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix.lower() in PLAYLIST_SUFFIXES:
                    if f.name not in seen_names:
                        seen_names.add(f.name)
                        playlists.append(f)
    playlists.sort(key=lambda x: x.name.lower())
    return playlists

def parse_m3u(file_path):
    """Read any supported playlist type into a list of track paths."""
    try:
        rows = _playlist_formats_module().read_playlist(file_path)
    except Exception:
        return []
    return [row["path"] for row in rows if row.get("path")]

def write_m3u(file_path, track_paths, fmt=None):
    """Write `track_paths` in the container named by the file's suffix."""
    tracks = []
    for t in track_paths:
        p = Path(t)
        try:
            size_mb = p.stat().st_size / 1048576.0
            mtime = p.stat().st_mtime
        except OSError:
            size_mb, mtime = 0.0, time.time()
        tracks.append({"name": p.name, "title": p.stem, "path": str(t),
                       "ext": p.suffix.lstrip(".").upper(), "size_mb": size_mb,
                       "mtime": mtime})
    return str(_playlist_formats_module().write_playlist(file_path, tracks, fmt))

def launch_playlist_in_player(playlist_path, player="cliamp"):
    playlist_path = Path(playlist_path).resolve()
    print(f"\n{BOLD}{GREEN}Launching {player} with playlist: {playlist_path.name}...{NC}")
    
    if player == "cliamp":
        # Launch cliamp with playlist
        cliamp_bin = shutil.which("cliamp") or (get_base_dir() / "bin" / "cliamp")
        if os.name == 'posix' and shutil.which("konsole"):
            subprocess.Popen(["konsole", "--hold", "-e", str(cliamp_bin), str(playlist_path)])
        elif os.name == 'posix' and shutil.which("ptyxis"):
            subprocess.Popen(["ptyxis", "--", str(cliamp_bin), str(playlist_path)])
        else:
            subprocess.Popen([str(cliamp_bin), str(playlist_path)])
    elif player == "strawberry":
        subprocess.Popen(["strawberry", str(playlist_path)])
    elif player == "vlc":
        if shutil.which("vlc"):
            subprocess.Popen(["vlc", str(playlist_path)])
        elif shutil.which("flatpak"):
            subprocess.Popen(["flatpak", "run", "org.videolan.VLC", str(playlist_path)])
    elif player == "mpv":
        if shutil.which("mpv"):
            subprocess.Popen(["mpv", f"--playlist={playlist_path}"])
    elif player == "kodi":
        if shutil.which("kodi"):
            subprocess.Popen(["kodi", str(playlist_path)])
        elif shutil.which("flatpak"):
            subprocess.Popen(["flatpak", "run", "tv.kodi.Kodi", str(playlist_path)])
    else:
        # Fallback to system default
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(playlist_path)])
        elif sys.platform == "win32":
            subprocess.Popen(["cmd.exe", "/c", "start", "", str(playlist_path)])
        else:
            subprocess.Popen(["xdg-open", str(playlist_path)])

def interactive_ui():
    while True:
        os.system('clear' if os.name == 'posix' else 'cls')
        playlists = list_playlists()
        
        print(f"{BOLD}{MAGENTA}======================================================================{NC}")
        print(f"{BOLD}{MAGENTA}        Custom Playlists Creator & Audio Player Launcher              {NC}")
        print(f"{BOLD}{MAGENTA}======================================================================{NC}")
        print(f"  Playlist Directory: {CYAN}{get_playlists_dir()}{NC}")
        print(f"  Saved Playlists:    {WHITE}{len(playlists)}{NC}")
        print(f"{BOLD}{BLUE}──────────────────────────────────────────────────────────────────────{NC}")
        
        if playlists:
            print(f"{BOLD}Existing Playlists:{NC}")
            for idx, p in enumerate(playlists, start=1):
                tracks = parse_m3u(p)
                print(f"  {BOLD}{CYAN}{idx:2d}){NC} {p.name:<36} {DIM}({len(tracks)} mix tracks){NC}")
            print("")
        else:
            print(f"  {DIM}No playlists created yet.{NC}\n")
            
        print(f"{BOLD}Playlist Operations:{NC}")
        print(f"  {BOLD}{CYAN} C){NC} Create New Custom Playlist (.m3u8)")
        print(f"  {BOLD}{CYAN} G){NC} {BOLD}{GREEN}Generate Mix Archive Folder Playlists (from Configured Storage Folders){NC}")
        print(f"  {BOLD}{CYAN} I){NC} Import / Open a Playlist File (any folder, any supported type)")
        print(f"  {BOLD}{CYAN} L){NC} Launch a Playlist in Audio Player (cliamp, Strawberry, VLC, etc.)")
        print(f"  {BOLD}{CYAN} E){NC} Edit / Re-order Tracks in Existing Playlist")
        print(f"  {BOLD}{CYAN} D){NC} Delete a Playlist")
        if sys.platform == "darwin":
            print(f"  {BOLD}{CYAN} T){NC} Generate DJ Playlist from History Files (macOS)")
        print(f"\n  {BOLD}{CYAN} 0){NC} Return to Main Menu {DIM}(or 'q'){NC}")
        print(f"{BOLD}{BLUE}──────────────────────────────────────────────────────────────────────{NC}")
        
        cmd = input(f"{BOLD}Enter choice: {NC}").strip()
        if cmd in ('0', 'q', 'exit', 'quit'):
            break
            
        if cmd.lower() == 'c':
            create_playlist_wizard()
        elif cmd.lower() == 'g':
            gen_script = get_base_dir() / "scripts" / "generate_archive_folder_playlists.py"
            if not gen_script.is_file():
                gen_script = get_base_dir() / "generate_archive_folder_playlists.py"
            if gen_script.is_file():
                subprocess.run([sys.executable, str(gen_script)])
            else:
                print(f"{RED}Error: generate_archive_folder_playlists.py not found!{NC}")
                time.sleep(1)
        elif cmd.lower() == 't' and sys.platform == "darwin":
            dj_script = get_base_dir() / "generate_dj_playlist_from_history.py"
            if dj_script.is_file():
                subprocess.run([sys.executable, str(dj_script)])
            else:
                print(f"{RED}Error: generate_dj_playlist_from_history.py not found!{NC}")
                time.sleep(1)
        elif cmd.lower() == 'i':
            import_playlist_wizard()
        elif cmd.lower() == 'l':
            if not playlists:
                print(f"{YELLOW}No playlists available.{NC}")
                time.sleep(1)
                continue
            sel = input(f"Select playlist to launch [1-{len(playlists)}]: ").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(playlists):
                p_file = playlists[int(sel) - 1]
                print(f"\nSelect Audio Player:")
                print(f"  1) cliamp (Terminal Player)")
                print(f"  2) Strawberry Music Player")
                print(f"  3) VLC Media Player")
                print(f"  4) MPV Player")
                print(f"  5) Kodi Media Center")
                p_sel = input("Select player [1-5, default 1]: ").strip() or "1"
                p_map = {"1": "cliamp", "2": "strawberry", "3": "vlc", "4": "mpv", "5": "kodi"}
                player_name = p_map.get(p_sel, "cliamp")
                launch_playlist_in_player(p_file, player_name)
                time.sleep(1)
        elif cmd.lower() == 'e':
            if not playlists:
                continue
            sel = input(f"Select playlist to edit [1-{len(playlists)}]: ").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(playlists):
                edit_playlist_wizard(playlists[int(sel) - 1])
        elif cmd.lower() == 'd':
            if not playlists:
                continue
            sel = input(f"Select playlist to delete [1-{len(playlists)}]: ").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(playlists):
                to_del = playlists[int(sel) - 1]
                conf = input(f"Delete {to_del.name}? [y/N]: ").strip().lower()
                if conf in ('y', 'yes'):
                    to_del.unlink()
                    print(f"{RED}✓ Playlist deleted.{NC}")
                    time.sleep(0.8)

def _ask_playlist_type(default=".m3u8"):
    """Ask which container to write. Every type the player opens is offered."""
    print(f"\n{BOLD}Playlist type:{NC}")
    print(f"  {BOLD}{CYAN}1){NC} M3U            {DIM}(universal){NC}")
    print(f"  {BOLD}{CYAN}2){NC} M3U8 (UTF-8)   {DIM}(universal, non-ASCII titles)${NC}")
    print(f"  {BOLD}{CYAN}3){NC} XSPF           {DIM}(XML Shareable Playlist)${NC}")
    print(f"  {BOLD}{CYAN}4){NC} PLS            {DIM}(numbered entries)${NC}")
    print(f"  {BOLD}{CYAN}5){NC} JSON           {DIM}(MP Mix Manager, keeps metadata)${NC}")
    choice = input(f"Choose [1-5, default {'2' if default == '.m3u8' else '1'}]: ").strip()
    return {"1": ".m3u", "2": ".m3u8", "3": ".xspf", "4": ".pls", "5": ".json"}.get(choice, default)

def import_playlist_wizard():
    """Open a playlist file from anywhere and copy it into the playlist folder.

    The player can open a file in place with Shift+O, so this wizard exists for
    the other two jobs: checking that a list from another machine resolves here,
    and keeping a copy beside the generated playlists.
    """
    print(f"\n{BOLD}{MAGENTA}--- Import / Open a Playlist File ---{NC}")
    print(f"  Supported types: {BOLD}{CYAN}{', '.join(PLAYLIST_SUFFIXES)}{NC}")
    raw = input("Path to the playlist (any folder, ~ allowed): ").strip()
    raw = raw.strip('"').strip("'")
    if not raw:
        return
    src = Path(raw).expanduser()
    if not src.is_file():
        print(f"{RED}Not found: {src}{NC}")
        time.sleep(1.4)
        return
    if src.suffix.lower() not in PLAYLIST_SUFFIXES:
        print(f"{RED}Not a supported playlist type: {src.suffix or '(none)'}{NC}")
        time.sleep(1.4)
        return
    try:
        rows = _playlist_formats_module().read_playlist(src)
    except Exception as exc:
        print(f"{RED}Could not read that playlist: {exc}{NC}")
        time.sleep(1.6)
        return
    present = [row for row in rows if row.get("path") and Path(row["path"]).is_file()]
    missing = len(rows) - len(present)
    print(f"  Tracks named:      {WHITE}{len(rows)}{NC}")
    print(f"  Playable here:     {GREEN}{len(present)}{NC}")
    if missing:
        print(f"  Not on this machine: {YELLOW}{missing}{NC}")
    if not rows:
        print(f"{RED}That playlist is empty.{NC}")
        time.sleep(1.4)
        return
    for row in rows[:12]:
        mark = f"{GREEN}✓{NC}" if Path(row["path"]).is_file() else f"{YELLOW}?{NC}"
        print(f"    {mark} {Path(row['path']).name}")
    if len(rows) > 12:
        print(f"    {DIM}... and {len(rows) - 12} more{NC}")
    keep = input(f"\nCopy it into the playlist folder? [y/N]: ").strip().lower()
    if keep in ('y', 'yes'):
        dest = get_playlists_dir() / src.name
        try:
            shutil.copy2(src, dest)
            print(f"{GREEN}✓ Copied to {dest}{NC}")
        except OSError as exc:
            print(f"{RED}Copy failed: {exc}{NC}")
    play = input("Open it in the MP Audio Player now? [Y/n]: ").strip().lower()
    if play not in ('n', 'no'):
        player = get_base_dir() / "MP_Audio_Player.py"
        if not player.is_file():
            player = get_base_dir() / "scripts" / "MP_Audio_Player.py"
        if player.is_file():
            subprocess.Popen([sys.executable, str(player), "--open-playlist", str(src)])
            print(f"{GREEN}✓ Opening {src.name} in MP Audio Player...{NC}")
        else:
            print(f"{RED}MP_Audio_Player.py was not found, so it could not be opened.{NC}")
    time.sleep(1.2)

def create_playlist_wizard():
    print(f"\n{BOLD}{MAGENTA}--- Create New Custom Playlist ---{NC}")
    name = input("Enter Playlist Name (e.g. Best_Of_SOF): ").strip()
    if not name:
        return
    # A recognised suffix is the user's own choice; anything else asks once.
    if not name.lower().endswith(PLAYLIST_SUFFIXES):
        name += _ask_playlist_type()
        
    p_file = get_playlists_dir() / name
    all_mixes = list_archive_mixes()
    selected_paths = []
    
    filter_q = ""
    while True:
        os.system('clear' if os.name == 'posix' else 'cls')
        filtered = [m for m in all_mixes if not filter_q or filter_q.lower() in m["filename"].lower()]
        
        print(f"{BOLD}{MAGENTA}Creating Playlist: {name}{NC}")
        print(f"Selected Tracks: {GREEN}{len(selected_paths)}{NC} mixes in playlist")
        if filter_q:
            print(f"Active Filter: {CYAN}{filter_q}{NC}")
        print(f"{BOLD}{BLUE}──────────────────────────────────────────────────────────────────────{NC}")
        
        for idx, m in enumerate(filtered[:25], start=1):
            fname = m["filename"]
            if len(fname) > 55:
                fname = fname[:52] + "..."
            in_list = f"{GREEN}★ ADDED{NC}" if m["path"] in selected_paths else ""
            print(f"  {idx:2d}) {fname:<55} {in_list}")
            
        print(f"{BLUE}──────────────────────────────────────────────────────────────────────{NC}")
        print("Options: Enter numbers (e.g. 1,3,5), '/<query>' to filter, 'save' to finish, 'cancel' to exit")
        cmd = input(f"{BOLD}Select: {NC}").strip()
        
        if cmd.lower() == 'cancel':
            return
        elif cmd.lower() == 'save':
            if selected_paths:
                written = write_m3u(p_file, selected_paths)
                print(f"\n{GREEN}✓ Playlist successfully created with "
                      f"{len(selected_paths)} tracks ({p_file.suffix}): {NC}{written}")
                input("Press Enter to continue...")
            return
        elif cmd.startswith('/'):
            filter_q = cmd[1:].strip()
        else:
            try:
                for part in cmd.split(','):
                    part = part.strip()
                    if part.isdigit():
                        i = int(part)
                        if 1 <= i <= len(filtered):
                            p = filtered[i - 1]["path"]
                            if p not in selected_paths:
                                selected_paths.append(p)
                            else:
                                selected_paths.remove(p)
            except Exception:
                pass

def edit_playlist_wizard(p_file):
    tracks = parse_m3u(p_file)
    while True:
        os.system('clear' if os.name == 'posix' else 'cls')
        print(f"{BOLD}{MAGENTA}Editing Playlist: {p_file.name}{NC}")
        print(f"Total Tracks: {len(tracks)}")
        print(f"{BOLD}{BLUE}──────────────────────────────────────────────────────────────────────{NC}")
        for idx, t in enumerate(tracks, start=1):
            print(f"  {idx:2d}) {Path(t).name}")
        print(f"{BLUE}──────────────────────────────────────────────────────────────────────{NC}")
        print("Options: 'r <index>' to remove track, 'add' to append tracks, 'save' to finish, '0' to cancel")
        cmd = input(f"{BOLD}Select: {NC}").strip()
        if cmd in ('0', 'cancel'):
            return
        elif cmd == 'save':
            write_m3u(p_file, tracks)
            print(f"{GREEN}✓ Playlist updated ({p_file.suffix}).{NC}")
            time.sleep(1)
            return
        elif cmd.startswith('r '):
            idx_str = cmd[2:].strip()
            if idx_str.isdigit() and 1 <= int(idx_str) <= len(tracks):
                tracks.pop(int(idx_str) - 1)
        elif cmd == 'add':
            mixes = list_archive_mixes()
            print("\nSelect mix to append:")
            for i, m in enumerate(mixes[:15], start=1):
                print(f"  {i:2d}) {m['filename']}")
            asel = input(f"Select [1-{min(15, len(mixes))}]: ").strip()
            if asel.isdigit() and 1 <= int(asel) <= min(15, len(mixes)):
                tracks.append(mixes[int(asel) - 1]["path"])

def main():
    parser = argparse.ArgumentParser(description="Custom Playlists Creator & Launcher")
    parser.add_argument("--list", action="store_true", help="List all playlists")
    parser.add_argument("--generate-archive-playlists", action="store_true", help="Generate playlists for all configured mix archive folders")
    parser.add_argument("--launch", help="Playlist path or name to launch")
    parser.add_argument("--player", default="cliamp", help="Audio player to launch")
    parser.add_argument("--import", dest="import_path", metavar="PLAYLIST",
                        help="Open a playlist file of any supported type")
    parser.add_argument("--convert", nargs=2, metavar=("SOURCE", "DEST"),
                        help="Re-write a playlist into another container (e.g. --convert set.m3u set.xspf)")
    args = parser.parse_args()
    
    if args.generate_archive_playlists:
        gen_script = get_base_dir() / "scripts" / "generate_archive_folder_playlists.py"
        if not gen_script.is_file():
            gen_script = get_base_dir() / "generate_archive_folder_playlists.py"
        if gen_script.is_file():
            subprocess.run([sys.executable, str(gen_script), "--all"])
    elif args.list:
        for p in list_playlists():
            print(f"{p.name} ({len(parse_m3u(p))} tracks)")
    elif args.launch:
        launch_playlist_in_player(args.launch, args.player)
    elif args.import_path:
        player = get_base_dir() / "MP_Audio_Player.py"
        if not player.is_file():
            player = get_base_dir() / "scripts" / "MP_Audio_Player.py"
        if player.is_file():
            subprocess.call([sys.executable, str(player), "--open-playlist", args.import_path])
        else:
            print(f"{RED}MP_Audio_Player.py was not found.{NC}")
    elif args.convert:
        source, dest = (Path(x).expanduser() for x in args.convert)
        paths = parse_m3u(source)
        if not paths:
            print(f"{RED}No tracks found in {source}{NC}")
            sys.exit(1)
        written = write_m3u(dest, paths)
        print(f"{GREEN}✓ Converted {len(paths)} track(s): {source.name} -> {written}{NC}")
    else:
        interactive_ui()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("")
        sys.exit(0)
