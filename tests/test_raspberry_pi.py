"""Tests for Raspberry Pi support: platform detection, GLSL down-levelling, and
the Pi packaging target in the Development Utility.

The detection module and the GLSL rewriter are pure Python, so those tests run
everywhere. The packaging tests need the BASH_SCRIPTS utility and skip when it
is not present next to the repository.
"""

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import unittest.mock
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS = BASE_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS))

import mp_raspberry_pi as pi  # noqa: E402


class TestPiDetection(unittest.TestCase):
    """mp_raspberry_pi identifies the machine and its limits."""

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("MP_FORCE_RASPBERRY_PI", "MP_LOW_POWER_GPU",
                        "MP_VISUALIZER_GL_LONG_EDGE", "MP_VISUALIZER_GL_MARCH_STEPS",
                        "MP_VISUALIZER_SAFE_PRESET", "MP_DISABLE_BLUETOOTH_MONITOR",
                        "MP_VISUALIZER_FPS_CAP")}
        for key in self._saved:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_detection_off_a_pi(self):
        os.environ["MP_FORCE_RASPBERRY_PI"] = "0"
        self.assertFalse(pi.is_raspberry_pi())
        self.assertEqual(pi.default_env(), {}, "a desktop must get no Pi overrides")
        # The desktop ladder still asks for 3.3 core first.
        self.assertEqual(pi.gl_create_attempts()[0], (3, 3, "core"))

    def test_detection_off_a_pi_with_a_real_gpu(self):
        """A desktop with a working driver is not a low-power GPU.

        `test_detection_off_a_pi` above asserts the *Pi* overrides are absent.
        This one pins the second axis: a machine with a render node keeps the
        plain desktop defaults, while a driverless one (see
        `test_render_node_absence_means_software_rendering`) gets the cap.
        """
        os.environ["MP_FORCE_RASPBERRY_PI"] = "0"
        os.environ["MP_LOW_POWER_GPU"] = "0"
        self.assertFalse(pi.is_low_power_gpu())
        self.assertEqual(pi.default_env(), {})

    def test_detection_forced_on(self):
        os.environ["MP_FORCE_RASPBERRY_PI"] = "1"
        self.assertTrue(pi.is_raspberry_pi())
        self.assertTrue(pi.is_low_power_gpu())
        # On a Pi the first attempt is the 3.1 core context V3D can give.
        self.assertEqual(pi.gl_create_attempts()[0], (3, 1, "core"))

    def test_default_env_sets_render_caps(self):
        os.environ["MP_FORCE_RASPBERRY_PI"] = "1"
        env = pi.default_env()
        self.assertIn("MP_VISUALIZER_GL_LONG_EDGE", env)
        self.assertIn("MP_VISUALIZER_GL_MARCH_STEPS", env)
        self.assertLessEqual(int(env["MP_VISUALIZER_GL_LONG_EDGE"]), 1280)
        self.assertLessEqual(int(env["MP_VISUALIZER_GL_MARCH_STEPS"]), 84)

    def test_default_env_never_overwrites(self):
        os.environ["MP_FORCE_RASPBERRY_PI"] = "1"
        os.environ["MP_VISUALIZER_GL_LONG_EDGE"] = "4000"
        applied = pi.apply_default_env()
        self.assertNotIn("MP_VISUALIZER_GL_LONG_EDGE", applied)
        self.assertEqual(os.environ["MP_VISUALIZER_GL_LONG_EDGE"], "4000")

    def test_glsl_version_mapping(self):
        self.assertEqual(pi.preferred_glsl_version(4, 6), "410")
        self.assertEqual(pi.preferred_glsl_version(3, 3), "330")
        self.assertEqual(pi.preferred_glsl_version(3, 2), "150")
        # The Pi 4/5 V3D ceiling.
        self.assertEqual(pi.preferred_glsl_version(3, 1), "140")
        self.assertEqual(pi.preferred_glsl_version(3, 0), "130")

    def test_thermal_state_parses(self):
        # thermal_state only reads sysfs; it must never raise when absent.
        self.assertIn(pi.thermal_state(), {"ok", "warm", "hot", "unknown"})

    def test_summary_is_a_string(self):
        self.assertIsInstance(pi.summary(), str)

    # -- frame rate cap -----------------------------------------------------
    def test_desktop_has_no_fps_cap(self):
        os.environ["MP_FORCE_RASPBERRY_PI"] = "0"
        os.environ["MP_LOW_POWER_GPU"] = "0"
        self.assertEqual(pi.visualizer_fps_cap(), 0,
                         "a desktop must keep rendering at the display rate")

    def test_pi_is_capped_at_30(self):
        os.environ["MP_FORCE_RASPBERRY_PI"] = "1"
        self.assertEqual(pi.visualizer_fps_cap(), 30)

    def test_fps_cap_env_override(self):
        os.environ["MP_FORCE_RASPBERRY_PI"] = "1"
        os.environ["MP_VISUALIZER_FPS_CAP"] = "24"
        self.assertEqual(pi.visualizer_fps_cap(), 24)
        os.environ["MP_VISUALIZER_FPS_CAP"] = "0"
        self.assertEqual(pi.visualizer_fps_cap(), 0, "0 must disable the cap")
        # The ceiling is 240 so the Settings menu can offer 144 for a 144 Hz
        # panel; a typo above that still clamps instead of freezing the window.
        os.environ["MP_VISUALIZER_FPS_CAP"] = "500"
        self.assertEqual(pi.visualizer_fps_cap(), 240, "a typo must be clamped")
        os.environ["MP_VISUALIZER_FPS_CAP"] = "144"
        self.assertEqual(pi.visualizer_fps_cap(), 144, "144 must survive the clamp")

    def test_default_env_sets_fps_cap(self):
        os.environ["MP_FORCE_RASPBERRY_PI"] = "1"
        self.assertEqual(pi.default_env()["MP_VISUALIZER_FPS_CAP"], "30")

    def test_render_node_absence_caps_the_frame_rate(self):
        """A driverless desktop (no /dev/dri/renderD*) gets the 30 FPS cap.

        A 2010 MacBook Pro on Bazzite has only card0: Mesa cannot bind the
        GeForce 320M and draws with llvmpipe on two CPU cores. That machine
        must be paced at 30 FPS even though it is not a Raspberry Pi.
        """
        os.environ["MP_FORCE_RASPBERRY_PI"] = "0"
        os.environ.pop("MP_LOW_POWER_GPU", None)
        with unittest.mock.patch.object(pi.sys, "platform", "linux"), \
             unittest.mock.patch.object(pi, "has_no_render_node", return_value=True):
            self.assertTrue(pi.should_cap_frame_rate())
            self.assertEqual(pi.visualizer_fps_cap(), 30)

    def test_driverless_desktop_keeps_its_own_resolution(self):
        """Only the frame rate is capped; the raymarch size stays untouched.

        Downscaling the visualizer is a Pi answer. A desktop should keep
        drawing at the resolution its user chose.
        """
        os.environ["MP_FORCE_RASPBERRY_PI"] = "0"
        os.environ.pop("MP_LOW_POWER_GPU", None)
        with unittest.mock.patch.object(pi, "has_no_render_node", return_value=True):
            self.assertFalse(pi.is_low_power_gpu(),
                             "a driverless desktop is not a Pi-sized GPU")
            self.assertEqual(pi.default_env(), {},
                             "no Pi render-resolution overrides on a desktop")

    def test_render_node_present_keeps_the_cap_off(self):
        os.environ["MP_FORCE_RASPBERRY_PI"] = "0"
        os.environ.pop("MP_LOW_POWER_GPU", None)
        with unittest.mock.patch.object(pi, "has_no_render_node", return_value=False):
            self.assertFalse(pi.should_cap_frame_rate())
            self.assertEqual(pi.visualizer_fps_cap(), 0)

    def test_explicit_low_power_override_wins(self):
        os.environ["MP_FORCE_RASPBERRY_PI"] = "0"
        os.environ["MP_LOW_POWER_GPU"] = "1"
        with unittest.mock.patch.object(pi, "has_no_render_node", return_value=False):
            self.assertTrue(pi.should_cap_frame_rate())
        os.environ["MP_LOW_POWER_GPU"] = "0"
        os.environ["MP_FORCE_RASPBERRY_PI"] = "1"
        self.assertFalse(pi.should_cap_frame_rate(),
                         "an explicit 0 must switch the cap off even on a Pi")

    def test_render_node_probe_is_linux_only(self):
        for other in ("darwin", "win32"):
            with unittest.mock.patch.object(pi.sys, "platform", other):
                self.assertFalse(pi.has_no_render_node(),
                                 f"{other} must never be classified this way")

    def test_limits_report_the_cap(self):
        os.environ["MP_FORCE_RASPBERRY_PI"] = "1"
        self.assertEqual(pi.visualizer_limits()["fps_cap"], 30)

    def test_swap_interval_matches_the_cap(self):
        # An exact multiple of the cap can block in the driver.
        self.assertEqual(pi.swap_interval_for_cap(60, 30), 2)
        self.assertEqual(pi.swap_interval_for_cap(120, 30), 4)
        # Already at or below the cap: no extra blocking.
        self.assertEqual(pi.swap_interval_for_cap(30, 30), 1)
        self.assertEqual(pi.swap_interval_for_cap(24, 30), 1)
        # A non-multiple refresh keeps interval 1 and lets the loop pace it.
        self.assertEqual(pi.swap_interval_for_cap(75, 30), 1)
        self.assertEqual(pi.swap_interval_for_cap(144, 30), 1)
        # No cap, or unknown refresh.
        self.assertEqual(pi.swap_interval_for_cap(60, 0), 1)
        self.assertEqual(pi.swap_interval_for_cap(0, 30), 1)


