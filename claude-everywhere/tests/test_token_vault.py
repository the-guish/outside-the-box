"""scripts/token_vault.py, driven through a pseudo-terminal like a person would."""

import fcntl
import os
import pty
import re
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from token_vault import extract_token, split_shown  # noqa: E402

VAULT = ROOT / "scripts" / "token_vault.py"
FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py")
TOKEN = "sk-ant-oat01-abc_DEF-123"
FAKE_TOKEN = "sk-ant-oat01-" + "Ab3_" * 15 + "-xY9" * 10


@pytest.mark.parametrize("pending, shown, held, hidden", [
    (b"sign in at https://x\r\n", b"sign in at https://x\r\n", b"", False),
    (b"prompt > ", b"prompt > ", b"", False),
    (b"line\r\nLong-lived auth", b"line\r\n", b"Long-lived auth", False),
    (b"done\r\nsk-ant", b"done\r\n", b"sk-ant", False),
    (b"ok\r\n\x1b[32m\xe2\x9c\x93 Long-lived authentication token created!", b"ok\r\n\x1b[32m\xe2\x9c\x93 ", b"", True),
    (b"\x1b[33msk-ant-oat01-abc", b"\x1b[33m", b"", True),
])
def test_split_shown(pending, shown, held, hidden):
    assert split_shown(pending) == (shown, held, hidden)


@pytest.mark.parametrize("raw, expected", [
    (f"{TOKEN}\r\nStore this token".encode(), TOKEN),
    (f"\x1b[33m{TOKEN}\x1b[39m\r\n\x1b[2mStore this token".encode(), TOKEN),
    (b"\x1b[33msk-ant-oat01-ab\x1b[39m\r\n\x1b[33mc_DEF-123\x1b[39m\r\n  Store this token", TOKEN),
    (f"{TOKEN}\r\n".encode(), None),
    (b"Store this token", None),
])
def test_extract_token(raw, expected):
    assert extract_token(raw) == expected


class Terminal:
    """Runs a command with a pseudo-terminal as its /dev/tty and a pipe as its stdout."""

    def __init__(self, args, env):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, b"\x18\x00\x50\x00\x00\x00\x00\x00")  # 24x80
        self.proc = subprocess.Popen(
            args, env=env, stdin=slave, stderr=slave, stdout=subprocess.PIPE,
            start_new_session=True, preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0),
        )
        os.close(slave)
        self.screen = b""

    def wait_for(self, text, timeout=10):
        deadline = time.monotonic() + timeout
        while text.encode() not in self.screen:
            if time.monotonic() > deadline:
                raise AssertionError(f"{text!r} not on screen: {self.screen[-500:]!r}")
            try:
                self.screen += os.read(self.master, 4096)
            except OSError:
                time.sleep(0.05)

    def type(self, text):
        os.write(self.master, text.encode())

    def finish(self):
        stdout = self.proc.communicate(timeout=20)[0].decode()
        try:
            while chunk := os.read(self.master, 4096):
                self.screen += chunk
        except OSError:
            pass
        os.close(self.master)
        return self.proc.returncode, stdout


@pytest.fixture
def env(tmp_path):
    gnupg = tmp_path / "gnupg"
    gnupg.mkdir(mode=0o700)
    env = {**os.environ, "GNUPGHOME": str(gnupg), "CLAUDE_BIN": str(FAKE_CLAUDE),
           "FAKE_SETUP_TOKEN": FAKE_TOKEN}
    yield env
    subprocess.run(["gpgconf", "--kill", "gpg-agent"], env=env, check=False)


def test_create_then_read(env, tmp_path):
    vault = tmp_path / "token.gpg"

    created = Terminal([sys.executable, str(VAULT), "create", str(vault)], env)
    created.wait_for("Paste code here")
    created.type("the-code\r")
    created.wait_for("New password")
    created.type("hunter2\r")
    created.wait_for("Repeat")
    created.type("hunter2\r")
    assert created.finish() == (0, FAKE_TOKEN)
    screen = re.sub(rb"\s|\x1b\[[0-9;]*m", b"", created.screen)
    assert b"sk-ant" not in screen and FAKE_TOKEN[20:40].encode() not in screen
    assert b"Tokencreated.Itwasnotshown" in screen
    assert vault.stat().st_mode & 0o777 == 0o600
    assert FAKE_TOKEN.encode() not in vault.read_bytes()

    read = Terminal([sys.executable, str(VAULT), "read", str(vault)], env)
    read.wait_for("Password")
    read.type("wrong\r")
    read.wait_for("Wrong password")
    read.type("hunter2\r")
    assert read.finish() == (0, FAKE_TOKEN)


def test_refuses_to_print_to_a_terminal(env, tmp_path):
    master, slave = pty.openpty()
    proc = subprocess.run([sys.executable, str(VAULT), "read", str(tmp_path / "x")],
                          env=env, stdout=slave, stderr=subprocess.PIPE)
    os.close(slave)
    os.close(master)
    assert proc.returncode == 1 and b"stdout is a terminal" in proc.stderr
