from trader import main
from trader.allocation import BuyCandidate, sleeve_weights

from conftest import sig


def _wire(monkeypatch, universe, *, cash):
    weights = sleeve_weights(1000, [], universe)
    cands = [
        BuyCandidate(sleeve=w.sleeve, symbol=w.sleeve.primary, signal=sig(w.sleeve.primary, -0.01), sleeve_weight=w)
        for w in weights
    ]
    holds, buys = [], []
    monkeypatch.setattr(main, "load_funds", lambda: universe)
    monkeypatch.setattr(main, "_account_snapshot", lambda: {
        "account": {"equity": 1000.0, "cash": cash}, "positions": [], "equity": 1000.0, "cash": cash,
    })
    monkeypatch.setattr(main, "_todays_placed_buys_et", lambda now: (set(), set()))
    monkeypatch.setattr(main, "red_buy_candidates", lambda *a: cands)
    monkeypatch.setattr(main, "day_signal", lambda t: sig(t, 0.01))
    monkeypatch.setattr(main, "_journal_hold", lambda sym, snap, reason, meta: holds.append((sym, meta)))
    monkeypatch.setattr(main, "_journal_buy", lambda *a: None)

    def fake_buy(sym, *, equity, cash, dry_run):
        buys.append(sym)
        return {"status": "placed", "notional": min(cash, equity * 0.05)}

    monkeypatch.setattr(main, "market_buy_notional", fake_buy)
    return holds, buys


def test_fully_invested_journals_no_cash_holds(monkeypatch, universe):
    holds, buys = _wire(monkeypatch, universe, cash=0.0)
    out = main.run_cycle(force=True)
    assert buys == []
    assert out["skipped_no_cash"] == 3
    assert [m["skip"] for _, m in holds] == ["no_cash"] * 3


def test_cash_runs_out_mid_cycle(monkeypatch, universe):
    holds, buys = _wire(monkeypatch, universe, cash=60.0)  # 50 + 10 → third has 0 left
    out = main.run_cycle(force=True)
    assert buys == ["VOO", "SCHD"]
    assert out["buys"] == 2 and out["skipped_no_cash"] == 1
    assert holds[-1][0] == "QQQM" and holds[-1][1]["skip"] == "no_cash"
