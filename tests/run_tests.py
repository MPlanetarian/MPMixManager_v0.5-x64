#!/usr/bin/env python3
"""
MP Mix Manager - test entry point.

Until 0.5.1 `tests/run_tests.sh` was not a test runner. It ran `bash -n`,
`py_compile`, one `grep` on config.env, `cmp` on duplicate files and a `chmod`
check -- and never imported the application or invoked a single `test_*.py`
module. The README advertised "243+ automated checks"; none of the 1,488 real
test functions were among them.

This script runs the actual suite and fails on a real failure. It also removes
the two reasons the suite could not be run by a contributor or in CI:

  * **Daemon leakage.** Constructing an AudioEngine started a Bluetooth
    monitor thread and a `pactl subscribe` child that were never stopped, so a
    full run left processes behind and eventually blocked on `wpctl`.
    `MP_DISABLE_BLUETOOTH_MONITOR=1` is exported here, and the engine now has
    an explicit `shutdown()`.
  * **Slow, order-dependent modules.** A handful of tests copy the tree or
    scan the real archive. They are opt-in behind environment flags, listed in
    `SLOW_MODULES` below.

Usage:
    python3 tests/run_tests.py              # fast suite
    python3 tests/run_tests.py -v           # verbose
    python3 tests/run_tests.py --pattern test_web_companion
    python3 tests/run_tests.py --full       # include the slow/opt-in tests
    python3 tests/run_tests.py --list       # list discovered modules
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent

# Modules that take minutes or touch the real machine. They run only with
# --full, and each is also individually gated by an environment flag so an
# explicit `python3 -m unittest tests.<name>` still honours the decision.
SLOW_MODULES = {
    "test_installer_dependencies",   # copies 157 MB and runs install.sh
}

SLOW_ENV = {
    "MP_RUN_SLOW_INSTALLER_TESTS": "1",
}


def configure_environment(full: bool) -> None:
    """Make the suite hermetic before any test module is imported."""
    # Never let a test start the Bluetooth monitor or a `pactl subscribe` child.
    os.environ.setdefault("MP_DISABLE_BLUETOOTH_MONITOR", "1")
    # Keep the player from writing into the developer's real config directory.
    os.environ.setdefault("MP_TEST_MODE", "1")
    # Quiet the sounddevice/numpy warnings that otherwise drown the result line.
    os.environ.setdefault("PYTHONWARNINGS", "ignore::DeprecationWarning")
    if full:
        for key, value in SLOW_ENV.items():
            os.environ[key] = value
    else:
        for key in SLOW_ENV:
            os.environ.pop(key, None)

    # The application imports its siblings directly, not as a package.
    for path in (str(ROOT), str(ROOT / "scripts")):
        if path not in sys.path:
            sys.path.insert(0, path)


def discover(pattern: str | None = None) -> unittest.TestSuite:
    loader = unittest.TestLoader()
    if pattern:
        return loader.discover(str(TESTS_DIR), pattern=f"{pattern}*.py",
                               top_level_dir=str(ROOT))
    return loader.discover(str(TESTS_DIR), top_level_dir=str(ROOT))


def filter_slow(suite: unittest.TestSuite, full: bool) -> unittest.TestSuite:
    """Drop the opt-in modules unless --full was passed.

    Discovery imports every module, and an import has to succeed for the module
    to be skipped cleanly, so filtering happens on the assembled suite.
    """
    if full:
        return suite
    kept = unittest.TestSuite()

    def walk(node):
        if isinstance(node, unittest.TestSuite):
            for child in node:
                walk(child)
            return
        module = getattr(node, "__class__", type(node)).__module__
        name = module.rsplit(".", 1)[-1]
        if name in SLOW_MODULES:
            return
        kept.addTest(node)

    walk(suite)
    return kept


def count_tests(suite) -> int:
    total = 0
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            total += count_tests(item)
        else:
            total += 1
    return total


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the MP Mix Manager test suite")
    parser.add_argument("pattern", nargs="?", help="only modules matching this prefix")
    parser.add_argument("-p", "--pattern", dest="pattern_opt", help=argparse.SUPPRESS)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--full", action="store_true",
                        help="include the slow, machine-touching modules")
    parser.add_argument("--list", action="store_true", help="list discovered modules")
    parser.add_argument("--failfast", action="store_true")
    args = parser.parse_args(argv)

    pattern = args.pattern_opt or args.pattern
    configure_environment(args.full)

    if args.list:
        for path in sorted(TESTS_DIR.glob(f"{pattern or 'test_'}*.py")):
            marker = " (slow, --full)" if path.stem in SLOW_MODULES else ""
            print(f"{path.stem}{marker}")
        return 0

    modules = select_modules(pattern, args.full)
    if not modules:
        print("No test modules matched.", file=sys.stderr)
        return 1

    if not args.full:
        print("(slow modules skipped; pass --full to include them)")
    print(f"Running {len(modules)} test modules, each in its own process.")
    print()

    started = time.time()
    run, failed, errored, skipped = 0, [], [], 0
    for name in modules:
        code, summary = _run_module(name, args.verbose, args.failfast)
        run += summary["run"]
        skipped += summary["skipped"]
        if code == 0:
            if args.verbose:
                print(f"  ok    {name} ({summary['run']} tests)", flush=True)
        elif code == 124:
            failed.append(f"{name} (timed out)")
            print(f"  TIMEOUT {name}", flush=True)
        else:
            failed.extend(summary["failures"])
            errored.extend(summary["errors"])
            print(f"  FAIL  {name}", flush=True)

    elapsed = time.time() - started
    print()
    print("=" * 70)
    print(f"  {run} run, {len(failed)} failed, {len(errored)} errored, "
          f"{skipped} skipped in {elapsed:.1f}s")
    print("=" * 70)

    if failed or errored:
        print("\nFailed tests:")
        for item in failed:
            print(f"  FAIL  {item}")
        for item in errored:
            print(f"  ERROR {item}")
        return 1
    print("\nOK")
    return 0


def select_modules(pattern: str | None, full: bool) -> list[str]:
    """Every test module on disk, slow ones included only with --full."""
    prefix = pattern or "test_"
    names = []
    for path in sorted(TESTS_DIR.glob(f"{prefix}*.py")):
        stem = path.stem
        if stem in SLOW_MODULES and not full:
            continue
        names.append(stem)
    return names


SUMMARY_RE = None


def _run_module(name: str, verbose: bool, failfast: bool):
    """Run one module in a child process and parse its result.

    A child process per module is not paranoia: several modules import GTK,
    NumPy and a Vulkan ctypes binding, and tearing those down in one long-lived
    interpreter segfaults inside libc after a few hundred tests. Isolation also
    means one module's leaked daemon cannot slow the next one down.
    """
    import json
    import re
    import subprocess

    env = dict(os.environ)
    env.setdefault("MP_DISABLE_BLUETOOTH_MONITOR", "1")
    cmd = [sys.executable, "-m", "unittest", f"tests.{name}"]
    if failfast:
        cmd.insert(-1, "-f")
    try:
        proc = subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True,
                              text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return 124, {"run": 0, "skipped": 0, "failures": [], "errors": []}
    output = proc.stdout + proc.stderr
    if verbose:
        print(output)
    run = skipped = 0
    m = re.search(r"^Ran (\d+) tests?", output, re.M)
    if m:
        run = int(m.group(1))
    m = re.search(r"skipped=(\d+)", output)
    if m:
        skipped = int(m.group(1))
    failures = re.findall(r"^(?:FAIL|ERROR): (\S+)", output, re.M)
    errors = re.findall(r"^ERROR: (\S+)", output, re.M)
    return proc.returncode, {
        "run": run, "skipped": skipped,
        "failures": [f"{name}.{f}" for f in failures if f not in errors],
        "errors": [f"{name}.{e}" for e in errors],
    }


def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


if __name__ == "__main__":
    sys.exit(main())
