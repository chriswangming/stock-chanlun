"""
笔检测器 — 基于分型识别笔。

链路位置::

    原始K线 → 包含处理 → 分型 → **笔** → 线段 → 中枢 → 买卖点/支撑阻力

本模块负责把「分型序列」整理成合规的「笔」：

1. ``compress_fenxings``：先把连续同类型的分型压缩掉（只留更极值的那个），
   得到严格顶底交替的序列；
2. ``detect``：再按「底→顶 = 向上笔、顶→底 = 向下笔」两两配对，
   并用最小K线数过滤掉过短、噪音化的笔。

Note:
    ``Fenxing.index`` / 分型日期来自**合并后序列**，而笔的 ``min_bars``
    长度校验走的是 ``self._date_values``，即**原始 klines 的日期**。
    这个「两个序列混用」是实现上最容易看错的地方，详见各函数注释。
"""
import numpy as np
import pandas as pd
from typing import Optional
from datetime import datetime
from .elements import Bi, KLine, MergedKLine
from .fenxing_detector import FenxingDetector, Fenxing


class BiDetector:
    """
    笔规则:
    1. 顶分型 + 底分型 = 一笔（向上笔: 底→顶，向下笔: 顶→底）
    2. 笔至少需要5根K线（含分型）
    3. 同级别笔由连续顶底分型构成
    """

    def __init__(self, klines: list[KLine]):
        """
        Args:
            klines: 原始K线序列（需按时间升序），元素为 :class:`KLine`。
                空列表是允许的，此时 ``detect()`` 直接返回空。

        Note:
            ``self.klines`` 与 ``self._date_values`` 都是**原始K线**；
            内部的 ``_fenxing_detector.klines`` / ``mer`` 才是包含合并后的序列。
            笔的长度校验用原始日期（``_date_values``），分型的下标用合并后
            序列，二者口径不同，不要互换。
        """
        # list(...) 拷贝一份，避免调用方后续改动原列表影响本对象
        self.klines: list[KLine] = list(klines)
        # 缓存日期数组供 searchsorted 使用（见 _count_klines_between）。
        # 走 pd.to_datetime 是为了让 dtype 固定为 datetime64[ns]，与原先直接取
        # DataFrame["date"].values 完全一致，避免 searchsorted 精度差异
        self._date_values = pd.to_datetime(pd.Series([k.date for k in self.klines], dtype="object")).values
        self._fenxing_detector = FenxingDetector(self.klines)
        self._fenxings: list[Fenxing] = []

    @staticmethod
    def compress_fenxings(fenxings: list[Fenxing]) -> list[Fenxing]:
        """
        压缩分型序列：连续同类型分型仅保留更“极值”的那个。

        - 连续 top：保留 high 更高者；
        - 连续 bottom：保留 low 更低者。

        Args:
            fenxings: 原始分型序列（按合并后序列下标升序）。

        Returns:
            严格顶底交替的分型序列（相邻两项 type 必不相同）。

        Note:
            为什么要压缩？分型检测只保证「局部三K线极值」，相邻分型完全可能
            是同类型（例如连续两个顶，只是后者略低）。而一笔的端点是顶底成对的，
            连续同类分型无法构成笔；保留更极值的那个，等价于保留这段波动的
            真实转折点。这里用 ``>=`` / ``<=``，即后来者与前者等值时也替换，
            使结果偏向**更晚**的那根（更贴近当下）。
        """
        if not fenxings:
            return []

        out: list[Fenxing] = [fenxings[0]]
        for fx in fenxings[1:]:
            last = out[-1]
            # 类型不同 → 顶底交替成立，直接接在后面
            if fx.type != last.type:
                out.append(fx)
                continue

            if fx.type == "top":
                # 连续两个顶：更高的那个才是真正的转折高点
                if fx.high >= last.high:
                    out[-1] = fx
            else:  # bottom
                # 连续两个底：更低的那个才是真正的转折低点
                if fx.low <= last.low:
                    out[-1] = fx

        return out

    def detect(self, min_bars: int = 5) -> list[Bi]:
        """
        检测序列中所有的笔。

        Args:
            min_bars: 一笔至少跨越的K线根数（默认 5）。这是缠论「笔」的经典约束
                —— 顶底之间必须至少间隔若干根K线，否则只是噪音级别的抖动。

        Returns:
            按时间顺序排列的笔列表。

        Note:
            配对规则：底分型 → 顶分型 = 向上笔；顶分型 → 底分型 = 向下笔。
            压缩后序列已严格顶底交替，因此正常情况下不会走到 else 分支。
            每轮只推进 ``i += 1``（而非 ``i += 2``），即窗口逐分型滑动，
            相邻两笔共享一个分型端点——这是笔的「连续衔接」语义。
        """
        # 先压缩成严格顶底交替的序列，再两两配对
        self._fenxings = self.compress_fenxings(self._fenxing_detector.detect())
        if len(self._fenxings) < 2:
            return []

        bis: list[Bi] = []
        i = 0

        while i < len(self._fenxings) - 1:
            fx1 = self._fenxings[i]
            fx2 = self._fenxings[i + 1]

            if fx1.type == "bottom" and fx2.type == "top":
                # 底 → 顶 = 向上笔：起点取底的低点，终点取顶的高点
                # 用原始K线日期统计跨度，不足 min_bars 则视为噪音丢弃
                bar_count = self._count_klines_between(fx1.date, fx2.date)
                if bar_count >= min_bars:
                    bis.append(Bi(
                        # ID 中的序号用 len(bis)+1，保证丢弃噪音笔后编号仍连续
                        id=f"bi_up_{len(bis)+1}",
                        start=fx1.date,
                        end=fx2.date,
                        direction="up",
                        high=float(fx2.high),
                        low=float(fx1.low),
                        start_price=float(fx1.low),
                        end_price=float(fx2.high),
                    ))
                i += 1

            elif fx1.type == "top" and fx2.type == "bottom":
                # 顶 → 底 = 向下笔：起点取顶的高点，终点取底的低点
                bar_count = self._count_klines_between(fx1.date, fx2.date)
                if bar_count >= min_bars:
                    bis.append(Bi(
                        id=f"bi_down_{len(bis)+1}",
                        start=fx1.date,
                        end=fx2.date,
                        direction="down",
                        high=float(fx1.high),
                        low=float(fx2.low),
                        start_price=float(fx1.high),
                        end_price=float(fx2.low),
                    ))
                # 滑动一格，下一轮以 fx2 作为新笔的起点
                i += 1
            else:
                # 理论上压缩后不会命中（连续同类分型已被压缩掉），留作兜底防死循环
                i += 1

        return bis

    def _count_klines_between(self, start: datetime, end: datetime) -> int:
        """
        计算 [start, end] 两个时间点之间（含两端）的K线根数。

        Args:
            start: 起始时间（取自起点分型的日期）
            end:   结束时间（取自终点分型的日期）

        Returns:
            该区间覆盖的K线根数；区间不合法时为 0。

        Note:
            这里用的是 ``self._date_values``，即**原始 klines 的日期**，
            而传入的 start/end 来自分型，分型是在**包含合并后序列**上算出来的。
            两个序列口径不同是有意为之：笔的「长度」应当按真实交易日的根数衡量，
            而不是按合并后那根更短的序列。切勿改成分型下标相减。
        """
        dates = self._date_values
        left = int(np.searchsorted(dates, np.datetime64(start), side="left")) # type: ignore
        right = int(np.searchsorted(dates, np.datetime64(end), side="right")) # type: ignore
        return max(0, right - left)
