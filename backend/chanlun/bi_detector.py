"""
笔检测器 —— 移植自 chan.py 的 Bi/BiList.py。

与 chan.py 的唯一结构性差别：chan.py 是流式（逐根K线增量驱动，支持虚笔），
这里改成批处理（一次性拿到全部合并K线后顺序推进）。推进顺序严格对应 chan.py 的
`CKLine_List.add_single_klu`：每加入第 i 根合并K线，就先给第 i-1 根定分型，
再用 `update_bi_sure(第 i-1 根)` 更新笔；虚笔（virtual bi）在批处理场景无意义，
因此未实现。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from .base import (
    DOWN, TOP, UP, BOTTOM,
    CBi, MergedKLine, RawBar, end_is_peak,
)
from .config import BiConfig, ChanlunConfig
from .elements import Bi
from .fenxing_detector import Fenxing, FenxingDetector


def _as_datetime(d) -> datetime:
    if hasattr(d, "to_pydatetime"):
        return d.to_pydatetime()
    return d


class BiDetector:
    """
    笔规则（chan.py 口径）:
    1. 一笔连接相邻的顶/底分型，方向相反且互不重叠（`check_fx_valid`）；
    2. 两端之间至少间隔若干根合并K线（`satisfy_bi_span`，严格笔默认 >=4）；
    3. 笔的端点必须是区间内的极值（`end_is_peak`）；
    4. 若新分型与上一笔同向且更极端，则更新上一笔终点而非新增一笔（`try_update_end`）。
    """

    virtual_bi: bool = True   # 是否在尾部补一笔未确认的虚笔（chan.py 默认行为）

    def __init__(self, klines: pd.DataFrame, config: Optional[ChanlunConfig] = None):
        self.klines = klines.reset_index(drop=True)
        self.config = (config or ChanlunConfig()).bi
        self._virtual_enabled_off = not self.virtual_bi
        self._fenxing_detector = FenxingDetector(self.klines)
        self._merged: list[MergedKLine] = self._fenxing_detector.merged
        self._bis: list[CBi] = []
        self._last_end: Optional[MergedKLine] = None
        self._free_klc: list[MergedKLine] = []

    # ── 对外属性 ────────────────────────────────────────────
    @property
    def merged_klines(self) -> list[MergedKLine]:
        """包含处理后的合并K线序列。"""
        return self._merged

    @property
    def processed_klines(self) -> pd.DataFrame:
        return self._fenxing_detector.klines

    @property
    def bis_internal(self) -> list[CBi]:
        """内部笔对象（供线段/中枢模块消费，带 pre/next 与所属括线信息）。"""
        return self._bis

    @property
    def raw_bars(self) -> list[RawBar]:
        """清洗后的原始K线（下标即 RawBar.idx，MACD 序列按此顺序对齐）。"""
        return self._fenxing_detector.raws

    # ── 主流程 ──────────────────────────────────────────────
    def detect(self) -> list[Bi]:
        """
        检测所有笔。
        """
        self._bis = []
        self._last_end = None
        self._free_klc = []

        # 逐根推进：chan.py 在「第 i 根合并K线诞生」的那一刻，才给第 i-1 根定分型并更新笔。
        # 关键是那一刻第 i 根还只有它自己的第一根原始K线，后续被包含吞并而变高之前，
        # 判定用的是它的初始快照 —— 所以这里临时换成 init_ 高/低再判定。
        for i in range(2, len(self._merged)):
            front = self._merged[i]
            fh, fl = front.high, front.low
            front.high, front.low = front.init_high, front.init_low
            try:
                klc = self._merged[i - 1]
                klc.update_fx(self._merged[i - 2], front)
                if klc.fx in (TOP, BOTTOM):
                    self._update_bi_sure(klc)
            finally:
                front.high, front.low = fh, fl

        # 开头那几根：第 1 根无需处理（没有左邻居），第 2 根在 i=2 时已处理

        # chan.py CKLine_List.cal_seg_and_zs：全部K线算完后，把「正在走的那一段」
        # 也补成一笔（虚笔），否则最后一段未完成的下跌/上涨不会出现在结果里
        if self._merged and not self._virtual_enabled_off:
            self._try_add_virtual_bi(self._merged[-1])

        return self._to_models()

    # ── chan.py CBiList 的方法移植 ──────────────────────────
    def _update_bi_sure(self, klc: MergedKLine) -> bool:
        if self._last_end is None or len(self._bis) == 0:
            return self._try_create_first_bi(klc)
        if klc.fx == self._last_end.fx:
            return self._try_update_end(klc)
        if self._can_make_bi(klc, self._last_end):
            self._add_new_bi(self._last_end, klc)
            self._last_end = klc
            return True
        if self._update_peak(klc):
            return True
        return False

    def _try_create_first_bi(self, klc: MergedKLine) -> bool:
        for exist_free in self._free_klc:
            if exist_free.fx == klc.fx:
                continue
            if self._can_make_bi(klc, exist_free):
                self._add_new_bi(exist_free, klc)
                self._last_end = klc
                return True
        self._free_klc.append(klc)
        self._last_end = klc
        return False

    def _add_new_bi(self, pre_klc: MergedKLine, cur_klc: MergedKLine, is_sure: bool = True):
        try:
            bi = CBi(pre_klc, cur_klc, idx=len(self._bis), is_sure=is_sure)
        except ValueError:
            # 方向与首尾极值不一致（异常数据），跳过而不是让整个分析崩掉
            return None
        self._bis.append(bi)
        if len(self._bis) >= 2:
            self._bis[-2].next = self._bis[-1]
            self._bis[-1].pre = self._bis[-2]
        return bi

    # ── 虚笔（chan.py try_add_virtual_bi / delete_virtual_bi） ──
    def _try_add_virtual_bi(self, klc: MergedKLine) -> bool:
        if len(self._bis) == 0:
            return False
        if klc.idx == self._bis[-1].end_klc.idx:
            return False
        last_bi = self._bis[-1]
        if ((last_bi.is_up() and klc.high >= last_bi.end_klc.high)
                or (last_bi.is_down() and klc.low <= last_bi.end_klc.low)):
            # 直接延长最后一笔
            return last_bi.update_virtual_end(klc)

        _tmp_klc: Optional[MergedKLine] = klc
        while _tmp_klc is not None and _tmp_klc.idx > last_bi.end_klc.idx:
            if self._can_make_bi(_tmp_klc, last_bi.end_klc, for_virtual=True):
                self._add_new_bi(last_bi.end_klc, _tmp_klc, is_sure=False)
                return True
            if self._update_peak(_tmp_klc, for_virtual=True):
                return True
            _tmp_klc = _tmp_klc.pre
        return False

    # ── 跨度 / 有效性 ───────────────────────────────────────
    def _get_klc_span(self, klc: MergedKLine, last_end: MergedKLine) -> int:
        span = klc.idx - last_end.idx
        if not self.config.gap_as_kl:
            return span
        if span >= 4:  # 加速：已经够长就无需精确值
            return span
        tmp = last_end
        while tmp is not None and tmp.idx < klc.idx:
            if tmp.next is not None and tmp.has_gap_with_next():
                span += 1
            tmp = tmp.next
        return span

    def _satisfy_bi_span(self, klc: MergedKLine, last_end: MergedKLine) -> bool:
        span = self._get_klc_span(klc, last_end)
        if self.config.is_strict:
            return span >= 4
        unit_kl_cnt = 0
        tmp = last_end.next
        while tmp is not None:
            unit_kl_cnt += len(tmp.lst)
            if tmp.next is None:
                return False
            if tmp.next.idx < klc.idx:
                tmp = tmp.next
            else:
                break
        return span >= 3 and unit_kl_cnt >= 3

    def _can_make_bi(self, klc: MergedKLine, last_end: MergedKLine,
                     for_virtual: bool = False) -> bool:
        if self.config.bi_algo != "fx" and not self._satisfy_bi_span(klc, last_end):
            return False
        if not last_end.check_fx_valid(klc, self.config.bi_fx_check, for_virtual=for_virtual):
            return False
        if self.config.bi_end_is_peak and not end_is_peak(last_end, klc):
            return False
        return True

    # ── 端点更新 ────────────────────────────────────────────
    def _try_update_end(self, klc: MergedKLine, for_virtual: bool = False) -> bool:
        if len(self._bis) == 0:
            return False
        last_bi = self._bis[-1]
        if for_virtual:
            # 虚笔时对手方不要求是已确认分型，只看方向
            same_dir = (last_bi.is_up() and klc.dir == UP) or (last_bi.is_down() and klc.dir == DOWN)
            if not same_dir:
                return False
            if (last_bi.is_up() and klc.high < last_bi.get_end_val()) or \
               (last_bi.is_down() and klc.low > last_bi.get_end_val()):
                return False
            return last_bi.update_virtual_end(klc)
        if ((last_bi.is_up() and klc.fx == TOP and klc.high >= last_bi.get_end_val())
                or (last_bi.is_down() and klc.fx == BOTTOM and klc.low <= last_bi.get_end_val())):
            if not last_bi.update_new_end(klc):
                return False
            self._last_end = klc
            return True
        return False

    def _can_update_peak(self, klc: MergedKLine) -> bool:
        if self.config.bi_allow_sub_peak or len(self._bis) < 2:
            return False
        if self._bis[-1].is_down() and klc.high < self._bis[-1].get_begin_val():
            return False
        if self._bis[-1].is_up() and klc.low > self._bis[-1].get_begin_val():
            return False
        if not end_is_peak(self._bis[-2].begin_klc, klc):
            return False
        if self._bis[-1].is_down() and self._bis[-1].get_end_val() < self._bis[-2].get_begin_val():
            return False
        if self._bis[-1].is_up() and self._bis[-1].get_end_val() > self._bis[-2].get_begin_val():
            return False
        return True

    def _update_peak(self, klc: MergedKLine, for_virtual: bool = False) -> bool:
        if not self._can_update_peak(klc):
            return False
        tmp_last_bi = self._bis.pop()
        if not self._try_update_end(klc, for_virtual=for_virtual):
            self._bis.append(tmp_last_bi)
            return False
        self._relink()
        return True

    def _relink(self):
        for i, bi in enumerate(self._bis):
            bi.idx = i
            bi.pre = self._bis[i - 1] if i > 0 else None
            bi.next = self._bis[i + 1] if i + 1 < len(self._bis) else None

    # ── 输出转换 ────────────────────────────────────────────
    def _to_models(self) -> list[Bi]:
        out: list[Bi] = []
        for bi in self._bis:
            begin_klu: RawBar = bi.get_begin_klu()
            end_klu: RawBar = bi.get_end_klu()
            direction = "up" if bi.is_up() else "down"
            out.append(
                Bi(
                    id=f"bi_{'up' if direction == 'up' else 'down'}_{len(out) + 1}",
                    start=begin_klu.date,
                    end=end_klu.date,
                    direction=direction,
                    high=float(bi._high()),
                    low=float(bi._low()),
                    start_price=float(bi.get_begin_val()),
                    end_price=float(bi.get_end_val()),
                    is_sure=bool(bi.is_sure),
                    used_to_be_sure=bool(bi.used_to_be_sure),
                    # 端点所在的原始K线下标：买卖点算力度（MACD 面积/峰值）时需要
                    start_idx=int(begin_klu.idx),
                    end_idx=int(end_klu.idx),
                    klc_start_idx=int(bi.begin_klc.lst[0].idx),
                    klc_end_idx=int(bi.end_klc.lst[-1].idx),
                )
            )
        return out
