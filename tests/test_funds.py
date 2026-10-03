from trader.funds import DEFAULT_FUNDS_PATH, load_funds


def test_tracked_funds_yaml_loads_and_targets_sum_to_one():
    assert DEFAULT_FUNDS_PATH.name == "funds.yaml"
    uni = load_funds(str(DEFAULT_FUNDS_PATH))
    assert {s.name for s in uni.sleeves} == {"core", "dividend", "growth"}
    assert abs(sum(s.target_pct for s in uni.sleeves) - 1.0) < 1e-9
    for s in uni.sleeves:
        assert s.primary in s.tickers


def test_splg_replaced_by_spym():
    tickers = load_funds(str(DEFAULT_FUNDS_PATH)).all_tickers()
    assert "SPLG" not in tickers
    assert "SPYM" in tickers
