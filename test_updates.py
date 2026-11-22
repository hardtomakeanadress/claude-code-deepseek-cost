"""Regressions for balance refresh triggers and interactive package updates."""
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

import install
import statusline
import updater


def load_costs():
    spec = importlib.util.spec_from_file_location("costs", "deepseek-cost.py")
    costs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(costs)
    return costs


class BalanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.costs = load_costs()
        self.cache = str(Path(self.tmp.name) / "session.json")
        self.costs.BALANCE_FILE = str(Path(self.tmp.name) / "balance.json")

    def test_every_spend_change_triggers_even_below_display_precision(self):
        with patch.object(self.costs.subprocess, "Popen") as spawn, patch.object(
                self.costs, "fetch_balance", side_effect=AssertionError("network in redraw")):
            self.costs.request_balance_refresh(self.cache, 2.6401)
            self.costs.request_balance_refresh(self.cache, 2.6401)
            self.assertEqual(spawn.call_count, 1)
            self.costs.request_balance_refresh(self.cache, 2.6402)
            self.assertEqual(spawn.call_count, 2)
            self.assertIn("--refresh-balance", spawn.call_args.args[0])

    def test_unchanged_spend_retries_after_a_minute(self):
        with patch.object(self.costs.subprocess, "Popen") as spawn, patch.object(
                self.costs.time, "time", side_effect=[1000, 1059, 1060]):
            for _ in range(3):
                self.costs.request_balance_refresh(self.cache, 2.64)
            self.assertEqual(spawn.call_count, 2)

    def test_spawn_failure_does_not_suppress_next_attempt(self):
        with patch.object(self.costs.subprocess, "Popen", side_effect=OSError):
            self.costs.request_balance_refresh(self.cache, 2.64)
        with patch.object(self.costs.subprocess, "Popen") as spawn:
            self.costs.request_balance_refresh(self.cache, 2.64)
            spawn.assert_called_once()

    def test_simultaneous_redraw_skips_locked_trigger(self):
        with patch.object(self.costs, "_lock_acquire", side_effect=BlockingIOError), patch.object(
                self.costs.subprocess, "Popen") as spawn:
            self.costs.request_balance_refresh(self.cache, 2.64)
            spawn.assert_not_called()

    def test_worker_refreshes_cache_and_failure_preserves_it(self):
        with patch("sys.argv", ["deepseek-cost.py", "--refresh-balance"]):
            with patch.object(self.costs, "fetch_balance", return_value=8.35):
                self.costs.main()
            self.assertEqual(self.costs.balance_text(), "$8.35 left")
            with patch.object(self.costs, "fetch_balance", return_value=None):
                self.costs.main()
            self.assertEqual(self.costs.balance_text(), "$8.35 left")

    def test_stale_and_malformed_cache(self):
        path = Path(self.costs.BALANCE_FILE)
        path.write_text(json.dumps({"usd": 11.45, "fetched_at": (
            datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()}))
        self.assertEqual(self.costs.balance_text(), "$11.45 left (stale)")
        path.write_text("[]")
        self.assertEqual(self.costs.balance_text(), "")

    def test_statusline_schedules_refresh_with_unrounded_cost(self):
        transcript = Path(self.tmp.name) / "transcript.jsonl"
        transcript.touch()
        with patch.object(self.costs, "session_cost", return_value=2.6401), patch.object(
                self.costs, "request_balance_refresh") as refresh:
            self.assertEqual(statusline.money_part(
                {"transcript_path": str(transcript), "session_id": "regression"}, self.costs), " $2.64")
            self.assertEqual(refresh.call_args.args[1], 2.6401)


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.costs = load_costs()

    def test_noninteractive_runs_do_not_check_or_prompt(self):
        with patch("sys.stdin.isatty", return_value=False), patch.object(
                updater, "load_costs") as load:
            updater.main()
            load.assert_not_called()

    def test_single_key_one_updates_and_enter_skips(self):
        for key in ("1", "\n"):
            with self.subTest(key=key), patch("sys.argv", ["updater.py"]), patch(
                    "sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), patch.object(
                    updater, "load_costs", return_value=self.costs), patch.object(
                    updater, "latest_version", return_value="99.0"), patch.object(
                    updater, "read_choice", return_value=key), patch.object(
                    updater, "update_package", return_value="99.0") as update, patch("builtins.print"):
                updater.main()
                self.assertEqual(update.call_count, int(key == "1"))

    def test_current_version_does_not_prompt(self):
        with patch("sys.argv", ["updater.py"]), patch("sys.stdin.isatty", return_value=True), patch(
                "sys.stdout.isatty", return_value=True), patch.object(
                updater, "load_costs", return_value=self.costs), patch.object(
                updater, "latest_version", return_value=self.costs.VERSION), patch.object(
                updater, "read_choice") as choice:
            updater.main()
            choice.assert_not_called()

    def test_local_changes_block_update(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".claude").mkdir()
            (home / ".claude/deepseek-install.json").write_text(json.dumps({"repo": directory}))
            with patch.object(updater.Path, "home", return_value=home), patch.object(
                    updater.subprocess, "check_output", side_effect=[updater.REPO_URL, "main", " M example.py"]), patch.object(
                    updater.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "local changes"):
                    updater.update_package()
                run.assert_not_called()

    def test_launcher_migration_preserves_other_content_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            rc = home / ".bashrc"
            rc.write_text('before\n# >>> dclaude wrapper >>>\ndclaude() {\n    claude "$@"\n}\n# <<< dclaude wrapper <<<\nafter\n')
            with patch("builtins.print"):
                install.upgrade_launchers(home, "python3")
                once = rc.read_text()
                install.upgrade_launchers(home, "python3")
            self.assertEqual(rc.read_text(), once)
            self.assertEqual(once.count('python3 "$HOME/.claude/updater.py" "$@"'), 1)
            self.assertTrue(once.startswith("before\n"))
            self.assertTrue(once.endswith("after\n"))


class StatusLineMatcherTests(unittest.TestCase):
    """Older installs wrote the ~/.claude directory in 8.3 form as well."""

    SHORT = r"C:\Users\me\CLAUDE~1\STATUS~1.PY"

    def test_short_directory_spelling_counts_as_ours(self):
        with patch.object(install, "_windows_short_path", return_value=self.SHORT):
            self.assertTrue(install._ours_statusline(
                "python C:/Users/me/CLAUDE~1/STATUS~1.PY"))
            self.assertTrue(install._ours_statusline(
                'python "C:/Users/me/CLAUDE~1/STATUS~1.PY"'))

    def test_other_statuslines_stay_foreign(self):
        with patch.object(install, "_windows_short_path", return_value=self.SHORT):
            self.assertFalse(install._ours_statusline("python C:/tools/statusline.py"))
            self.assertFalse(install._ours_statusline(
                "python C:/Users/me/OTHER~1/STATUS~1.PY"))
            self.assertFalse(install._ours_statusline(
                "python C:/Users/me/.claude/other.py"))

    def test_long_spelling_matches_when_no_short_name_exists(self):
        with patch.object(install, "_windows_short_path", return_value=None):
            self.assertTrue(install._ours_statusline(
                "python C:/Users/me/.claude/statusline.py"))


if __name__ == "__main__":
    unittest.main()
