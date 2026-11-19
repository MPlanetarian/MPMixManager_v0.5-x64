#!/usr/bin/env python3
import sys
import os
import subprocess
import shutil
import datetime

# Setup paths and connection info
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST_DIR = os.environ.get("MIX_ARCHIVE_DIR") or os.path.join(_base, "MIX_ARCHIVE")
LOG_DIR = os.path.join(DEST_DIR, "IMPORT_LOGS")
os.makedirs(LOG_DIR, exist_ok=True)

TODAY = datetime.date.today().strftime('%Y-%m-%d')
LOG_FILE = os.path.join(LOG_DIR, f"import_{TODAY}.log")

START_DATE = datetime.datetime.now().strftime('%A, %Y-%m-%d')
START_TIME = datetime.datetime.now().strftime('%H:%M:%S')
start_seconds = datetime.datetime.now().timestamp()

def load_config_env():
    """Read config.env so SMB host and credentials are not baked into the file."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.environ.get("MP_CONFIG", ""),
        os.path.join(script_dir, "config.env"),
        os.path.join(os.path.dirname(script_dir), "config.env"),
        os.path.expanduser("~/.config/mix-manager/config.env"),
    ]
    env_vars = {}
    for config_file in candidates:
        if config_file and os.path.isfile(config_file):
            try:
                with open(config_file, "r", encoding="utf-8", errors="ignore") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, val = line.split("=", 1)
                        env_vars[key.strip()] = val.strip().strip('"').strip("'")
                break
            except OSError:
                continue
    return env_vars


def _default_smb_sources():
    """Build the SMB source list from configuration, never from literals.

    The previous version embedded a host, a user and a password. A password in
    tracked source is not a default, it is a leak, and it is also visible in
    `ps` because rclone received it as an argument.
    """
    env_vars = load_config_env()

    def setting(*names):
        for name in names:
            value = os.environ.get(name) or env_vars.get(name)
            if value:
                return str(value).strip()
        return ""

    host = setting("MUSIC_SMB_HOST", "DJ_SMB_HOST", "SMB_DEFAULT_HOST")
    share = setting("DJ_SMB_SHARE", "MUSIC_SMB_SHARE") or "mplanetarian"
    user = setting("MUSIC_SMB_USER", "DJ_SMB_USER")
    password = setting("MUSIC_SMB_PASSWORD", "DJ_SMB_PASSWORD")
    if not host:
        return []

    auth = [f'host="{host}"']
    if user:
        auth.append(f'user="{user}"')
    if password:
        auth.append(f'pass="{password}"')
    prefix = ":smb," + ",".join(auth) + ":"

    def source(name, sub):
        path = f"{share}/{sub}".rstrip("/") + "/"
        return {"name": name, "url": f"smb://{host}/{path}", "rclone_path": prefix + path}

    return [
        # The folder on disk is named TRAKTOR_RECORDINGS (an existing
        # recordings tree); only the label is the suite's neutral wording.
        source("DJ RECORDINGS (SMB)",
               setting("DJ_RECORDINGS_DIR")
               or setting("TRAKTOR_RECORDINGS_DIR")
               or "Documents/TRAKTOR_RECORDINGS"),
        source("MIX ARCHIVE (SMB)",
               setting("MIX_ARCHIVE_SMB_DIR") or "Documents/MPlanetarian/M_PRODUCTION/MIX_ARCHIVE"),
    ]


# SMB Sources
SMB_SOURCES = _default_smb_sources()

# Audio extensions to look for
AUDIO_EXTS = ['.wav', '.flac', '.mp3', '.m4a', '.ogg', '.wma']

def get_audio_base(filename):
    lower = filename.lower()
    for ext in AUDIO_EXTS:
        if lower.endswith(ext):
            return lower[:-len(ext)]
    return None

def log_message(msg):
    timestamp = datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S')
    log_line = f"{timestamp} INFO  : {msg}\n"
    with open(LOG_FILE, "a") as lf:
        lf.write(log_line)

# 1. Scan local directories to find already processed files
print("Scanning local directories to identify processed files...")
processed_bases = set()

local_scan_dirs = [
    DEST_DIR,
    os.path.join(DEST_DIR, "CONVERTED_WAV_FILES"),
    "/run/media/mplanetarian/VROC/M_PRODUCTION/STREAM_OF_FREQUENCY/CONVERTED_WAV_FILES_MOVED"
]

for sdir in local_scan_dirs:
    if os.path.isdir(sdir):
        for f in os.listdir(sdir):
            base = get_audio_base(f)
            if base:
                processed_bases.add(base)

print(f" -> Found {len(processed_bases)} unique processed mix bases.")

# 2. Scan remote shares to find new mixes to copy
files_to_copy_by_source = []
total_bytes_to_copy = 0

print("Scanning SMB shares for new WAV files...")
for idx, src in enumerate(SMB_SOURCES):
    print(f" -> Checking share: {src['url']}")
    # Run rclone lsf to get size and path of files
    cmd = [
        "rclone", "lsf", "--csv", "--files-only", "--recursive",
        "--format", "sp", src["rclone_path"]
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Error checking SMB source {src['name']}: {res.stderr}")
        continue
        
    src_files = []
    lines = res.stdout.strip().split("\n")
    for line in lines:
        if not line or "," not in line:
            continue
        parts = line.split(",", 1)
        try:
            size = int(parts[0])
            rel_path = parts[1]
        except ValueError:
            continue
            
        filename = os.path.basename(rel_path)
        # We only care about WAV files for import
        if not (filename.lower().endswith(".wav")):
            continue
            
        base = get_audio_base(filename)
        if base and base not in processed_bases:
            src_files.append((rel_path, size))
            total_bytes_to_copy += size
            
    files_to_copy_by_source.append(src_files)

# 3. Check disk space on destination drive
free_space = shutil.disk_usage(DEST_DIR).free

print(f"Total new size to copy: {total_bytes_to_copy / (1024**3):.2f} GB")
print(f"Available space: {free_space / (1024**3):.2f} GB")

if total_bytes_to_copy > free_space:
    err_msg = "ERROR: Not enough space to copy data on drive."
    print(f"\033[1;31m{err_msg}\033[0m")
    log_message(err_msg)
    # Print clean summary layout
    summary_lines = [
        "\n==================================================",
        "IMPORT JOB SUMMARY",
        "==================================================",
        "Status:       FAILED (Not enough space on drive)",
        f"Date:         {START_DATE}",
        f"Start Time:   {START_TIME}",
        f"End Time:     {datetime.datetime.now().strftime('%H:%M:%S')}",
        "Total Runtime: 0h 0m 0s",
        f"Sources:      {', '.join([s['name'] for s in SMB_SOURCES])}",
        f"Destination:  {DEST_DIR}",
        f"New WAVs Imported: 0 files",
        f"Log File:     {LOG_FILE}",
        "==================================================\n"
    ]
    summary_text = "\n".join(summary_lines)
    with open(LOG_FILE, "a") as lf:
        lf.write(summary_text)
    print(summary_text)
    sys.exit(1)

# 4. Copy new mixes
copied_files_count = 0
rclone_status = 0

for idx, src_files in enumerate(files_to_copy_by_source):
    if not src_files:
        continue
    src = SMB_SOURCES[idx]
    print(f"Copying {len(src_files)} file(s) from {src['url']}...")
    
    # Write the files to copy to a temp list file
    list_file_path = f"/tmp/rclone_import_list_{idx}.txt"
    with open(list_file_path, "w") as f:
        for rel_path, _ in src_files:
            f.write(f"{rel_path}\n")
            
    # Run rclone copy using the list
    cmd = [
        "rclone", "copy", src["rclone_path"], DEST_DIR,
        "--files-from-raw", list_file_path,
        "--progress",
        "--log-file", LOG_FILE,
        "--log-level", "INFO"
    ]
    
    res = subprocess.run(cmd)
    if res.returncode != 0:
        rclone_status = res.returncode
        print(f"Warning: rclone finished with non-zero exit code {res.returncode}")
        
    copied_files_count += len(src_files)
    
    # Clean up temp file
    if os.path.exists(list_file_path):
        os.remove(list_file_path)

# 5. Build summary text
end_seconds = datetime.datetime.now().timestamp()
total_run_time = int(end_seconds - start_seconds)
hours = total_run_time // 3600
mins = (total_run_time % 3600) // 60
secs = total_run_time % 60
formatted_runtime = f"{hours}h {mins}m {secs}s"

status_str = "SUCCESS" if rclone_status == 0 else "FAILED"

summary_lines = [
    "\n==================================================",
    "IMPORT JOB SUMMARY",
    "==================================================",
    f"Status:       {status_str}",
    f"Date:         {START_DATE}",
    f"Start Time:   {START_TIME}",
    f"End Time:     {datetime.datetime.now().strftime('%H:%M:%S')}",
    f"Total Runtime: {formatted_runtime}",
    f"Sources:      {', '.join([s['name'] for s in SMB_SOURCES])}",
    f"Destination:  {DEST_DIR}",
    f"New WAVs Imported: {copied_files_count} files",
    f"Log File:     {LOG_FILE}",
    "==================================================\n"
]
summary_text = "\n".join(summary_lines)

# Write summary to log
with open(LOG_FILE, "a") as lf:
    lf.write(summary_text)

# Print summary to terminal
if rclone_status == 0:
    print(f"\033[1;32mImport Completed Successfully!\033[0m")
else:
    print(f"\033[1;31mImport Job Failed! Check the log file for errors.\033[0m")
print(summary_text)

sys.exit(rclone_status)
