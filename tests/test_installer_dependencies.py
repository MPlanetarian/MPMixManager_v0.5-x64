#!/usr/bin/env python3
"""The installer installs what the application imports.

These tests keep three things in agreement, because every real install failure
on macOS, Fedora and Windows came from them drifting apart:

  * requirements.txt        — what a manual `pip install -r` gives you
  * scripts/mp_dependencies.py — what the installer actually installs
  * the source tree          — what the code actually imports

They also cover two defects found while auditing the installer for 0.5.0:
a `cliamp` symlink published without a target (dead link on every install),
and the Python version gate accepting 3.6 even though the code needs 3.8.
"""

from __future__ import annotations

import ast
import json
import re
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.sh"
DEPS = ROOT / "scripts" / "mp_dependencies.py"
REQUIREMENTS = ROOT / "requirements.txt"


def load_deps_module():
    """Import scripts/mp_dependencies.py by path."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("mp_dependencies", DEPS)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def requirements_names() -> list[str]:
    names = []
    for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        line = re.split(r"[<>=!~\[;]", line, maxsplit=1)[0].strip()
        if line:
            names.append(line.lower().replace("_", "-"))
    return names


def imported_third_party_modules() -> set[str]:
    """Every top-level third-party module the shipped code imports."""
    stdlib = set(sys.stdlib_module_names)
    local = {p.stem for p in ROOT.glob("*.py")}
    local |= {p.stem for p in (ROOT / "scripts").glob("*.py")}
    local |= {p.stem for p in (ROOT / "scripts" / "lib").glob("*.py")}
    local |= {p.name for p in (ROOT / "scripts").iterdir() if p.is_dir()}
    local |= {"shared", "lib", "assets", "scripts", "mp_install_dir"}

    found: set[str] = set()
    patterns = [ROOT.glob("*.py"), (ROOT / "scripts").glob("*.py")]
    for group in patterns:
        for path in group:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        found.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    found.add(node.module.split(".")[0])
    return {
        name
        for name in found
        if name and name not in stdlib and name not in local and not name.startswith("_")
    }


class TestRequirementsCoverage(unittest.TestCase):
    def test_requirements_txt_lists_every_required_pip_module(self):
        """A module the installer treats as required must be in requirements.txt.

        PyGObject is the one deliberate exception: it needs system headers and a
        matching girepository, so it can only come from the distro package
        manager. mp_dependencies.py marks it as system-package-only.
        """
        deps = load_deps_module()
        listed = set(requirements_names())
        offenders = []
        for spec in deps.SPECS:
            if spec["kind"] != "module" or not spec.get("required"):
                continue
            pip_name = spec.get("pip", "")
            if not pip_name:
                continue  # PyGObject: system package only, documented as such
            if pip_name.lower().replace("_", "-") not in listed:
                offenders.append(f"{spec['id']} ({pip_name}) is required but not in requirements.txt")
        self.assertEqual(offenders, [])

    def test_every_optional_pip_module_is_listed_or_documented(self):
        """Optional modules are either in requirements.txt or explicitly heavy."""
        deps = load_deps_module()
        listed = set(requirements_names())
        offenders = []
        for spec in deps.SPECS:
            if spec["kind"] != "module" or spec.get("required"):
                continue
            pip_name = spec.get("pip", "")
            if not pip_name:
                continue
            if spec.get("optional_heavy"):
                continue  # ultralytics: commented out, documented as heavy
            if pip_name.lower().replace("_", "-") not in listed:
                offenders.append(f"{spec['id']} ({pip_name}) is optional but absent from requirements.txt")
        self.assertEqual(offenders, [])

    def test_the_declared_requirements_file_is_the_real_one(self):
        deps = load_deps_module()
        self.assertEqual(deps.REQUIREMENTS_FILE.resolve(), REQUIREMENTS.resolve())

    def test_requirements_file_parses_to_the_same_names_as_the_engine(self):
        """The engine's parser and the test's parser must agree."""
        deps = load_deps_module()
        engine = {name.lower().replace("_", "-") for name in deps.requirements_entries(REQUIREMENTS)}
        ours = set(requirements_names())
        self.assertEqual(engine, ours)


class TestImportsAreDeclared(unittest.TestCase):
    def test_every_imported_third_party_module_is_known(self):
        """Nothing the shipped code imports is missing from the dependency map.

        This is what catches "Error: numpy is required" on a fresh machine: a new
        import added to the code without a matching SPECS entry.
        """
        deps = load_deps_module()
        known = set()
        for spec in deps.SPECS:
            if spec["kind"] == "module":
                known.add(spec.get("module", "").lower())
        # Import-name aliases: the code imports PIL, the package is pillow.
        aliases = {
            "pil": "pillow",
            "opengl": "pyopengl",
            "gi": "pygobject",
            "yaml": "pyyaml",
        }
        allowed_undocumented = {
            # Helper scripts that are not part of the four MP windows and are
            # allowed to fail their own way (each prints its own install hint).
            "torch", "cv2", "openai", "psycopg2", "dbus", "cairo",
            "prompt_toolkit", "filling", "shared", "mplanetarian",
            "the", "traktor",
        }
        offenders = []
        for name in sorted(imported_third_party_modules()):
            key = name.lower()
            if key in known or key in allowed_undocumented:
                continue
            if aliases.get(key) in known:
                continue
            offenders.append(name)
        self.assertEqual(
            offenders, [],
            "these imports have no dependency entry, so a fresh install may not "
            f"provide them: {offenders}",
        )


class TestDependencyEngine(unittest.TestCase):
    def test_probe_json_is_valid_and_reports_every_spec(self):
        result = subprocess.run(
            [sys.executable, str(DEPS), "--probe", "--json"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertIn(result.returncode, (0, 1), result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["platform"], load_deps_module().host_os())
        probed = {entry["id"] for entry in data["dependencies"]}
        expected = {
            spec["id"]
            for spec in load_deps_module().SPECS
            if load_deps_module()._applies(spec)
        }
        self.assertEqual(probed, expected)

    def test_verify_exit_status_matches_the_report(self):
        result = subprocess.run(
            [sys.executable, str(DEPS), "--verify", "--json"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        data = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0 if data["ok"] else 1)

    def test_a_probe_does_not_import_heavy_optional_packages(self):
        """Probing must stay fast: it uses find_spec, not import.

        Importing ultralytics pulls in PyTorch and took over 2 seconds, which
        made the installer feel broken on a machine that has it installed.
        """
        source = DEPS.read_text(encoding="utf-8")
        probe_body = source.split("PROBE_CODE =", 1)[1].split('"""', 2)[1]
        self.assertIn("find_spec", probe_body)
        self.assertNotIn("importlib.import_module(name)", probe_body)

    def test_every_binary_spec_has_a_package_for_each_manager_it_claims(self):
        deps = load_deps_module()
        empty = []
        for spec in deps.SPECS:
            if spec["kind"] != "binary" or spec.get("same_package_as"):
                continue
            if not spec.get("pkgs"):
                empty.append(spec["id"])
        self.assertEqual(empty, [], "binary dependencies with no package mapping")

    def test_pygobject_never_uses_pip(self):
        """PyGObject needs headers and a girepository; pip cannot supply them."""
        deps = load_deps_module()
        pygobject = next(s for s in deps.SPECS if s["id"] == "PyGObject")
        self.assertEqual(pygobject.get("pip", ""), "")
        self.assertIn("system", pygobject.get("note", "").lower())

    def test_windows_venv_is_inside_the_install_tree(self):
        """scripts/windows_find_python.cmd checks <install>/venv first."""
        deps = load_deps_module()
        finder = (ROOT / "scripts" / "windows_find_python.cmd").read_text(
            encoding="utf-8", errors="replace"
        )
        self.assertIn(r"venv\Scripts\python.exe", finder)
        if deps.host_os() == "windows":
            self.assertEqual(deps.venv_path().parent, deps.ROOT)


