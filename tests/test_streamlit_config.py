from __future__ import annotations

import tomllib
from pathlib import Path


def test_streamlit_server_address_is_localhost():
    config = tomllib.loads(
        (Path(__file__).resolve().parents[1] / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    )

    assert config["server"]["address"] == "127.0.0.1"