class TestVisualizerPacing(unittest.TestCase):
    """monitor_refresh_interval is the visualizer's pacing authority."""

    class _Mode:
        class contents:
            refreshRate = 60

    class _GLFW:
        def glfwGetPrimaryMonitor(self):
            return object()

        def glfwGetVideoMode(self, _mon):
            return TestVisualizerPacing._Mode()

    class _Renderer:
        _glfw = None

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in
                       ("MP_FORCE_RASPBERRY_PI", "MP_VISUALIZER_FPS_CAP")}
        for key in self._saved:
            os.environ.pop(key, None)
        # Import here so the module sees the environment above.
        import mp_liquid_visualizer_vulkan as vis
        self.vis = vis
        renderer = self._Renderer()
        renderer._glfw = self._GLFW()
        self.renderer = renderer

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _fps(self):
        interval = self.vis.monitor_refresh_interval(self.renderer)
        return 1.0 / interval

    def test_desktop_follows_the_display(self):
        os.environ["MP_FORCE_RASPBERRY_PI"] = "0"
        os.environ["MP_LOW_POWER_GPU"] = "0"
        self.assertAlmostEqual(self._fps(), 60.0, places=1)

    def test_driverless_desktop_is_capped_to_30(self):
        """No render node means llvmpipe, so the loop must target 30 FPS."""
        os.environ["MP_FORCE_RASPBERRY_PI"] = "0"
        os.environ.pop("MP_LOW_POWER_GPU", None)
        with unittest.mock.patch.object(pi, "has_no_render_node", return_value=True):
            self.assertAlmostEqual(self._fps(), 30.0, places=1)

    def test_pi_targets_30_fps(self):
        os.environ["MP_FORCE_RASPBERRY_PI"] = "1"
        self.assertAlmostEqual(self._fps(), 30.0, places=1)

    def test_cap_can_be_disabled(self):
        os.environ["MP_FORCE_RASPBERRY_PI"] = "1"
        os.environ["MP_VISUALIZER_FPS_CAP"] = "0"
        self.assertAlmostEqual(self._fps(), 60.0, places=1)


