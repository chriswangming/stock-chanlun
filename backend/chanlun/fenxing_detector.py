"""Fenxing detector: K线包含处理 + 顶底分型识别（算法对齐 chan.py）。"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

import pandas as pd

from .base import (
    BOTTOM, COMBINE, DOWN, TOP, UP,
    MergedKLine, RawBar,
)


@dataclass
class Fenxing:
    """A top or bottom fractal."""

    date: datetime
    type: Literal["top", "bottom"]
    high: float
    low: float
    index: int                      # 合并后K线的序号（KLC idx）
    raw_index: int = -1             # 极值所在原始K线的序号
    klc_date: Optional[datetime] = None   # 合并K线的起始时间
    merged_count: int = 1           # 该合并K线包含多少根原始K线


class FenxingDetector:
    """
    检测缠论分型。

    流程与 chan.py 一致：
    1. 先做包含关系处理 —— 方向在「新K线加入时」由它与前一根的关系一次性确定
       （chan.py CKLine_Combiner.test_combine），而不是每次合并时重新推断；
    2. 再用相邻三根合并K线判定顶/底分型；
    3. 分型的时间取「区间内真正造出极值的那根原始K线」（chan.py get_peak_klu），
       这样笔的端点才会落在真实的转折点而不是被包含吞掉的最后一根。
    """

    def __init__(self, klines: pd.DataFrame):
        df = klines.reset_index(drop=True)
        self.raw_klines = df
        self.raws: list[RawBar] = []
        self.merged: list[MergedKLine] = []
        self._build(df)

    # ── 包含处理 ────────────────────────────────────────────
    def _build(self, df: pd.DataFrame):
        if df.empty:
            return

        dates = df["date"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        opens = df["open"].tolist()
        closes = df["close"].tolist()
        vols = df["volume"].tolist() if "volume" in df.columns else [0.0] * len(df)

        self.raws = []
        for i in range(len(df)):
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            # 丢掉缺失/非有限的脏数据：NaN 会污染包含关系判定（max/min 传播后整段失效）
            if any(v is None or v != v or v in (float("inf"), float("-inf"))
                   for v in (o, h, l, c)):
                continue
            o, h, l, c = float(o), float(h), float(l), float(c)
            if h < l:  # 个别数据源高低价颠倒
                h, l = l, h
            self.raws.append(
                RawBar(len(self.raws), self._as_datetime(dates[i]), o, h, l, c,
                       float(vols[i] or 0.0))
            )
        if not self.raws:
            return

        self.merged = [MergedKLine(self.raws[0], idx=0, _dir=UP)]
        for raw in self.raws[1:]:
            # try_add 返回 UP/DOWN 表示无需合并、需要新建一根合并K线，
            # 新K线的合并方向由这个返回值决定（chan.py add_single_klu）
            direction = self.merged[-1].try_add(raw)
            if direction != COMBINE:
                self.merged.append(MergedKLine(raw, idx=len(self.merged), _dir=direction))

        self._mark_fx()

    def _mark_fx(self):
        """每根合并K线的分型需要它的右邻居出现后才能确定（chan.py update_fx）。"""
        for i in range(1, len(self.merged) - 1):
            self.merged[i].update_fx(self.merged[i - 1], self.merged[i + 1])

    # ── 输出 ────────────────────────────────────────────────
    def detect(self) -> list[Fenxing]:
        """返回所有顶/底分型（按时间升序）。"""
        out: list[Fenxing] = []
        for klc in self.merged:
            if klc.fx not in (TOP, BOTTOM):
                continue
            is_high = klc.fx == TOP
            peak = klc.get_peak(is_high=is_high)
            out.append(
                Fenxing(
                    date=peak.date,
                    type=klc.fx,
                    high=float(klc.high),
                    low=float(klc.low),
                    index=klc.idx,
                    raw_index=peak.idx,
                    klc_date=klc.time_begin,
                    merged_count=len(klc.lst),
                )
            )
        return out

    @property
    def klines(self) -> pd.DataFrame:
        """处理包含关系后的 K 线 DataFrame（对外兼容旧接口）。"""
        return pd.DataFrame(
            [
                {
                    "date": m.time_begin,
                    "open": m.lst[0].open,
                    "high": m.high,
                    "low": m.low,
                    "close": m.lst[-1].close,
                    "volume": sum(float(r.volume or 0.0) for r in m.lst),
                }
                for m in self.merged
            ]
        )

    @staticmethod
    def _as_datetime(d) -> datetime:
        if hasattr(d, "to_pydatetime"):
            return d.to_pydatetime()
        return d
