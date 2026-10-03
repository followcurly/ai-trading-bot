from trader import executor_simple
from trader.executor_simple import market_buy_notional


def test_notional_is_pct_of_equity_capped_by_cash():
    r = market_buy_notional("voo", equity=100_000, cash=1_000, buy_pct=0.05, dry_run=True)
    assert r == {"status": "skipped", "reason": "dry_run", "notional": 1000.0, "symbol": "VOO"}


def test_below_min_notional_skips_without_calling_broker(monkeypatch):
    def boom():
        raise AssertionError("broker must not be called")

    monkeypatch.setattr(executor_simple, "trading_client", boom)
    r = market_buy_notional("VOO", equity=100_000, cash=0.0, buy_pct=0.05)
    assert r["status"] == "skipped" and r["reason"].startswith("notional_too_small")


def test_broker_error_is_reported_not_raised(monkeypatch):
    class Client:
        def submit_order(self, req):
            raise RuntimeError('{"message":"asset \\"SPLG\\" not found"}')

    monkeypatch.setattr(executor_simple, "trading_client", lambda: Client())
    r = market_buy_notional("SPLG", equity=1000, cash=1000, buy_pct=0.05)
    assert r["status"] == "error" and "not found" in r["reason"]