class TestGlslDownlevelling(unittest.TestCase):
    """The GLSL rewriter turns 3.30 source into something a 3.1 driver takes."""

    VERTEX = (BASE_DIR / "shaders" / "liquid_glass_gl.vert").read_text(encoding="utf-8")
    FRAGMENT = (BASE_DIR / "shaders" / "liquid_glass_gl.frag").read_text(encoding="utf-8")

    def test_version_directive_is_replaced(self):
        out = pi.adapt_glsl(self.VERTEX, "140")
        self.assertTrue(out.lstrip().startswith("#version 140"))
        self.assertNotIn("330", out.splitlines()[0])

    def test_layout_qualifiers_are_removed(self):
        out = pi.adapt_glsl(self.VERTEX, "140")
        self.assertNotIn("layout", out)
        self.assertIn("in vec2 aPos;", out)
        self.assertIn("in vec2 aUv;", out)

    def test_attribute_locations_are_reported(self):
        locations = dict(pi.glsl_attribute_locations(self.VERTEX))
        self.assertEqual(locations, {"aPos": 0, "aUv": 1})

    def test_modern_version_is_untouched(self):
        self.assertEqual(pi.adapt_glsl(self.VERTEX, "330"), self.VERTEX)

    def test_fragment_shader_survives(self):
        out = pi.adapt_glsl(self.FRAGMENT, "140")
        self.assertTrue(out.lstrip().startswith("#version 140"))
        # The language features the shader uses (in/out, texture(), structs,
        # constant loop bounds) are all valid at 1.40 and must be preserved.
        self.assertIn("texture(", out)
        self.assertIn("out vec4 outColor;", out)


