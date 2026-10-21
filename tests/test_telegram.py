"""Offline behavior checks. No real account, credentials or network calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import _tg_common as auth
import setup_telegram as setup
import tg_send


class CredentialsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        env = {key: value for key, value in os.environ.items() if not key.startswith("TG_")}
        env["TG_CONFIG"] = str(self.path / "config.json")
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.keychain = patch.object(auth, "_keychain", return_value=None)
        self.keychain.start()
        self.addCleanup(self.keychain.stop)

    def test_missing_keys_give_setup_and_guide(self):
        with self.assertRaises(SystemExit) as error:
            auth.get_credentials()
        self.assertIn("setup_telegram.py setup", str(error.exception))
        self.assertIn("api-guide", str(error.exception))

    def test_save_reload_and_restrict_permissions(self):
        auth.save_credentials(12345, "example-hash-not-a-real-credential")
        self.assertEqual(auth.get_credentials(), (12345, "example-hash-not-a-real-credential"))
        if os.name != "nt":
            self.assertEqual(auth.config_path().stat().st_mode & 0o777, 0o600)

    def test_partial_env_does_not_mix_with_config(self):
        auth.save_credentials(12345, "example-hash")
        os.environ["TG_API_ID"] = "54321"
        with self.assertRaises(SystemExit):
            auth.get_credentials()

    def test_full_env_pair_overrides_config(self):
        auth.save_credentials(12345, "old-example")
        os.environ.update(TG_API_ID="54321", TG_API_HASH="new-example")
        self.assertEqual(auth.get_credentials(), (54321, "new-example"))

    def test_invalid_config_never_echoes_contents(self):
        auth.config_path().write_text("not-json-private-example", encoding="utf-8")
        with self.assertRaises(SystemExit) as error:
            auth.get_credentials()
        self.assertNotIn("not-json-private-example", str(error.exception))

    def test_session_path_expands_tilde(self):
        os.environ["TG_SESSION"] = "~/telegram-example/session"
        self.assertEqual(auth.get_session(), str(Path.home() / "telegram-example/session"))

    def test_missing_session_does_not_start_login_or_create_file(self):
        os.environ.update(TG_API_ID="12345", TG_API_HASH="example-hash",
                          TG_SESSION=str(self.path / "absent"))
        with patch.object(auth, "make_client") as factory:
            with self.assertRaises(SystemExit):
                with auth.get_client():
                    self.fail("must not yield")
        factory.assert_not_called()
        self.assertFalse((self.path / "absent.session").exists())

    def test_expired_session_does_not_start_interactive_auth(self):
        session = self.path / "existing"
        auth.session_file(str(session)).touch()
        os.environ.update(TG_API_ID="12345", TG_API_HASH="example-hash", TG_SESSION=str(session))
        client = Mock()
        client.is_user_authorized.return_value = False
        with patch.object(auth, "make_client", return_value=client):
            with self.assertRaises(SystemExit):
                with auth.get_client():
                    self.fail("must not yield")
        client.start.assert_not_called()
        client.disconnect.assert_called_once()

    def test_offline_check_does_not_validate_login(self):
        session = self.path / "existing"
        auth.session_file(str(session)).touch()
        os.environ.update(TG_API_ID="12345", TG_API_HASH="example-hash", TG_SESSION=str(session))
        with patch.object(setup, "get_client") as factory, contextlib.redirect_stdout(io.StringIO()) as out:
            setup.check(offline=True)
        factory.assert_not_called()
        self.assertIn("не проверялись", out.getvalue())


class SendTests(unittest.TestCase):
    def test_preview_never_sends(self):
        client = Mock()
        client.get_entity.return_value = type("User", (), {"first_name": "Example", "last_name": None})()
        @contextlib.contextmanager
        def fake_client():
            yield client
        with patch.object(tg_send, "get_client", fake_client), \
             patch.object(tg_send, "peer_id", return_value=12345), \
             patch.object(sys, "argv", ["tg_send.py", "--chat", "me", "--text", "Example message"]), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            tg_send.main()
        client.send_message.assert_not_called()
        self.assertIn("Example message", out.getvalue())
        self.assertIn("12345", out.getvalue())

    def test_yes_sends_exact_text_once(self):
        client = Mock()
        entity = type("User", (), {"first_name": "Example", "last_name": None})()
        client.get_entity.return_value = entity
        client.send_message.return_value.id = 17
        @contextlib.contextmanager
        def fake_client():
            yield client
        text = "Line one\n\n**Line two**"
        with patch.object(tg_send, "get_client", fake_client), \
             patch.object(tg_send, "peer_id", return_value=12345), \
             patch.object(sys, "argv", ["tg_send.py", "--chat", "me", "--text", text, "--yes"]), \
             contextlib.redirect_stdout(io.StringIO()):
            tg_send.main()
        client.send_message.assert_called_once_with(entity, text)


class OnboardingTests(unittest.TestCase):
    def test_new_user_can_store_keys_and_reach_local_login(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            session = str(Path(tmp) / "example-session")
            client = Mock()
            client.is_user_authorized.return_value = False
            client.get_me.return_value = type("User", (), {"bot": False})()
            with patch.dict(os.environ, {"TG_CONFIG": str(config)}), \
                 patch.object(sys.stdin, "isatty", return_value=True), \
                 patch.object(setup, "get_credentials", side_effect=SystemExit("Нет API-ключей")), \
                 patch.object(setup, "api_guide") as guide, \
                 patch("builtins.input", side_effect=["12345", "yes"]), \
                 patch.object(setup.getpass, "getpass", return_value="local-example-hash"), \
                 patch.object(setup, "get_session", return_value=session), \
                 patch.object(setup, "make_client", return_value=client), \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                setup.setup()
            guide.assert_called_once()
            self.assertEqual(json.loads(config.read_text())["api_id"], 12345)
            self.assertNotIn("local-example-hash", out.getvalue())
            client.start.assert_called_once()
            self.assertTrue(callable(client.start.call_args.kwargs["code_callback"]))
            client.send_message.assert_not_called()

    def test_declining_storage_does_not_save_or_start_login(self):
        with patch.object(sys.stdin, "isatty", return_value=True), \
             patch.object(setup, "get_credentials", side_effect=SystemExit("Нет API-ключей")), \
             patch.object(setup, "api_guide"), \
             patch("builtins.input", side_effect=["12345", "n"]), \
             patch.object(setup.getpass, "getpass", return_value="example-hash"), \
             patch.object(setup, "save_credentials") as save, \
             patch.object(setup, "make_client") as factory, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                setup.setup()
        save.assert_not_called()
        factory.assert_not_called()

    def test_existing_authorization_is_reused_without_login_or_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = str(Path(tmp) / "example-session")
            client = Mock()
            client.is_user_authorized.return_value = True
            client.get_me.return_value = type("User", (), {"bot": False})()
            with patch.object(sys.stdin, "isatty", return_value=True), \
                 patch.object(setup, "get_credentials", return_value=(12345, "example-hash")), \
                 patch.object(setup, "get_session", return_value=session), \
                 patch.object(setup, "make_client", return_value=client), \
                 contextlib.redirect_stdout(io.StringIO()):
                setup.setup()
            client.start.assert_not_called()
            client.send_message.assert_not_called()
            client.disconnect.assert_called_once()

    def test_guide_does_not_require_telethon_or_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, TG_CONFIG=str(Path(tmp) / "absent.json"))
            result = subprocess.run([sys.executable, str(ROOT / "scripts/setup_telegram.py"), "api-guide"],
                                    capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sudo nano /etc/hosts", result.stdout)
        self.assertIn("Ctrl + O", result.stdout)

    def test_setup_requires_private_interactive_terminal(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/setup_telegram.py"), "setup"],
                                input="", capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("своём терминале", result.stderr)


if __name__ == "__main__":
    unittest.main()
