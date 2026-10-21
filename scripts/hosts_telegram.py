#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Explicit local hosts edit. Run status normally; add/remove require elevation."""
import argparse
from pathlib import Path
import re
import subprocess
import sys

from _tg_common import is_elevated

ADDRESS = "149.154.167.220"
DOMAIN = "my.telegram.org"
BEGIN = "# telegram-skill: begin"
END = "# telegram-skill: end"


def hosts_path():
    if sys.platform == "win32":
        import os
        return Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/drivers/etc/hosts"
    if sys.platform == "darwin":
        return Path("/etc/hosts")
    raise SystemExit("Автоматическая правка подготовлена для Windows и macOS; файл не изменён.")


def decode_hosts(data):
    # Preserve byte encoding, including non-UTF-8 comments and existing BOMs.
    for bom, codec in [(b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"),
                       (b"\xef\xbb\xbf", "utf-8")]:
        if data.startswith(bom):
            return data[len(bom):].decode(codec), codec, bom
    return data.decode("latin-1"), "latin-1", b""


def mappings(text):
    found = []
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        if len(fields) > 1 and DOMAIN in [value.lower() for value in fields[1:]]:
            found.append(fields[0])
    return found


def add_mapping(path):
    original = path.read_bytes()
    text, codec, bom = decode_hosts(original)
    existing = mappings(text)
    if existing:
        if set(existing) == {ADDRESS}:
            return "Нужная запись уже есть; повторно не добавлена."
        raise ValueError("Для my.telegram.org уже указан другой адрес. Проверьте его вручную; файл не изменён.")
    if BEGIN in text or END in text:
        raise ValueError("Найден незавершённый блок telegram-skill. Проверьте его вручную; файл не изменён.")
    backup = path.with_name(path.name + ".backup-telegram")
    try:
        with backup.open("xb") as stream:
            stream.write(original)
    except FileExistsError:
        # Keep the first backup intact; removal below never restores unrelated settings.
        pass
    newline = "\r\n" if "\r\n" in text else "\n"
    prefix = "" if not text or text.endswith(("\r", "\n")) else newline
    block = prefix + newline.join([BEGIN, ADDRESS + " " + DOMAIN, END, ""])
    # Append, preserving the current file's permissions and all existing bytes.
    with path.open("ab") as stream:
        stream.write(block.encode(codec))
    return "Запись добавлена. Исходная резервная копия: " + str(backup)


def remove_mapping(path):
    data = path.read_bytes()
    text, codec, bom = decode_hosts(data)
    pattern = re.compile(r"(?m)^" + re.escape(BEGIN) + r"\r?\n" +
                         re.escape(ADDRESS + " " + DOMAIN) + r"\r?\n" +
                         re.escape(END) + r"(?:\r?\n|$)")
    updated, count = pattern.subn("", text)
    if count == 0:
        return "Управляемой записи нет; файл не изменён."
    backup = path.with_name(path.name + ".backup-telegram")
    if backup.is_file():
        previous = backup.read_bytes()
        # Restore the exact final-newline state only when no other data changed.
        if bom + updated.encode(codec) in (previous + "\n".encode(codec), previous + "\r\n".encode(codec)):
            path.write_bytes(previous)
            return "Добавленная запись удалена; остальные строки сохранены."
    path.write_bytes(bom + updated.encode(codec))
    return "Добавленная запись удалена; остальные строки сохранены."


def flush_cache():
    commands = [["ipconfig", "/flushdns"]] if sys.platform == "win32" else [
        ["dscacheutil", "-flushcache"], ["killall", "-HUP", "mDNSResponder"]]
    try:
        for cmd in commands:
            subprocess.run(cmd, check=True, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "add", "remove"])
    args = parser.parse_args()
    try:
        path = hosts_path()
        if args.action == "status":
            found = mappings(decode_hosts(path.read_bytes())[0])
            print("Адреса my.telegram.org: " + ", ".join(found) if found else "Записи my.telegram.org нет.")
            return
        if not is_elevated():
            raise SystemExit("Для правки hosts нужны права администратора. "
                             "Мастер setup после правки запускайте в обычном окне.")
        print(add_mapping(path) if args.action == "add" else remove_mapping(path))
        if not flush_cache():
            print("Правка файла завершена, очистка кэша не подтверждена. Перезагрузите компьютер.")
        print("Полностью закройте браузер и откройте его снова.")
        print("Закройте администраторское окно. Мастер setup запускайте в обычном терминале, без sudo.")
    except (OSError, UnicodeError, ValueError) as error:
        raise SystemExit("Правка hosts не завершена: " + str(error))


if __name__ == "__main__":
    main()
