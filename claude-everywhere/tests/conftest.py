import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from anthropic import Anthropic
from openai import OpenAI

FAKE_CLAUDE = Path(__file__).with_name("fake_claude.py")
API_KEY = "test-key"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, tmp: Path, claude_bin: str, extra_env: dict | None = None):
        self.log = tmp / "calls.jsonl"
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        env = {
            **os.environ,
            "CLAUDE_EVERYWHERE_CLAUDE_BIN": claude_bin,
            "CLAUDE_EVERYWHERE_WORKDIR": str(tmp / "workdir"),
            "CLAUDE_EVERYWHERE_DB": str(tmp / "sessions.db"),
            "CLAUDE_EVERYWHERE_API_KEY": API_KEY,
            "CLAUDE_EVERYWHERE_PORT": str(self.port),
            "FAKE_CLAUDE_STATE": str(tmp / "fake-state"),
            "FAKE_CLAUDE_LOG": str(self.log),
            **(extra_env or {}),
        }
        self.proc = subprocess.Popen(
            [sys.executable, "-c", "from claude_everywhere.app import main; main()"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            try:
                httpx.get(f"{self.url}/health").raise_for_status()
                break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            raise RuntimeError("server did not start")
        self.client = OpenAI(base_url=f"{self.url}/v1", api_key=API_KEY, max_retries=0)
        self.anthropic = Anthropic(base_url=self.url, api_key=API_KEY, max_retries=0)

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def stop(self):
        self.proc.terminate()
        self.proc.wait(5)


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path, str(FAKE_CLAUDE))
    yield s
    s.stop()