class TestPiLauncherHelpers(unittest.TestCase):
    """scripts/lib/mp_pi_detect.sh agrees with the Python module."""

    SHELL = SCRIPTS / "lib" / "mp_pi_detect.sh"

    def run_shell(self, snippet, **env):
        base = dict(os.environ)
        for key in ("MP_FORCE_RASPBERRY_PI", "MP_LOW_POWER_GPU"):
            base.pop(key, None)
        base.update(env)
        proc = subprocess.run(
            ["bash", "-c", f'source "{self.SHELL}"; {snippet}'],
            capture_output=True, text=True, env=base, timeout=60,
        )
        return proc

    def test_shell_syntax(self):
        proc = subprocess.run(["bash", "-n", str(self.SHELL)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_not_a_pi(self):
        proc = self.run_shell("mp_is_raspberry_pi && echo yes || echo no",
                              MP_FORCE_RASPBERRY_PI="0")
        self.assertEqual(proc.stdout.strip(), "no")

    def test_vulkan_is_corrected_on_a_pi(self):
        proc = self.run_shell("mp_renderer_backend vulkan", MP_FORCE_RASPBERRY_PI="1")
        self.assertEqual(proc.stdout.strip(), "opengl",
                         "a Pi must never launch the NVIDIA-only Vulkan host")

    def test_vulkan_is_kept_on_a_desktop(self):
        proc = self.run_shell("mp_renderer_backend vulkan", MP_FORCE_RASPBERRY_PI="0")
        self.assertEqual(proc.stdout.strip(), "vulkan")

    def test_defaults_are_exported(self):
        proc = self.run_shell(
            "mp_pi_apply_defaults; echo $MP_VISUALIZER_GL_LONG_EDGE $MP_VISUALIZER_GL_MARCH_STEPS",
            MP_FORCE_RASPBERRY_PI="1",
        )
        parts = proc.stdout.split()
        self.assertEqual(len(parts), 2)
        self.assertLessEqual(int(parts[0]), 1280)
        self.assertLessEqual(int(parts[1]), 84)


BASH_SCRIPTS = Path(os.environ.get("MP_BASH_SCRIPTS", str(BASE_DIR.parent / "Documents" / "BASH_SCRIPTS")))
UTILITY = BASH_SCRIPTS / "MP_Dev_Utility.sh"
BASH = shutil.which("bash") or "bash"


@unittest.skipUnless(UTILITY.is_file(), f"packaging utility not found at {UTILITY}")
class TestRaspberryPiPackaging(unittest.TestCase):
    """The Raspberry Pi target produces the two artifacts users expect."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="mp_pi_pkg_test_"))
        cls.src = cls.tmp / "src"
        (cls.src / "scripts").mkdir(parents=True)
        (cls.src / "assets").mkdir()
        (cls.src / "VERSION").write_text("0.5.3 Stable (Sensorium)\n")
        (cls.src / "Mix_Archive_Manager.sh").write_text("#!/usr/bin/env bash\necho manager\n")
        (cls.src / "manager.sh").write_text("#!/usr/bin/env bash\necho manager\n")
        (cls.src / "MP_Gui.sh").write_text("#!/usr/bin/env bash\necho gui\n")
        (cls.src / "LICENSE").write_text("MIT\n")
        (cls.src / "config.env").write_text("SECRET_TOKEN=do-not-ship\n")
        (cls.src / "assets" / "mp_liquid_glass_icon.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        (cls.src / "assets" / "mp_liquid_glass_icon.svg").write_text("<svg/>")
        # Host-only noise that a Pi bundle must not carry.
        (cls.src / "manager.exe").write_bytes(b"MZ" + b"0" * 32)
        (cls.src / "launch.bat").write_text("@echo off\n")
        (cls.src / "Exit_MP_Video.mp4").write_bytes(b"0" * 1024)
        # Credentials and archive noise.
        (cls.src / "MIX_ARCHIVE").mkdir()
        (cls.src / "MIX_ARCHIVE" / "big.flac").write_bytes(b"x" * 64)
        (cls.src / "logs").mkdir()
        (cls.src / "logs" / "run.log").write_text("noise\n")
        cls.out = cls.tmp / "out"
        cls.proc = subprocess.run(
            [BASH, str(UTILITY), "--source", str(cls.src), "--output", str(cls.out),
             "--targets", "raspberrypi", "--yes"],
            capture_output=True, text=True, timeout=1800,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_build_succeeded(self):
        self.assertEqual(self.proc.returncode, 0,
                         f"STDOUT:\n{self.proc.stdout}\nSTDERR:\n{self.proc.stderr}")

    def test_both_artifacts_exist(self):
        bundle = self.out / "MP_Mix_Manager_0.5.3_RaspberryPi"
        self.assertTrue((bundle / "mp-mix-manager_0.5.3_arm64_raspberrypi.deb").is_file())
        self.assertTrue((bundle / "MP_Mix_Manager_0.5.3_raspberrypi_arm64.tar.gz").is_file())
        self.assertTrue((bundle / "README_RaspberryPi.txt").is_file())

    def test_deb_declares_pi_dependencies(self):
        bundle = self.out / "MP_Mix_Manager_0.5.3_RaspberryPi"
        deb = bundle / "mp-mix-manager_0.5.3_arm64_raspberrypi.deb"
        work = self.tmp / "deb_extract"
        work.mkdir(exist_ok=True)
        subprocess.run(["ar", "x", str(deb)], cwd=work, check=True)
        control = subprocess.run(
            ["tar", "-xJf", "control.tar.xz", "-O", "./control"],
            cwd=work, capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("Architecture: arm64", control)
        # The whole point of the Pi target: the GPU stack comes with the app.
        self.assertIn("libglfw3", control)
        self.assertIn("libgl1-mesa-dri", control)
        self.assertIn("libglx-mesa0", control)

    def test_tarball_is_slim_and_installable(self):
        bundle = self.out / "MP_Mix_Manager_0.5.3_RaspberryPi"
        tarball = bundle / "MP_Mix_Manager_0.5.3_raspberrypi_arm64.tar.gz"
        extract = self.tmp / "pi_extract"
        extract.mkdir(exist_ok=True)
        with tarfile.open(tarball) as tf:
            names = tf.getnames()
            tf.extractall(extract)
        root = extract / "mp-mix-manager-0.5.3-raspberrypi"
        self.assertTrue((root / "install-raspberry-pi.sh").is_file())
        self.assertTrue((root / "uninstall-raspberry-pi.sh").is_file())
        self.assertTrue((root / "pi-defaults.env").is_file())
        self.assertTrue((root / "ARCHITECTURE.txt").is_file())
        self.assertIn("arm64", (root / "ARCHITECTURE.txt").read_text())

        # Host-only files must not reach a Pi SD card.
        self.assertFalse(any(n.endswith(".exe") for n in names), "no Windows binaries")
        self.assertFalse(any(n.endswith(".bat") for n in names), "no Windows launchers")
        self.assertFalse(any(n.endswith("Exit_MP_Video.mp4") for n in names),
                         "the 21 MB exit movie is not needed on a Pi")
        self.assertFalse(any(n.endswith("/config.env") for n in names), "no credentials")
        self.assertFalse(any("MIX_ARCHIVE" in n for n in names))

        for script in ("install-raspberry-pi.sh", "uninstall-raspberry-pi.sh"):
            proc = subprocess.run(["bash", "-n", str(root / script)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, f"{script}: {proc.stderr}")

    def test_pi_defaults_pick_opengl(self):
        bundle = self.out / "MP_Mix_Manager_0.5.3_RaspberryPi"
        tarball = bundle / "MP_Mix_Manager_0.5.3_raspberrypi_arm64.tar.gz"
        extract = self.tmp / "pi_defaults"
        extract.mkdir(exist_ok=True)
        with tarfile.open(tarball) as tf:
            tf.extractall(extract)
        text = (extract / "mp-mix-manager-0.5.3-raspberrypi" / "pi-defaults.env").read_text()
        self.assertIn('LIQUID_GLASS_BACKEND="opengl"', text)

    def test_manifest_records_the_bundle(self):
        manifest = (self.out / "BUILD-MANIFEST.txt").read_text()
        self.assertIn("RaspberryPi", manifest)
        self.assertIn("arm64", manifest)

    def test_help_mentions_raspberrypi(self):
        proc = subprocess.run([BASH, str(UTILITY), "--help"], capture_output=True, text=True)
        self.assertIn("raspberrypi", proc.stdout)
        self.assertIn("Raspberry Pi", proc.stdout)


if __name__ == "__main__":
    unittest.main()
