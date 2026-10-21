"""Offline regressions for local setup and document uploads."""
import contextlib
import ctypes
import hashlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import _tg_common as auth
import setup_telegram as setup
import tg_send


class SetupUpdateTests(unittest.TestCase):
    def test_unreadable_config_reports_permissions_without_deleting_data(self):
        with patch.object(auth, "config_path", return_value=Path("example-config.json")), \
             patch.object(Path, "read_text", side_effect=PermissionError):
            with self.assertRaises(SystemExit) as error:
                auth.read_config()
        self.assertIn("Нет доступа", str(error.exception))
        self.assertIn("Не удаляйте сессию", str(error.exception))

    def test_elevated_setup_stops_before_credentials_and_network(self):
        with patch.object(setup, "is_elevated", return_value=True), \
             patch.object(setup, "get_credentials") as credentials, \
             patch.object(setup, "make_client") as factory:
            with self.assertRaises(SystemExit) as error:
                setup.setup()
        self.assertIn("обычном", str(error.exception))
        credentials.assert_not_called()
        factory.assert_not_called()

    def test_windows_elevation_checks_current_token(self):
        library = SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=lambda: 1))
        with patch.object(sys, "platform", "win32"), patch.object(ctypes, "windll", library, create=True):
            self.assertTrue(auth.is_elevated())
        library.shell32.IsUserAnAdmin = lambda: 0
        with patch.object(sys, "platform", "win32"), patch.object(ctypes, "windll", library, create=True):
            self.assertFalse(auth.is_elevated())

    def test_invalid_hash_rejected_without_showing_value(self):
        for value in ["", "a" * 31, "a" * 33, "z" * 32, "a" * 16 + " " + "a" * 16]:
            with self.subTest(value=value), self.assertRaises(SystemExit) as error:
                auth.validate_credentials(12345, value)
            if value:
                self.assertNotIn(value, str(error.exception))

    def test_hash_format_retry_is_local(self):
        with patch("builtins.input", side_effect=["12345", "12345"]), \
             patch.object(setup.getpass, "getpass", side_effect=["wrong-example", "a" * 32]), \
             patch.object(setup, "make_client") as factory, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            result = setup.prompt_credentials()
        self.assertEqual(result, (12345, "a" * 32))
        self.assertNotIn("wrong-example", out.getvalue())
        self.assertNotIn("a" * 32, out.getvalue())
        factory.assert_not_called()

    def test_hash_format_retries_are_bounded(self):
        with patch("builtins.input", return_value="12345") as prompt, \
             patch.object(setup.getpass, "getpass", return_value="wrong-example"), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                setup.prompt_credentials()
        self.assertEqual(prompt.call_count, 3)


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "report.md"
        self.path.write_bytes(b"# Example report\n")
        self.client = Mock()
        self.entity = SimpleNamespace(first_name="Example", last_name=None)
        self.client.get_entity.return_value = self.entity
        self.sent = []
        def send(entity, file, **kwargs):
            self.sent.append((entity, file.read(), kwargs))
            return SimpleNamespace(id=17)
        self.client.send_file.side_effect = send
        @contextlib.contextmanager
        def fake_client():
            yield self.client
        self.factory = patch.object(tg_send, "get_client", fake_client)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        peer = patch.object(tg_send, "peer_id", return_value=12345)
        peer.start()
        self.addCleanup(peer.stop)

    def call(self, *args):
        with patch.object(sys, "argv", ["tg_send.py", "--chat", "me", *args]), \
             contextlib.redirect_stdout(io.StringIO()) as out, \
             contextlib.redirect_stderr(io.StringIO()):
            tg_send.main()
        return out.getvalue()

    def test_document_preview_never_uploads(self):
        result = self.call("--attach", str(self.path), "--text", "Example caption")
        self.client.send_file.assert_not_called()
        self.client.send_message.assert_not_called()
        for item in ["report.md", "17 байт", "Example caption", hashlib.sha256(self.path.read_bytes()).hexdigest()]:
            self.assertIn(item, result)

    def test_approved_document_sends_exact_bytes_and_caption(self):
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.call("--attach", str(self.path), "--text", "Example caption", "--sha256", digest, "--yes")
        self.assertEqual(self.sent, [(self.entity, b"# Example report\n", {"caption": "Example caption", "force_document": True})])
        self.client.send_message.assert_not_called()

    def test_document_can_have_no_caption(self):
        self.call("--attach", str(self.path), "--yes")
        self.assertEqual(self.sent[0][2]["caption"], "")

    def test_modified_document_is_not_sent(self):
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.path.write_bytes(b"Changed report\n")
        with self.assertRaises(SystemExit):
            self.call("--attach", str(self.path), "--sha256", digest, "--yes")
        self.assertFalse(self.sent)
        self.client.get_entity.assert_not_called()

    def test_directory_or_missing_file_is_rejected_before_connection(self):
        for path in [self.path.parent, self.path.parent / "absent.md"]:
            with self.subTest(path=path), self.assertRaises(SystemExit):
                self.call("--attach", str(path), "--yes")
        self.client.get_entity.assert_not_called()

    def test_overlong_caption_rejected_before_connection(self):
        for caption in ["a" * 1025, "😀" * 513]:
            with self.subTest(length=len(caption)), self.assertRaises(SystemExit):
                self.call("--attach", str(self.path), "--text", caption, "--yes")
        self.client.get_entity.assert_not_called()

    def test_caption_boundary_uses_parsed_markdown(self):
        self.call("--attach", str(self.path), "--text", "**" + "a" * 1024 + "**")
        self.client.send_file.assert_not_called()

    def test_text_file_mode_still_sends_text(self):
        self.call("--file", str(self.path), "--yes")
        self.client.send_message.assert_called_once_with(self.entity, "# Example report\n")
        self.client.send_file.assert_not_called()

    def test_conflicting_modes_are_rejected(self):
        with self.assertRaises(SystemExit):
            self.call("--file", str(self.path), "--attach", str(self.path))
        self.client.get_entity.assert_not_called()


if __name__ == "__main__":
    unittest.main()
