#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local onboarding: API guide, private credential entry, login and connection check."""
import argparse
import getpass
import os
from pathlib import Path
import sqlite3
import sys

from _tg_common import (config_path, get_client, get_credentials, get_session,
                        is_elevated, make_client, save_credentials, session_file, validate_credentials)

ROOT = Path(__file__).resolve().parent.parent


def api_guide():
    print((ROOT / "references" / "api-keys.md").read_text(encoding="utf-8"))


def check(offline=False):
    get_credentials()
    if not session_file(get_session()).is_file():
        raise SystemExit("Нет сессии. Запустите: python scripts/setup_telegram.py setup")
    if offline:
        print("Ключи и файл сессии найдены. Вход и связь с Telegram не проверялись.")
        return
    with get_client() as client:
        me = client.get_me()
        if me is None or getattr(me, "bot", False):
            raise SystemExit("Нужна сессия личного аккаунта, а не бота.")
    print("Подключение к личному аккаунту подтверждено. Сообщения не отправлялись.")


def prompt_credentials():
    print("api_hash при вводе не виден. Вставьте его один раз и нажмите Enter.")
    for _ in range(3):
        api_id = input("api_id: ").strip()
        api_hash = getpass.getpass("api_hash (ввод скрыт): ")
        try:
            return validate_credentials(api_id, api_hash)
        except SystemExit as error:
            print(str(error))
    raise SystemExit("Не удалось проверить формат ключей. Проверьте значения на странице приложения.")


def setup():
    if is_elevated():
        raise SystemExit("Закройте окно с правами администратора. Запустите мастер в обычном "
                         "PowerShell или Терминале, без sudo. Ключи и сессия не изменены.")
    if not sys.stdin.isatty():
        raise SystemExit("Запустите мастер в своём терминале. Ключи, код и пароль не вводятся в чат.")
    print("Настройка Telegram. Мастер не отправляет сообщения в чаты.")
    print("Секреты вводите только здесь, не передавайте их ассистенту.")
    try:
        credentials = get_credentials()
        print("API-ключи уже настроены; используем их без показа значений.")
    except SystemExit as error:
        if "Нет API-ключей" not in str(error):
            raise
        print("API-ключей ещё нет. Получите их по инструкции ниже.")
        api_guide()
        print("Когда получите ключи, вернитесь сюда. Прервать мастер: Ctrl+C.")
        credentials = prompt_credentials()
        answer = input("Сохранить ключи в локальном конфиге? [д/н]: ").strip().lower()
        if answer not in {"д", "да", "y", "yes"}:
            raise SystemExit("Отменено, ключи не сохранены. Можно использовать TG_API_ID/TG_API_HASH.")
        save_credentials(*credentials)
        print("Ключи сохранены локально:", config_path())

    session = get_session()
    target = session_file(session)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    old_umask = os.umask(0o077)
    client = None
    try:
        client = make_client(session, credentials)
        client.connect()
        if not client.is_user_authorized():
            print("Войдите в свой аккаунт: телефон, код входа, пароль двухэтапной защиты, если включён.")
            client.start(
                phone=lambda: getpass.getpass("Телефон с кодом страны (ввод скрыт): ").strip(),
                code_callback=lambda: getpass.getpass("Код входа (ввод скрыт): ").strip(),
                password=lambda: getpass.getpass("Пароль двухэтапной защиты (ввод скрыт): "),
                max_attempts=3,
            )
        me = client.get_me()
        if me is None or getattr(me, "bot", False):
            raise SystemExit("Нужен личный аккаунт Telegram, а не бот.")
    finally:
        if client is not None:
            client.disconnect()
        os.umask(old_umask)
        if target.exists() and os.name != "nt":
            target.chmod(0o600)
    print("Готово: сессия личного аккаунта авторизована. Сообщения в чаты не отправлялись.")
    print("Проверка: python scripts/setup_telegram.py check")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("setup", help="локальный мастер: ключи, вход, проверка")
    diag = sub.add_parser("check", help="проверка существующего доступа без отправки сообщений")
    diag.add_argument("--offline", action="store_true", help="только наличие ключей и файла сессии")
    sub.add_parser("api-guide", help="как получить api_id/api_hash и что попробовать при ERROR")
    args = parser.parse_args()
    try:
        if args.command == "api-guide":
            api_guide()
        elif args.command == "check":
            check(args.offline)
        else:
            setup()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\nНастройка прервана. Для продолжения запустите мастер снова.")
    except sqlite3.OperationalError:
        raise SystemExit("Сессия занята или недоступна. Завершите другой процесс с этой сессией и повторите.")
    except OSError:
        raise SystemExit("Ошибка доступа к файлам или сети. Проверьте права и подключение; секреты не выводятся.")
    except Exception as error:
        if type(error).__name__ == "ApiIdInvalidError":
            raise SystemExit("Telegram отклонил пару api_id/api_hash. Проверьте оба значения "
                             "у одного приложения; не отправляйте ключи в чат.")
        raise SystemExit("Telegram не завершил операцию (" + type(error).__name__ +
                         "). Проверьте подключение и ключи; не повторяйте вход многократно.")


if __name__ == "__main__":
    main()