class TestInstallerWiring(unittest.TestCase):
    def setUp(self):
        self.text = INSTALL.read_text(encoding="utf-8")

    def test_installer_drives_the_dependency_engine(self):
        self.assertIn("scripts/mp_dependencies.py", self.text)
        self.assertIn("--install", self.text)
        self.assertNotIn("MISSING_TOOLS=(", self.text, "old duplicated dependency list is back")

    def test_installer_rejects_python_older_than_3_8(self):
        """The code uses 3.8 syntax; a 3.6 gate produced SyntaxError at launch."""
        self.assertIn("sys.version_info >= (3, 8)", self.text)
        self.assertNotIn("sys.version_info >= (3, 6)", self.text)

    def test_installer_never_publishes_a_symlink_without_a_target(self):
        """bin/cliamp is not shipped, so `ln -sf` created a dead link."""
        offenders = []
        for number, line in enumerate(self.text.splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith("ln -sf"):
                continue
            if "link()" in stripped or "MP_LINK" in stripped:
                continue
            offenders.append(f"{number}: {stripped}")
        self.assertEqual(offenders, [], "use the guarded link() helper instead")

    def test_installer_clears_a_stale_cliamp_dead_link(self):
        self.assertIn('"$HOME/.local/bin/cliamp"', self.text)
        self.assertIn("-L", self.text)

    def test_installer_points_the_rest_of_the_run_at_the_venv(self):
        """Shortcuts and the tray must use the interpreter that has the modules."""
        self.assertIn("python-env/bin/python3", self.text)
        self.assertIn('MP_PYTHON="$MP_ENV_PYTHON"', self.text)

    def test_installer_copies_config_for_launchers_outside_the_tree(self):
        self.assertIn(".config/mix-manager/config.env", self.text)

    def test_installer_offers_to_install_python_itself(self):
        self.assertIn("MP_INSTALL_PYTHON", self.text)
        self.assertIn("brew install python3", self.text)
        self.assertIn("Python.Python.3.13", self.text)

    def test_installer_keeps_optional_extras_optional(self):
        self.assertIn("--optional", self.text)
        self.assertIn("MP_INSTALL_DEV_DEPS", self.text)

    def test_installer_supports_a_dry_run(self):
        self.assertIn("--dry-run", self.text)

    def test_installer_still_needs_no_python_for_windows_shortcuts(self):
        """install_windows_user.sh runs before Python is required."""
        windows = self.text.index('"$HOST_OS" = "windows"')
        helper = self.text.index("install_windows_user.sh", windows)
        python_required = self.text.index("python3 is required", helper)
        self.assertLess(helper, python_required)

    def test_installer_shell_syntax_is_valid(self):
        result = subprocess.run(["bash", "-n", str(INSTALL)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(
    os.environ.get("MP_RUN_SLOW_INSTALLER_TESTS") == "1",
    "slow: copies the whole tree and runs install.sh "
    "(set MP_RUN_SLOW_INSTALLER_TESTS=1 to run)",
)
class TestInstallerRunsInATempHome(unittest.TestCase):
    """Run the real installer against a throwaway HOME and check the result.

    The installer is run once for the whole class: a full run creates the Python
    environment and takes minutes, and every assertion below inspects the same
    end state. It also `cp -a`s the entire 157 MB tree, which made a default
    `unittest discover` take over twenty minutes with no progress output.

    It is now opt-in via MP_RUN_SLOW_INSTALLER_TESTS=1 (the CI release job sets
    it); the fast checks in this module still run everywhere.
    """

    home: Path
    tree: Path
    result: subprocess.CompletedProcess

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="mp_install_test_"))
        cls.home = cls.tmp / "home"
        cls.home.mkdir()
        cls.tree = cls.tmp / "tree"
        # A copy keeps the real tree (and its venv) untouched.
        subprocess.run(
            ["cp", "-a", str(ROOT) + "/.", str(cls.tree)],
            check=True,
            capture_output=True,
        )
        env = {
            "HOME": str(cls.home),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "MP_INSTALL_PYTHON": "never",
            "MP_INSTALL_DEV_DEPS": "never",
            "TERM": "dumb",
        }
        cls.result = subprocess.run(
            ["bash", str(cls.tree / "install.sh")],
            capture_output=True,
            text=True,
            env=env,
            timeout=1800,
        )

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls.tmp, ignore_errors=True)

    def assert_install_succeeded(self):
        self.assertEqual(
            self.result.returncode,
            0,
            self.result.stdout[-3000:] + self.result.stderr[-2000:],
        )

    def test_no_symlink_in_the_local_bin_is_broken(self):
        self.assert_install_succeeded()
        bindir = self.home / ".local" / "bin"
        self.assertTrue(bindir.is_dir(), "no launchers were published")
        broken = [
            path.name
            for path in bindir.iterdir()
            if path.is_symlink() and not path.exists()
        ]
        self.assertEqual(broken, [], f"dead symlinks: {broken}")

    def test_manager_sh_is_published_and_runnable(self):
        self.assert_install_succeeded()
        manager = self.home / "manager.sh"
        self.assertTrue(manager.is_symlink() or manager.is_file())
        self.assertTrue(manager.exists(), "manager.sh points at nothing")

    def test_installer_publishes_the_install_dir_record(self):
        self.assert_install_succeeded()
        record = self.home / ".config" / "mix-manager" / "install_dir"
        self.assertTrue(record.is_file())
        self.assertEqual(record.read_text().strip(), str(self.tree))

    def test_desktop_shortcuts_use_the_install_dir_resolver(self):
        """Shortcuts must not hard-code the tree, they resolve it at launch.

        A hard-coded path breaks as soon as the folder is renamed or a new
        version is unpacked beside it, so every launcher goes through
        ~/.local/bin/mp-desktop-exec and scripts/lib/mp_install_dir.sh.
        """
        self.assert_install_succeeded()
        desktop = self.home / "Desktop"
        entries = sorted(desktop.glob("*.desktop")) if desktop.is_dir() else []
        self.assertGreaterEqual(len(entries), 4, f"shortcuts written: {[p.name for p in entries]}")
        for entry in entries:
            text = entry.read_text(encoding="utf-8", errors="replace")
            self.assertNotIn(
                str(self.tree),
                text,
                f"{entry.name} hard-codes the install path instead of resolving it",
            )
            if entry.name.startswith("MP "):
                self.assertIn(
                    "mp-desktop-exec",
                    text,
                    f"{entry.name} does not go through the install-dir resolver",
                )

    def test_the_install_dir_resolver_finds_this_tree(self):
        """The resolver library the installer publishes must find the install."""
        self.assert_install_succeeded()
        library = self.home / ".config" / "mix-manager" / "lib" / "mp_install_dir.sh"
        self.assertTrue(library.is_file(), "resolver library was not published")
        probe = subprocess.run(
            [
                "bash",
                "-c",
                f'. "{library}" && mp_find_install_dir Mix_Archive_Manager.sh',
            ],
            capture_output=True,
            text=True,
            env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"},
        )
        self.assertEqual(probe.stdout.strip(), str(self.tree), probe.stderr)

    def test_config_env_is_usable_by_launchers_outside_the_tree(self):
        self.assert_install_succeeded()
        shared = self.home / ".config" / "mix-manager" / "config.env"
        self.assertTrue(shared.is_file(), "launchers outside the tree find no config.env")


if __name__ == "__main__":
    unittest.main()
