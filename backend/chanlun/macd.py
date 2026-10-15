"""
MACD / 力度指标 —— 移植自 chan.py 的 Math/MACD.py、Math/RSI.py 与 Bi/Bi.py 的
cal_macd_metric 系列方法。

chan.py 在每根原始K线（CKLine_Unit）上挂一个 macd 指标，笔的「力度」就是按
`macd_algo` 在这笔覆盖的K线区间上做聚合：

    area      : 从笔起点（正向）/ 终点（反向）开始，累加同号 MACD 柱面积
    peak      : 区间内与笔方向同号的 MACD 柱最大绝对值
    full_area : 区间内与笔方向同号的 MACD 柱面积之和
    diff      : 区间内 MACD 柱最大值 - 最小值
    slope     : 价格幅度 / 高点 / K线根数
    amp       : 价格幅度 / 起点价
    volume    : 区间成交量之和
    rsi       : RSI 极值（下跌笔取最小值的倒数，上涨笔取最大值）

默认算法与 chan.py 一致：peak。
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence

MACD_ALGOS = ("area", "peak", "full_area", "diff", "slope", "amp", "volume", "rsi")

_EPS = 1e-7


class MACDSeries:
    """按原始K线下标索引的 MACD（及成交量/RSI）序列。"""

    def __init__(self, dif: Sequence[float], dea: Sequence[float], bar: Sequence[float],
                 volume: Optional[Sequence[float]] = None,
                 rsi: Optional[Sequence[float]] = None):
        self.dif = list(dif)
        self.dea = list(dea)
        self.bar = list(bar)
        self.volume = list(volume) if volume is not None else []
        self.rsi = list(rsi) if rsi is not None else []

    def __len__(self) -> int:
        return len(self.bar)

    # ── 构造 ────────────────────────────────────────────
    @classmethod
    def from_klines(cls, bars: Iterable, fast: int = 12, slow: int = 26,
                  signal: int = 9, rsi_period: int = 14) -> "MACDSeries":
        """
        bars: 具备 .close / .volume 的原始K线（chan.py CKLine_Unit）。
        MACD 采用 chan.py Math/MACD.py 的递推 EMA（首根 DIF=DEA=0）。
        """
        bars = list(bars)
        closes = [float(getattr(b, "close", 0.0) or 0.0) for b in bars]
        vols = [float(getattr(b, "volume", 0.0) or 0.0) for b in bars]
        difs: list[float] = []
        deas: list[float] = []
        bars_out: list[float] = []
        fast_ema = slow_ema = dea = 0.0
        for i, close in enumerate(closes):
            if i == 0:
                fast_ema = slow_ema = close
                dif = dea = 0.0
            else:
                fast_ema = (2 * close + (fast - 1) * fast_ema) / (fast + 1)
                slow_ema = (2 * close + (slow - 1) * slow_ema) / (slow + 1)
                dif = fast_ema - slow_ema
                dea = (2 * dif + (signal - 1) * dea) / (signal + 1)
            difs.append(dif)
            deas.append(dea)
            bars_out.append(2 * (dif - dea))
        return cls(difs, deas, bars_out, vols, _calc_rsi(closes, rsi_period))


def _calc_rsi(closes: Sequence[float], period: int = 14) -> list[float]:
    """chan.py Math/RSI.py 的实现（逐根推进版）。"""
    out: list[float] = []
    diff: list[float] = []
    ups: list[float] = []
    downs: list[float] = []
    for i, c in enumerate(closes):
        if i == 0:
            out.append(50.0)
            continue
        diff.append(float(c) - float(closes[i - 1]))
        if len(diff) < period:
            up_sum = sum(x for x in diff if x > 0)
            down_sum = sum(-x for x in diff if x < 0)
            ups.append(up_sum / len(diff))
            downs.append(down_sum / len(diff))
        else:
            upval = diff[-1] if diff[-1] > 0 else 0.0
            downval = -diff[-1] if diff[-1] < 0 else 0.0
            ups.append((ups[-1] * (period - 1) + upval) / period)
            downs.append((downs[-1] * (period - 1) + downval) / period)
        if downs[-1] == 0:
            out.append(100.0 if ups[-1] > 0 else 0.0)
            continue
        rs = ups[-1] / downs[-1]
        out.append(100.0 - 100.0 / (1.0 + rs))
    return out


def _clip(series: MACDSeries, idx: int) -> int:
    if idx < 0:
        return 0
    n = len(series)
    return idx if idx < n else max(n - 1, 0)


def cal_macd_metric(series: Optional[MACDSeries], line, macd_algo: str,
                    is_reverse: bool = False) -> float:
    """
    计算一笔的力度指标 —— 对应 chan.py CBi.cal_macd_metric。

    line 需要提供：is_down()、_high()/_low()、begin_klu_idx/end_klu_idx（顶点K线）
    以及 klc_begin_idx/klc_end_idx（笔覆盖的首尾K线）。
    """
    if series is None or len(series) == 0:
        return _EPS

    lo_full = _clip(series, line.klc_begin_idx)
    hi_full = _clip(series, line.klc_end_idx)
    lo_peak = _clip(series, line.begin_klu_idx)
    hi_peak = _clip(series, line.end_klu_idx)
    if hi_full < lo_full:
        lo_full, hi_full = hi_full, lo_full
    if hi_peak < lo_peak:
        lo_peak, hi_peak = hi_peak, lo_peak
    is_down = line.is_down()

    if macd_algo == "area":
        return _metric_area(series, lo_peak, hi_peak, is_reverse)
    if macd_algo == "full_area":
        total = _EPS
        for i in range(lo_peak, hi_peak + 1):
            m = series.bar[i]
            if (is_down and m < 0) or (not is_down and m > 0):
                total += abs(m)
        return total
    if macd_algo == "peak":
        peak = _EPS
        for i in range(lo_full, hi_full + 1):
            m = series.bar[i]
            if is_down and m < 0 and abs(m) > peak:
                peak = abs(m)
            elif not is_down and m > 0 and abs(m) > peak:
                peak = abs(m)
        return peak
    if macd_algo == "diff":
        return max(series.bar[lo_full:hi_full + 1]) - min(series.bar[lo_full:hi_full + 1])
    if macd_algo == "slope":
        high, low = float(line._high()), float(line._low())
        span = max(hi_peak - lo_peak + 1, 1)
        return (high - low) / high / span if high else _EPS
    if macd_algo == "amp":
        high, low = float(line._high()), float(line._low())
        return (high - low) / high if (is_down and high) else ((high - low) / low if low else _EPS)
    if macd_algo == "volume":
        return sum(series.volume[lo_full:hi_full + 1]) if series.volume else _EPS
    if macd_algo == "rsi":
        if not series.rsi:
            return _EPS
        window = series.rsi[lo_full:hi_full + 1]
        return 10000.0 / (min(window) + _EPS) if is_down else max(window)
    raise ValueError(f"unsupported macd_algo={macd_algo}, should be one of {MACD_ALGOS}")


def _metric_area(series: MACDSeries, lo: int, hi: int, is_reverse: bool) -> float:
    """chan.py Cal_MACD_half_obverse / Cal_MACD_half_reverse。"""
    total = _EPS
    if not is_reverse:
        peak = series.bar[lo]
        for i in range(lo, hi + 1):
            m = series.bar[i]
            if m * peak > 0:
                total += abs(m)
            else:
                break
        return total
    peak = series.bar[hi]
    for i in range(hi, lo - 1, -1):
        m = series.bar[i]
        if m * peak > 0:
            total += abs(m)
        else:
            break
    return total
