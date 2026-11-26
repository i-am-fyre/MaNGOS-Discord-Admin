import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("MANGOS_DB_PASSWORD", "db-pass")
    monkeypatch.setenv("SOAP_USERNAME", "SOAPUSER")
    monkeypatch.setenv("SOAP_PASSWORD", "soap-pass")
    return os.environ


@pytest.fixture
def example_config(env, tmp_path):
    from mangosbot.config import load_config

    text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    text = text.replace("guild_id = 0 ", "guild_id = 123456789012345678 ", 1)
    text = text.replace('data_dir = "data"', f'data_dir = "{(tmp_path / "data").as_posix()}"', 1)
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(path)
