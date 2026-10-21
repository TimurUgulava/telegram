#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Credentials and existing user sessions for the Telegram skill."""
from contextlib import contextmanager
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys

KEYCHAIN_ID_SERVICE = "tg-api-id"
KEYCHAIN_HASH_SERVICE = "tg-api-hash"
SETUP_HINT = "Запустите локально: python scripts/setup_telegram.py setup"


def config_path():
    return Path(os.environ.get("TG_CONFIG", "~/.tg/config.json")).expanduser()


def read_config():
    path = config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except PermissionError:
        raise SystemExit("[telegram] Нет доступа к config.json. Закройте администраторское окно, "
                         "откройте обычный терминал и проверьте владельца файла. Не удаляйте сессию.")
    except (OSError, ValueError):
        raise SystemExit("[telegram] Не удалось прочитать локальный config.json. " + SETUP_HINT)
    if not isinstance(data, dict):
        raise SystemExit("[telegram] В config.json ожидается объект JSON.")
    return data


def _keychain(service):
    if sys.platform != "darwin":
        return None
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", service,
             "-a", getpass.getuser(), "-w"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def validate_credentials(api_id, api_hash):
    try:
        api_id = int(api_id)
    except (TypeError, ValueError):
        raise SystemExit("[telegram] api_id должен быть положительным числом.")
    if api_id <= 0:
        raise SystemExit("[telegram] api_id должен быть положительным числом.")
    if not isinstance(api_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash.strip()):
        raise SystemExit("[telegram] api_hash должен содержать ровно 32 символа: цифры 0–9 "
                         "и латинские буквы a–f. Скопируйте значение целиком и вставьте один раз.")
    return api_id, api_hash.strip()


def is_elevated():
    """Detect an elevated Windows token or a root process on Unix."""
    if sys.platform == "win32":
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    return hasattr(os, "geteuid") and os.geteuid() == 0


def get_credentials():
    # Each source is a pair: never mix credentials for unrelated applications.
    env_id, env_hash = os.environ.get("TG_API_ID"), os.environ.get("TG_API_HASH")
    if env_id or env_hash:
        if not env_id or not env_hash:
            raise SystemExit("[telegram] Задайте обе переменные: TG_API_ID и TG_API_HASH.")
        return validate_credentials(env_id, env_hash)
    config = read_config()
    if config.get("api_id") or config.get("api_hash"):
        return validate_credentials(config.get("api_id"), config.get("api_hash"))
    api_id, api_hash = _keychain(KEYCHAIN_ID_SERVICE), _keychain(KEYCHAIN_HASH_SERVICE)
    if not api_id or not api_hash:
        raise SystemExit(
            "[telegram] Нет API-ключей. " + SETUP_HINT +
            "\nЕсли ключей ещё нет: python scripts/setup_telegram.py api-guide"
        )
    return validate_credentials(api_id, api_hash)


def get_session():
    value = os.environ.get("TG_SESSION") or read_config().get("session") or "~/.tg/tg_session"
    if not isinstance(value, str) or not value.strip():
        raise SystemExit("[telegram] TG_SESSION должен содержать путь к сессии.")
    return str(Path(value).expanduser().resolve())


def session_file(session):
    return Path(session if session.endswith(".session") else session + ".session")


def save_credentials(api_id, api_hash):
    api_id, api_hash = validate_credentials(api_id, api_hash)
    path = config_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = read_config()
    data.update(api_id=api_id, api_hash=api_hash)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        if os.name != "nt":
            os.fchmod(stream.fileno(), 0o600)
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def make_client(session, credentials):
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        raise SystemExit("[telegram] Не установлен Telethon: python -m pip install -r requirements.txt")
    return TelegramClient(session, *credentials, device_model="Telegram Skill",
                          connection_retries=1, request_retries=1, flood_sleep_threshold=0)


@contextmanager
def get_client():
    credentials = get_credentials()
    session = get_session()
    if not session_file(session).is_file():
        raise SystemExit("[telegram] Нет авторизованной сессии. " + SETUP_HINT)
    client = make_client(session, credentials)
    try:
        client.connect()
        if not client.is_user_authorized():
            raise SystemExit("[telegram] Требуется повторный вход. " + SETUP_HINT)
        yield client
    finally:
        client.disconnect()


def dialog_type(entity):
    cls = entity.__class__.__name__
    if cls == "User":
        return "личка"
    if cls == "Chat":
        return "группа"
    if cls == "Channel":
        return "канал" if getattr(entity, "broadcast", False) else "супергруппа"
    return cls
