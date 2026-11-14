from __future__ import annotations

from configparser import ConfigParser
from pathlib import Path


def test_streamlit_server_address_is_localhost():
    config = ConfigParser()
    config.read(Path(__file__).resolve().parents[1] / ".streamlit" / "config.toml", encoding="utf-8")

    assert config.get("server", "address") == "127.0.0.1"
