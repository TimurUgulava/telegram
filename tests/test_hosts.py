"""Hosts behavior against temporary files only; no system writes or cache flushes."""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hosts_telegram as hosts


class HostsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "hosts"
        self.original = b"127.0.0.1 localhost\r\n# existing comment\r\n"
        self.path.write_bytes(self.original)

    def test_add_backup_and_remove_roundtrip_preserve_bytes(self):
        hosts.add_mapping(self.path)
        self.assertEqual(self.path.with_name("hosts.backup-telegram").read_bytes(), self.original)
        self.assertEqual(hosts.mappings(hosts.decode_hosts(self.path.read_bytes())[0]), [hosts.ADDRESS])
        hosts.remove_mapping(self.path)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_repeat_add_does_not_duplicate_or_replace_backup(self):
        hosts.add_mapping(self.path)
        first = self.path.read_bytes()
        hosts.add_mapping(self.path)
        self.assertEqual(self.path.read_bytes(), first)
        self.assertEqual(self.path.with_name("hosts.backup-telegram").read_bytes(), self.original)

    def test_conflicting_address_is_not_modified(self):
        original = self.original + b"192.0.2.1 my.telegram.org\r\n"
        self.path.write_bytes(original)
        with self.assertRaises(ValueError):
            hosts.add_mapping(self.path)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(self.path.with_name("hosts.backup-telegram").exists())

    def test_unmanaged_matching_entry_is_neither_duplicated_nor_removed(self):
        original = self.original + b"149.154.167.220 my.telegram.org\n"
        self.path.write_bytes(original)
        hosts.add_mapping(self.path)
        hosts.remove_mapping(self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_remove_preserves_settings_added_later(self):
        hosts.add_mapping(self.path)
        later = b"192.0.2.10 example.test\r\n"
        with self.path.open("ab") as stream:
            stream.write(later)
        hosts.remove_mapping(self.path)
        self.assertEqual(self.path.read_bytes(), self.original + later)

    def test_roundtrip_without_final_newline_and_with_encoding(self):
        fixtures = [b"127.0.0.1 localhost", b"# comment \xff\n", b"",
                    b"\xff\xfe" + "127.0.0.1 localhost".encode("utf-16-le"),
                    b"\xfe\xff" + "127.0.0.1 localhost\r\n".encode("utf-16-be"),
                    b"\xef\xbb\xbf127.0.0.1 localhost\n"]
        for original in fixtures:
            with self.subTest(original=original):
                backup = self.path.with_name("hosts.backup-telegram")
                if backup.exists():
                    backup.unlink()
                self.path.write_bytes(original)
                hosts.add_mapping(self.path)
                self.assertEqual(hosts.mappings(hosts.decode_hosts(self.path.read_bytes())[0]), [hosts.ADDRESS])
                hosts.remove_mapping(self.path)
                self.assertEqual(self.path.read_bytes(), original)

    def test_incomplete_marker_is_rejected(self):
        original = self.original + hosts.BEGIN.encode() + b"\n"
        self.path.write_bytes(original)
        with self.assertRaises(ValueError):
            hosts.add_mapping(self.path)
        self.assertEqual(self.path.read_bytes(), original)

    def test_status_is_read_only_without_elevation_or_cache_changes(self):
        with patch.object(hosts, "hosts_path", return_value=self.path), \
             patch.object(hosts, "is_elevated") as elevated, \
             patch.object(hosts, "flush_cache") as flush, \
             patch.object(sys, "argv", ["hosts_telegram.py", "status"]), \
             contextlib.redirect_stdout(io.StringIO()):
            hosts.main()
        elevated.assert_not_called()
        flush.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_write_requires_elevation(self):
        with patch.object(hosts, "hosts_path", return_value=self.path), \
             patch.object(hosts, "is_elevated", return_value=False), \
             patch.object(sys, "argv", ["hosts_telegram.py", "add"]):
            with self.assertRaises(SystemExit):
                hosts.main()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_cache_failure_reports_that_file_edit_succeeded(self):
        with patch.object(hosts, "hosts_path", return_value=self.path), \
             patch.object(hosts, "is_elevated", return_value=True), \
             patch.object(hosts, "flush_cache", return_value=False), \
             patch.object(sys, "argv", ["hosts_telegram.py", "add"]), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            hosts.main()
        self.assertIn(hosts.BEGIN.encode(), self.path.read_bytes())
        self.assertIn("очистка кэша не подтверждена", out.getvalue())

    def test_cache_commands_use_platform_tools_only(self):
        with patch.object(sys, "platform", "win32"), patch.object(hosts.subprocess, "run") as run:
            self.assertTrue(hosts.flush_cache())
        self.assertEqual(run.call_args.args[0], ["ipconfig", "/flushdns"])
        with patch.object(sys, "platform", "darwin"), patch.object(hosts.subprocess, "run") as run:
            self.assertTrue(hosts.flush_cache())
        self.assertEqual([c.args[0] for c in run.call_args_list],
                         [["dscacheutil", "-flushcache"], ["killall", "-HUP", "mDNSResponder"]])


if __name__ == "__main__":
    unittest.main()
