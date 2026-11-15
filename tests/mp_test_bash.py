"""Bash interpreter discovery shared by the test suite.

The manager needs bash 4+ (`declare -A`, `mapfile`, `[[ =~ ]]` with quoting
rules bash 3.2 gets wrong). On macOS `/bin/bash` is still 3.2.57, so a test
that runs `["bash", ...]` gets a shell that cannot even parse
`Mix_Archive_Manager.sh`:

    ./Mix_Archive_Manager.sh: line 777: syntax error near unexpected token `}'

`Mix_Archive_Manager.sh` line 2 upgrades a *run* of the manager to a
Homebrew/usr-local bash 5, but that re-exec happens in the child, not in the
test process, and `source`-based tests need the interpreter itself to be bash 5
before they start.

This module gives every test the same answer:

    from tests.mp_test_bash import BASH

    subprocess.run([BASH, "-c", "source ./Mix_Archive_Manager.sh; ..."])

`MP_TEST_BASH` overrides it outright, which is how a CI job pins a specific
interpreter.
"""

from __future__ import annotations

import os
import shutil
import subprocess

# Known-good locations, most specific first. The manager's own line-2 upgrade
# looks at the same two macOS paths, so a machine that can run the app can also
# run its tests.
_CANDIDATES = (
    "/opt/homebrew/bin/bash",      # Apple Silicon Homebrew
    "/usr/local/bin/bash",         # Intel Homebrew
    "/home/linuxbrew/.linuxbrew/bin/bash",
    "/bin/bash",
    "/usr/bin/bash",
    "/usr/local/bin/bash5",
)


def _major(interp: str) -> int:
    """Return the bash major version, or 0 when it cannot be run."""
    try:
        proc = subprocess.run(
            [interp, "-c", "printf %s \"${BASH_VERSINFO[0]}\""],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    try:
        return int(proc.stdout.decode("ascii", "replace").strip() or 0)
    except ValueError:
        return 0


def find_bash(min_major: int = 4) -> str:
    """Absolute path to a bash of at least `min_major`, or "bash" as a last resort.

    Returning the bare name keeps the old behaviour on a machine where nothing
    qualifies, so a test fails with the same message it always did rather than
    exploding at import time.
    """
    override = os.environ.get("MP_TEST_BASH", "").strip()
    if override:
        return override

    seen = set()
    for cand in _CANDIDATES:
        if cand in seen:
            continue
        seen.add(cand)
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            if _major(cand) >= min_major:
                return cand

    # Fall back to whatever is on PATH, then to the first candidate that at
    # least runs, so the failure is a normal test failure.
    found = shutil.which("bash")
    if found and _major(found) >= min_major:
        return found
    for cand in _CANDIDATES:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return found or "bash"


def bash_major(interp: str | None = None) -> int:
    """Major version of the interpreter the tests will use."""
    return _major(interp or BASH)


#: The interpreter every test that sources the manager should use.
BASH = find_bash()

#: Absolute path to the repository root.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
