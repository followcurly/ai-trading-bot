from trader import allocation
from trader.allocation import pick_red_candidate_for_sleeve, sleeve_weights

from conftest import pos, sig


def test_sleeve_weights_sum_positions_per_sleeve(universe):
    positions = [pos("VOO", 300), pos("VTI", 100), pos("SCHD", 250), pos("QQQ", 350)]
    w = {r.sleeve.name: r for r in sleeve_weights(1000, positions, universe)}
    assert w["core"].weight == 0.40
    assert w["core"].underweight and round(w["core"].gap_pct, 6) == 0.10
    assert not w["dividend"].underweight  # exactly at target
    assert not w["growth"].underweight and w["growth"].gap_pct < 0


def test_zero_equity_does_not_divide_by_zero(universe):
    assert all(r.weight == 0 for r in sleeve_weights(0, [], universe))


def test_primary_preferred_when_red(universe):
    sw = sleeve_weights(1000, [], universe)[0]
    signals = {"VOO": sig("VOO", -0.01), "VTI": sig("VTI", -0.02)}
    assert pick_red_candidate_for_sleeve(sw, signals=signals).symbol == "VOO"


def test_falls_back_to_first_red_alternate(universe):
    sw = sleeve_weights(1000, [], universe)[0]
    signals = {"VOO": sig("VOO", 0.01), "VTI": sig("VTI", 0.0), "SPYM": sig("SPYM", -0.001)}
    assert pick_red_candidate_for_sleeve(sw, signals=signals).symbol == "SPYM"


def test_missing_price_is_not_red(universe):
    sw = sleeve_weights(1000, [], universe)[0]
    signals = {t: sig(t, None) for t in ("VOO", "VTI", "SPYM")}
    assert pick_red_candidate_for_sleeve(sw, signals=signals) is None


def test_overweight_sleeve_never_a_candidate(universe):
    sw = sleeve_weights(1000, [pos("VOO", 900)], universe)[0]
    assert pick_red_candidate_for_sleeve(sw, signals={"VOO": sig("VOO", -0.05)}) is None


def test_candidates_ordered_by_largest_gap(universe, monkeypatch):
    monkeypatch.setattr(allocation, "day_signal", lambda t: sig(t, -0.01))
    positions = [pos("VOO", 450), pos("SCHD", 50), pos("QQQ", 200)]
    cands = allocation.red_buy_candidates(1000, positions, universe)
    assert [c.sleeve.name for c in cands] == ["dividend", "core", "growth"]
