#!/usr/bin/env python3
"""Keeps a Claude long-lived token in a file encrypted with a password. Standard library only.

  token_vault.py create FILE   runs `claude setup-token`, hides the token, saves it encrypted
  token_vault.py read FILE     asks for the password and decrypts

Both write the token to stdout, which must not be a terminal. Prompts and the sign-in
screen use /dev/tty.
"""

from __future__ import annotations

import fcntl
import getpass
import os
import pty
import re
import select
import signal
import struct
import subprocess
import sys
import termios
import tty
from pathlib import Path

ANSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]")
HIDE_FROM = (b"Long-lived authentication token created", b"sk-ant-oat")
TOKEN_START = b"sk-ant-oat"
TOKEN_END = b"Store this token"
TOKEN = re.compile(rb"sk-ant-oat[A-Za-z0-9_-]+")
PASSWORD_ATTEMPTS = 3


def split_shown(pending: bytes) -> tuple[bytes, bytes, bool]:
    """Splits output into what the screen may show now, what to hold back, and whether to hide
    everything from here on. Holds back only a tail that could start a marker."""
    hits = [i for i in (pending.find(m) for m in HIDE_FROM) if i >= 0]
    if hits:
        return pending[:min(hits)], b"", True
    keep = 0
    for marker in HIDE_FROM:
        for n in range(min(len(marker) - 1, len(pending)), 0, -1):
            if pending.endswith(marker[:n]):
                keep = max(keep, n)
                break
    return pending[:len(pending) - keep], pending[len(pending) - keep:], False


def extract_token(raw: bytes) -> str | None:
    """The token in setup-token's output, once fully printed. It may be colored and wrapped."""
    text = ANSI.sub(b"", raw)
    start = text.find(TOKEN_START)
    end = text.find(TOKEN_END, start)
    if start < 0 or end < 0:
        return None
    match = TOKEN.match(re.sub(rb"\s+", b"", text[start:end]))
    return match.group().decode() if match else None


def run_setup_token(claude_bin: str, tty_fd: int) -> str:
    rows, cols = struct.unpack("HHHH", fcntl.ioctl(tty_fd, termios.TIOCGWINSZ, b"\0" * 8))[:2]
    pid, master = pty.fork()
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.execvp(claude_bin, [claude_bin, "setup-token"])

    def resize(*_):
        fcntl.ioctl(master, termios.TIOCSWINSZ, fcntl.ioctl(tty_fd, termios.TIOCGWINSZ, b"\0" * 8))

    signal.signal(signal.SIGWINCH, resize)
    saved = termios.tcgetattr(tty_fd)
    tty.setraw(tty_fd)
    output = pending = b""
    hidden = False
    token = None
    try:
        while token is None:
            readable, _, _ = select.select([master, tty_fd], [], [])
            if tty_fd in readable:
                os.write(master, os.read(tty_fd, 1024))
            if master in readable:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    data = b""
                if not data:
                    break
                output += data
                if not hidden:
                    shown, pending, hidden = split_shown(pending + data)
                    os.write(tty_fd, shown)
                if hidden:
                    token = extract_token(output)
    finally:
        termios.tcsetattr(tty_fd, termios.TCSAFLUSH, saved)
        signal.signal(signal.SIGWINCH, signal.SIG_DFL)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
        os.close(master)
    if token is None:
        raise SystemExit("\r\nsetup-token ended without a token.")
    return token


def gpg(args: list[str], data: bytes, password: str) -> subprocess.CompletedProcess:
    read_end, write_end = os.pipe()
    os.write(write_end, password.encode() + b"\n")
    os.close(write_end)
    try:
        return subprocess.run(
            ["gpg", "--batch", "--quiet", "--no-symkey-cache", "--pinentry-mode", "loopback",
             "--passphrase-fd", str(read_end), *args],
            input=data, capture_output=True, pass_fds=(read_end,),
        )
    finally:
        os.close(read_end)


def ask(prompt: str) -> str:
    return getpass.getpass(prompt, stream=sys.stderr)


def create(path: Path) -> str:
    with open("/dev/tty", "r+b", buffering=0) as terminal:
        token = run_setup_token(os.environ.get("CLAUDE_BIN", "claude"), terminal.fileno())
    print("\nToken created. It was not shown.", file=sys.stderr)
    while True:
        password = ask("New password for the token: ")
        if password and password == ask("Repeat the password: "):
            break
        print("The passwords were empty or did not match.", file=sys.stderr)
    result = gpg(["--symmetric", "--cipher-algo", "AES256", "--output", "-"], token.encode(), password)
    if result.returncode:
        raise SystemExit(f"gpg failed: {result.stderr.decode().strip()}")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(result.stdout)
    partial.replace(path)
    print(f"Saved, encrypted, to {path}", file=sys.stderr)
    return token


def read(path: Path) -> str:
    encrypted = path.read_bytes()
    for _ in range(PASSWORD_ATTEMPTS):
        result = gpg(["--decrypt"], encrypted, ask(f"Password for {path.name}: "))
        if result.returncode == 0:
            return result.stdout.decode().strip()
        print("Wrong password.", file=sys.stderr)
    raise SystemExit("Too many wrong passwords.")


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("create", "read"):
        raise SystemExit(__doc__)
    if sys.stdout.isatty():
        raise SystemExit("stdout is a terminal: capture it, for example TOKEN=$(token_vault.py read FILE)")
    command, path = sys.argv[1], Path(sys.argv[2]).expanduser()
    token = create(path) if command == "create" else read(path)
    sys.stdout.write(token)


if __name__ == "__main__":
    main()
