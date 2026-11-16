from __future__ import annotations

import pytest

from src.risk import RiskConfig


def test_risk_config_rejects_plain_string_whitelist():
    with pytest.raises(ValueError, match="symbol_whitelist"):
        RiskConfig(symbol_whitelist="ACME")


def test_risk_config_accepts_iterable_string_whitelists():
    config_set = RiskConfig(symbol_whitelist={"ACME", "MSFT"})
    config_list = RiskConfig(symbol_whitelist=["ACME", "MSFT"])
    config_tuple = RiskConfig(symbol_whitelist=("ACME", "MSFT"))
    config_frozenset = RiskConfig(symbol_whitelist=frozenset({"ACME", "MSFT"}))

    assert config_set.symbol_whitelist == frozenset({"ACME", "MSFT"})
    assert config_list.symbol_whitelist == frozenset({"ACME", "MSFT"})
    assert config_tuple.symbol_whitelist == frozenset({"ACME", "MSFT"})
    assert config_frozenset.symbol_whitelist == frozenset({"ACME", "MSFT"})


def test_risk_config_allows_empty_iterable_whitelist():
    config = RiskConfig(symbol_whitelist=[])
    assert config.symbol_whitelist == frozenset()
