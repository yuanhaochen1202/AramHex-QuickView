"""Failure handling must work without importing the broken application or using its config."""
import builtins
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("aramhex_launcher_config", ROOT / "packaging/launcher_config.py")
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class ProductionLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aramhex-launcher-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.program = self.root / "portable"
        self.program.mkdir()
        (self.program / "portable.txt").write_text("portable", encoding="utf-8")
        self.home = self.root / "isolated-home"
        self.env = patch.dict("os.environ", {"ARAMHEX_HOME": str(self.home), "APPDATA": str(self.root / "appdata")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.executable = patch.object(sys, "executable", str(self.program / "AramHex.exe"))
        self.executable.start()
        self.addCleanup(self.executable.stop)

    def launcher(self, channel="user", recovery_url=None):
        module = ModuleType("tested_production_launcher")
        exec(compile(config.render_launcher(ROOT, channel, recovery_url), "launcher.py", "exec"), module.__dict__)
        return module

    def prompt(self, launcher, answer):
        prompts, urls = [], []
        def message_box(*args):
            prompts.append(args)
            return answer
        mocks = {"ctypes": SimpleNamespace(windll=SimpleNamespace(user32=SimpleNamespace(MessageBoxW=message_box))),
                 "webbrowser": SimpleNamespace(open=lambda url: urls.append(url))}
        with patch.dict(sys.modules, mocks):
            launcher._crash("ModuleNotFoundError: No module named 'uuid'")
        return prompts, urls

    def test_user_yes_opens_only_maintainer_cos_package(self):
        prompts, urls = self.prompt(self.launcher(), 6)
        self.assertEqual(urls, [config.USER_RECOVERY_URL])
        self.assertIn("/downloads/AramHex-Portable-", urls[0])
        self.assertTrue(urls[0].endswith("-user.zip"))
        self.assertNotIn("1.1.5.4", urls[0])
        self.assertIn("用户版", prompts[0][1])
        self.assertIn(str(self.home / "crash.log"), prompts[0][1])
        self.assertEqual(prompts[0][3] & 0x100, 0x100)  # No is the default.

    def test_user_no_or_closed_never_opens_a_page(self):
        for answer in (7, 0):
            with self.subTest(answer=answer):
                prompts, urls = self.prompt(self.launcher(), answer)
                self.assertEqual(urls, [])
                self.assertEqual(len(prompts), 1)

    def test_maintainer_failure_has_no_download_action_even_for_yes_result(self):
        prompts, urls = self.prompt(self.launcher("maintainer"), 6)
        self.assertEqual(urls, [])
        self.assertIn("维护聊天", prompts[0][1])
        self.assertEqual(prompts[0][3], 0x10)  # Error icon, OK button only.

    def test_user_without_recovery_package_only_reports_failure(self):
        prompts, urls = self.prompt(self.launcher("user", ""), 6)
        self.assertEqual(urls, [])
        self.assertNotIn("点「是」", prompts[0][1])
        self.assertEqual(prompts[0][3], 0x10)

    def test_explicit_home_wins_over_portable_and_preserves_old_errors(self):
        launcher = self.launcher()
        path = Path(launcher._crash_log("first failure"))
        launcher._crash_log("second failure")
        self.assertEqual(path, self.home / "crash.log")
        self.assertFalse((self.program / "userdata").exists())
        self.assertIn("first failure", path.read_text(encoding="utf-8"))
        self.assertIn("second failure", path.read_text(encoding="utf-8"))

    def test_portable_home_used_without_explicit_override(self):
        with patch.dict("os.environ", {"ARAMHEX_HOME": ""}):
            self.assertEqual(Path(self.launcher()._crash_log("failure")), self.program / "userdata/crash.log")

    def test_installed_home_used_without_override_or_portable_marker(self):
        (self.program / "portable.txt").unlink()
        with patch.dict("os.environ", {"ARAMHEX_HOME": ""}):
            self.assertEqual(Path(self.launcher()._crash_log("failure")), self.root / "appdata/AramHex/crash.log")

    def test_unwritable_log_still_shows_error_without_claiming_saved_path(self):
        launcher = self.launcher()
        with patch.object(launcher.os, "makedirs", side_effect=OSError("write denied")):
            prompts, urls = self.prompt(launcher, 7)
        self.assertEqual(urls, [])
        self.assertNotIn("错误记录：", prompts[0][1])

    def test_fallback_does_not_import_application_or_network_client(self):
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.startswith(("lolhex", "requests", "urllib3")):
                raise AssertionError("Failure handling must not import application/network code")
            return original(name, *args, **kwargs)
        with patch.object(builtins, "__import__", guarded):
            prompts, urls = self.prompt(self.launcher(), 7)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(urls, [])

    def test_entry_point_logs_import_error_and_exits_one(self):
        prompts, urls = [], []
        module = ModuleType("lolhex.__main__")
        def broken_main():
            raise ModuleNotFoundError("No module named 'uuid'")
        module.main = broken_main
        mocks = {"lolhex.__main__": module,
                 "ctypes": SimpleNamespace(windll=SimpleNamespace(user32=SimpleNamespace(
                     MessageBoxW=lambda *args: prompts.append(args) or 7))),
                 "webbrowser": SimpleNamespace(open=lambda url: urls.append(url))}
        with patch.dict(sys.modules, mocks), self.assertRaises(SystemExit) as raised:
            exec(compile(config.render_launcher(ROOT, "user"), "launcher.py", "exec"), {"__name__": "__main__"})
        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(urls, [])
        self.assertIn("No module named 'uuid'", (self.home / "crash.log").read_text(encoding="utf-8"))

    def test_build_renderer_can_pin_a_new_reviewed_baseline(self):
        url = f"https://{config.RECOVERY_HOST}/downloads/AramHex-Portable-1.1.5.9-user.zip"
        self.assertNotEqual(url, config.USER_RECOVERY_URL)
        self.assertEqual(self.launcher("user", url).USER_RECOVERY_URL, url)
        self.assertNotIn("https://aramhex.com/#download", config.render_launcher(ROOT, "user"))

    def test_default_recovery_tracks_source_version_without_executing_application(self):
        source = self.root / "versioned-source"
        (source / "lolhex").mkdir(parents=True)
        (source / "packaging").mkdir()
        (source / "packaging/launcher.py").write_text(
            (ROOT / "packaging/launcher.py").read_text(encoding="utf-8-sig"), encoding="utf-8")
        for version in ("1.1.6", "1.1.6.1", "1.1.7"):
            with self.subTest(version=version):
                (source / "lolhex/__init__.py").write_text(
                    "raise AssertionError('application source must not execute')\n"
                    f"__version__ = {version!r}\n", encoding="utf-8")
                url = f"https://{config.RECOVERY_HOST}/downloads/AramHex-Portable-{version}-user.zip"
                self.assertEqual(config.recovery_url_for_source(source), url)
                rendered = config.render_launcher(source, "user")
                self.assertIn(f"USER_RECOVERY_URL = {url!r}\n", rendered)
        self.assertEqual(config.USER_RECOVERY_URL, config.recovery_url_for_source(ROOT))

    def test_source_version_must_be_unique_literal_and_valid(self):
        source = self.root / "invalid-version-source"
        (source / "lolhex").mkdir(parents=True)
        for body in ("# __version__ = '1.1.6'\n", "__version__ = str('1.1.6')\n",
                     "__version__ = '1.1.6'\n__version__ = '1.1.7'\n",
                     "__version__ = '1.1'\n", "__version__ = '1.1.6-test.1'\n"):
            with self.subTest(body=body):
                (source / "lolhex/__init__.py").write_text(body, encoding="utf-8")
                with self.assertRaises(ValueError):
                    config.recovery_url_for_source(source)

    def test_explicit_recovery_override_does_not_require_application_source(self):
        source = self.root / "explicit-source"
        (source / "packaging").mkdir(parents=True)
        (source / "packaging/launcher.py").write_text(
            (ROOT / "packaging/launcher.py").read_text(encoding="utf-8-sig"), encoding="utf-8")
        url = f"https://{config.RECOVERY_HOST}/downloads/AramHex-Portable-1.1.6-user.zip"
        self.assertIn(f"USER_RECOVERY_URL = {url!r}\n", config.render_launcher(source, "user", url))

    def test_recovery_accepts_upstream_baselines_and_custom_revisions(self):
        for version in ("1.1.6", "1.1.6.1", "1.1.7"):
            with self.subTest(version=version):
                url = f"https://{config.RECOVERY_HOST}/downloads/AramHex-Portable-{version}-user.zip"
                launcher = self.launcher("user", url)
                self.assertEqual(launcher.USER_RECOVERY_URL, url)
                _, urls = self.prompt(launcher, 6)
                self.assertEqual(urls, [url])

    def test_recovery_rejects_versions_outside_the_release_protocol(self):
        for version in ("1.6", "1.1.6.1.1", "v1.1.6", "1.1.6-beta", "1.100000.6", "１.１.６"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                config.render_launcher(ROOT, "user",
                    f"https://{config.RECOVERY_HOST}/downloads/AramHex-Portable-{version}-user.zip")

    def test_build_renderer_rejects_non_user_or_untrusted_recovery_targets(self):
        for url in ("https://aramhex.com/#download", config.USER_RECOVERY_URL.replace("https:", "http:"),
                    config.USER_RECOVERY_URL + "?token=example", config.USER_RECOVERY_URL + "#fragment",
                    config.USER_RECOVERY_URL.replace("-user.zip", "-maintainer.zip")):
            with self.subTest(url=url), self.assertRaises(ValueError):
                config.render_launcher(ROOT, "user", url)
        with self.assertRaises(ValueError):
            config.render_launcher(ROOT, "maintainer", config.USER_RECOVERY_URL)
        with self.assertRaises(ValueError):
            config.render_launcher(ROOT, "friends")

    def test_build_renderer_rejects_missing_or_duplicate_constants(self):
        source = self.root / "fake-source/packaging"
        source.mkdir(parents=True)
        for body in ("BUILD_CHANNEL = 'maintainer'\n", "BUILD_CHANNEL = 'maintainer'\nBUILD_CHANNEL = 'user'\nUSER_RECOVERY_URL = ''\n"):
            (source / "launcher.py").write_text(body, encoding="utf-8")
            with self.assertRaises(ValueError):
                config.render_launcher(source.parent, "user", "")


if __name__ == "__main__":
    unittest.main()
