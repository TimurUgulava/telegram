#!/usr/bin/env python3
"""
Отправка сообщения в чат Telegram. ⚠️ ЕДИНСТВЕННЫЙ скрипт скилла, который пишет.

ПРЕДОХРАНИТЕЛЬ: без флага --yes скрипт работает в режиме СУХОГО ПРОГОНА —
резолвит чат и печатает, что было бы отправлено, но НИЧЕГО НЕ ШЛЁТ.
Реальная отправка только с --yes. Это страховка: нет --yes -> нет сообщения.

Флаг --yes по протоколу скилла ставится ТОЛЬКО после явного согласия пользователя.

Usage:
    # сухой прогон (безопасно, ничего не уходит):
    python3 tg_send.py --chat <id|@username> --text "сообщение"
    # реальная отправка (после согласования):
    python3 tg_send.py --chat <id|@username> --text "сообщение" --yes
    # длинный текст из файла:
    python3 tg_send.py --chat <id|@username> --file path/to/message.txt --yes
    # вложение с подписью (сначала без --yes):
    python3 tg_send.py --chat <id|@username> --attach report.md --text "Отчёт"
"""
import argparse
import hashlib
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tg_common import get_client, dialog_type  # noqa: E402


def parse_chat(value):
    v = value.strip()
    if v.startswith("@") or not (v.lstrip("-").isdigit()):
        return v
    return int(v)


def peer_id(entity, fallback):
    """Тот же маркированный id, что отдаёт tg_resolve.py (-100… для каналов)."""
    try:
        from telethon import utils
        return utils.get_peer_id(entity)
    except Exception:
        return getattr(entity, "id", fallback)


def attachment_digest(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest()


def caption_length(text):
    # Telegram measures text entities in UTF-16 code units, after Markdown parsing.
    try:
        from telethon.extensions import markdown
    except ImportError:
        raise SystemExit("[tg_send] Не установлен Telethon: python -m pip install -r requirements.txt")
    plain, _ = markdown.parse(text)
    return len(plain.encode("utf-16-le")) // 2


def main():
    ap = argparse.ArgumentParser(description="Отправка сообщения (по умолчанию — сухой прогон)")
    ap.add_argument("--chat", required=True, help="id чата или @username")
    ap.add_argument("--text", help="текст сообщения или подпись к вложению")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--file", help="прочитать файл UTF-8 и отправить его текст сообщением")
    g.add_argument("--attach", help="отправить локальный файл документом")
    ap.add_argument("--sha256", help="ожидаемая контрольная сумма вложения из сухого прогона")
    ap.add_argument("--yes", action="store_true",
                    help="РЕАЛЬНО отправить. Без него — только сухой прогон.")
    args = ap.parse_args()

    if args.file and args.text is not None:
        ap.error("--file уже содержит текст; --text можно сочетать только с --attach")
    if args.sha256 and not args.attach:
        ap.error("--sha256 используется только с --attach")
    attachment = None
    digest = None
    if args.attach:
        try:
            attachment = Path(args.attach).expanduser().resolve(strict=True)
            if not attachment.is_file():
                raise ValueError("ожидается файл, не папка")
            with attachment.open("rb") as stream:
                digest = attachment_digest(stream)
        except (OSError, ValueError) as error:
            ap.error("не удалось прочитать вложение: " + str(error))
        if args.sha256 and digest.lower() != args.sha256.lower():
            ap.error("вложение изменилось после согласования; повторите сухой прогон")
        if caption_length(args.text or "") > 1024:
            ap.error("подпись превышает лимит Telegram 1024; сократите её")

    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeError):
            sys.exit("[tg_send] Не удалось прочитать текст UTF-8. Для вложения используйте --attach.")
    else:
        text = args.text
    if not attachment and (not text or not text.strip()):
        sys.exit("[tg_send] Пустой текст — отправлять нечего.")

    chat = parse_chat(args.chat)
    with get_client() as client:
        entity = client.get_entity(chat)
        title = getattr(entity, "title", None) or \
            (" ".join(filter(None, [getattr(entity, "first_name", None),
                                    getattr(entity, "last_name", None)])) or str(chat))
        ctype = dialog_type(entity)
        pid = peer_id(entity, chat)

        if not args.yes:
            # Карточка согласования в формате протокола SKILL.md — показывай пользователю 1:1.
            print("📤 Готов отправить:")
            print(f"Чат:  «{title}»  (тип: {ctype}, id: {pid})")
            if attachment:
                print(f"Файл: {attachment.name}")
                print(f"Путь: {attachment}")
                print(f"Размер: {attachment.stat().st_size} байт")
                print(f"SHA256: {digest}")
                print("Подпись:")
                print(text or "(без подписи)")
            else:
                print("Текст:")
                print(text)
            print()
            print("⚠️ Сухой прогон — сообщение НЕ отправлено. После явного «да» от пользователя — повтори с --yes.")
            return

        if attachment:
            # Keep the verified file open while sending; check changes since preview.
            with attachment.open("rb") as stream:
                if attachment_digest(stream) != digest:
                    ap.error("вложение изменилось; повторите сухой прогон")
                msg = client.send_file(entity, stream, caption=text or "", force_document=True)
        else:
            msg = client.send_message(entity, text)
        print(f"[tg_send] ОТПРАВЛЕНО в «{title}» (id: {pid}), message_id = {msg.id}")


if __name__ == "__main__":
    main()
