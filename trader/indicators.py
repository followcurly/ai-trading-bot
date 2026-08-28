"""Technical indicators without pandas-ta (RSI, MACD, EMA, ATR, Bollinger)."""

from __future__ import annotations

import pandas as pd


def add_indicators(bars: pd.DataFrame) -> pd.DataFrame:
    """Expects columns: open, high, low, close, volume. Mutates copy."""
    df = bars.copy()
    c = df["close"]
    h = df["high"]
    l = df["low"]
    vol = df["volume"] if "volume" in df.columns else pd.Series(0.0, index=df.index)

    delta = c.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(span=14, adjust=False).mean()
    avg_loss = loss.ewm(span=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    df["RSI_14"] = 100 - (100 / (1 + rs))

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["MACD_12_26_9"] = ema12 - ema26
    df["MACDs_12_26_9"] = df["MACD_12_26_9"].ewm(span=9, adjust=False).mean()

    df["EMA_20"] = c.ewm(span=20, adjust=False).mean()
    df["EMA_50"] = c.ewm(span=50, adjust=False).mean()

    prev_close = c.shift(1)
    tr = pd.concat(
        [
            h - l,
            (h - prev_close).abs(),
            (l - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = tr.ewm(span=14, adjust=False).mean()
    df["ATRr_14"] = atr14

    up_move = h.diff()
    down_move = -l.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    atr_safe = atr14.replace(0, float("nan"))
    plus_di = 100.0 * (plus_dm.ewm(span=14, adjust=False).mean() / atr_safe)
    minus_di = 100.0 * (minus_dm.ewm(span=14, adjust=False).mean() / atr_safe)
    di_sum = (plus_di + minus_di).replace(0, float("nan"))
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    df["ADX_14"] = dx.ewm(span=14, adjust=False).mean()

    rsi_col = df["RSI_14"]
    rsi_min = rsi_col.rolling(14).min()
    rsi_max = rsi_col.rolling(14).max()
    denom = (rsi_max - rsi_min).replace(0, float("nan"))
    st_k = ((rsi_col - rsi_min) / denom) * 100.0
    df["StochRSI_k"] = st_k
    df["StochRSI_d"] = st_k.rolling(3).mean()

    chg1 = c.diff()
    direction = (chg1 > 0).astype(float) - (chg1 < 0).astype(float)
    df["OBV"] = (direction * vol).fillna(0.0).cumsum()

    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    df["BBU_20_2.0"] = mid + 2 * std
    df["BBL_20_2.0"] = mid - 2 * std

    vol_ma20 = vol.rolling(20).mean().replace(0, float("nan"))
    df["vol_ratio_20"] = vol / vol_ma20

    return df
