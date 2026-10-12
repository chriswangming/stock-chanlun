"""
缠论基础构件 —— 移植自 chan.py 的同名实现：

    Common/func_util.py          -> has_overlap
    Combiner/Combine_Item.py     -> 元素 high/low 适配器（此处由 _Combiner 的钩子替代）
    Combiner/KLine_Combiner.py   -> _Combiner  (包含关系合并)
    KLine/KLine.py               -> MergedKLine.check_fx_valid
    Bi/Bi.py                     -> CBi        (笔的内部表示)
    Bi/BiList.py                 -> end_is_peak

与 chan.py 的对应关系保持 1:1，方便两个仓库后续对照排查。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Union

# ── 方向常量 ────────────────────────────────────────────────
UP = "up"
DOWN = "down"
COMBINE = "combine"
INCLUDED = "included"

TOP = "top"
BOTTOM = "bottom"


def has_overlap(l1: float, h1: float, l2: float, h2: float, equal: bool = False) -> bool:
    """区间 [l1,h1] 与 [l2,h2] 是否有重叠；equal=True 时端点相等也算重叠。"""
    return (h2 >= l1 and h1 >= l2) if equal else (h2 > l1 and h1 > l2)


# ── 原始 K 线 ───────────────────────────────────────────────
class RawBar:
    """未做包含处理的单根 K 线（对应 chan.py 的 CKLine_Unit）。"""

    __slots__ = ("idx", "date", "open", "high", "low", "close", "volume")

    def __init__(self, idx: int, date: datetime, open_: float, high: float,
                 low: float, close: float, volume: float = 0.0):
        self.idx = idx
        self.date = date
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume

    def __repr__(self):  # pragma: no cover - 调试用
        return f"RawBar({self.idx},{self.date},{self.low}~{self.high})"


# ── 通用包含合并器 ──────────────────────────────────────────
class _Combiner:
    """
    chan.py CKLine_Combiner 的通用移植。

    子类只需实现 `_item_high` / `_item_low`，即可让「合并后的K线」与
    「特征序列元素(Eigen)」共用同一套包含处理逻辑——这正是 chan.py 里
    CEigen(CKLine_Combiner[CBi]) 的做法。
    """

    def __init__(self, first, _dir: str = UP):
        self.dir: str = _dir
        self.high: float = self._item_high(first)
        self.low: float = self._item_low(first)
        self.lst: list = [first]
        self.fx: Optional[str] = None
        self.pre = None
        self.next = None

    # ── 元素适配钩子 ─────────────────────────────
    def _item_high(self, x) -> float:
        raise NotImplementedError

    def _item_low(self, x) -> float:
        raise NotImplementedError

    # ── 包含关系判定 ─────────────────────────────
    def test_combine(self, item, exclude_included: bool = False,
                     allow_top_equal: Optional[int] = None) -> str:
        h = self._item_high(item)
        l = self._item_low(item)
        if self.high >= h and self.low <= l:          # 当前包含新元素
            return COMBINE
        if self.high <= h and self.low >= l:          # 新元素包含当前
            if allow_top_equal == 1 and self.high == h and self.low > l:
                return DOWN
            if allow_top_equal == -1 and self.low == l and self.high < h:
                return UP
            return INCLUDED if exclude_included else COMBINE
        if self.high > h and self.low > l:
            return DOWN
        if self.high < h and self.low < l:
            return UP
        raise ValueError("combine type unknown")

    def try_add(self, item, exclude_included: bool = False,
                allow_top_equal: Optional[int] = None,
                skip_update_input: bool = False) -> str:
        """
        尝试把 item 并入当前整理对象。
        返回 COMBINE（已合并）/ UP / DOWN（无法合并，指示新对象的方向）。
        """
        _dir = self.test_combine(item, exclude_included, allow_top_equal)
        if _dir == COMBINE:
            self.lst.append(item)
            h = self._item_high(item)
            l = self._item_low(item)
            if self.dir == UP:
                # 一字K线(high==low)且不再创新高时不参与合并，否则会把区间单边拉宽
                if h != l or h != self.high:
                    self.high = max(self.high, h)
                    self.low = max(self.low, l)
            elif self.dir == DOWN:
                if h != l or l != self.low:
                    self.high = min(self.high, h)
                    self.low = min(self.low, l)
            else:
                raise ValueError(f"dir must be up/down, got {self.dir}")
        return _dir

    def get_peak(self, is_high: bool):
        """返回造成当前最高/最低点的那个原始元素（chan.py get_high/low_peak_klu）。"""
        target = self.high if is_high else self.low
        for x in reversed(self.lst):
            v = self._item_high(x) if is_high else self._item_low(x)
            if v == target:
                return x
        raise ValueError("can't find peak")

    def update_fx(self, pre, nxt, exclude_included: bool = False,
                  allow_top_equal: Optional[int] = None):
        """根据左右邻居判定顶/底分型。"""
        self.next = nxt
        self.pre = pre
        if nxt is not None:
            nxt.pre = self
        if exclude_included:
            if pre.high < self.high and nxt.high <= self.high and nxt.low < self.low:
                if allow_top_equal == 1 or nxt.high < self.high:
                    self.fx = TOP
            elif nxt.high > self.high and pre.low > self.low and nxt.low >= self.low:
                if allow_top_equal == -1 or nxt.low > self.low:
                    self.fx = BOTTOM
        elif (pre.high < self.high and nxt.high < self.high
              and pre.low < self.low and nxt.low < self.low):
            self.fx = TOP
        elif (pre.high > self.high and nxt.high > self.high
              and pre.low > self.low and nxt.low > self.low):
            self.fx = BOTTOM

    def __len__(self):
        return len(self.lst)

    def __getitem__(self, i):
        return self.lst[i]

    def __iter__(self):
        yield from self.lst


# ── 合并后的 K 线 ───────────────────────────────────────────
class MergedKLine(_Combiner):
    """做过包含关系处理的 K 线（对应 chan.py 的 CKLine）。"""

    def __init__(self, raw: RawBar, idx: int, _dir: str = UP):
        super().__init__(raw, _dir)
        self.idx = idx
        # 本K线刚创建时的高低点。chan.py 是流式的：当它是「最新一根」时判定的
        # 分型/笔用的是当时的值，之后它再吞并新K线变高也不会回头重算。
        # 批处理想拿到同样的结果，就必须在这类判定里用这份快照。
        self.init_high = raw.high
        self.init_low = raw.low

    # _Combiner 钩子：元素是原始K线
    def _item_high(self, x: RawBar) -> float:
        return x.high

    def _item_low(self, x: RawBar) -> float:
        return x.low

    @property
    def time_begin(self) -> datetime:
        return self.lst[0].date

    @property
    def time_end(self) -> datetime:
        return self.lst[-1].date

    def get_klu_max_high(self) -> float:
        return max(x.high for x in self.lst)

    def get_klu_min_low(self) -> float:
        return min(x.low for x in self.lst)

    def has_gap_with_next(self) -> bool:
        """与本K线和下一根之间是否存在跳空缺口（gap_as_kl 时算作一根K线）。"""
        assert self.next is not None
        # 相同也算重叠，也就是没有 gap
        return not has_overlap(
            self.get_klu_min_low(), self.get_klu_max_high(),
            self.next.get_klu_min_low(), self.next.get_klu_max_high(),
            equal=True,
        )

    def check_fx_valid(self, item2: "MergedKLine", method: str,
                       for_virtual: bool = False) -> bool:
        """
        判断 self 与 item2 这对顶底分型是否能独立成笔（互不重叠）。

        method 语义同 chan.py：
          strict(默认)：底分型的最低点必须比顶分型3元素最低点的最小值还低，顶分型反之
          totally     ：底分型3元素的最高点必须比顶分型三元素的最低点还低
          loss        ：底分型的最低点比顶分型中间元素低点还低
          half        ：上升笔时底分型最低点 < 顶分型「前两元素」最低点，反之亦然
        for_virtual=True 时用于虚笔：对手方只需方向正确（不要求是已确认分型）。
        """
        if self.pre is None or self.next is None or item2.pre is None:
            return False
        if self.fx == TOP:
            if not for_virtual and item2.fx != BOTTOM:
                return False
            if for_virtual and item2.dir != DOWN:
                return False
            if method == "half":
                item2_high = max(item2.pre.high, item2.high)
                self_low = min(self.low, self.next.low)
            elif method == "loss":
                item2_high = item2.high
                self_low = self.low
            elif method in ("strict", "totally"):
                if for_virtual:
                    item2_high = max(item2.pre.high, item2.high)
                else:
                    if item2.next is None:
                        return False
                    item2_high = max(item2.pre.high, item2.high, item2.next.high)
                self_low = min(self.pre.low, self.low, self.next.low)
            else:
                raise ValueError(f"unknown bi_fx_check={method}")
            if method == "totally":
                return self.low > item2_high
            return self.high > item2_high and item2.low < self_low

        if self.fx == BOTTOM:
            if not for_virtual and item2.fx != TOP:
                return False
            if for_virtual and item2.dir != UP:
                return False
            if method == "half":
                item2_low = min(item2.pre.low, item2.low)
                cur_high = max(self.high, self.next.high)
            elif method == "loss":
                item2_low = item2.low
                cur_high = self.high
            elif method in ("strict", "totally"):
                if for_virtual:
                    item2_low = min(item2.pre.low, item2.low)
                else:
                    if item2.next is None:
                        return False
                    item2_low = min(item2.pre.low, item2.low, item2.next.low)
                cur_high = max(self.pre.high, self.high, self.next.high)
            else:
                raise ValueError(f"unknown bi_fx_check={method}")
            if method == "totally":
                return self.high < item2_low
            return self.low < item2_low and item2.high > cur_high

        return False

    def __repr__(self):  # pragma: no cover - 调试用
        fx = "^" if self.fx == TOP else ("_" if self.fx == BOTTOM else "")
        return f"#{self.idx}{fx}:{self.time_begin}~{self.time_end} {self.low}~{self.high}({len(self.lst)})"


# ── 笔（内部表示） ──────────────────────────────────────────
class CBi:
    """笔的内部表示（对应 chan.py 的 CBi，去掉 MACD/买卖点部分）。"""

    def __init__(self, begin_klc: MergedKLine, end_klc: MergedKLine,
                 idx: int, is_sure: bool = True):
        self.begin_klc = begin_klc
        self.end_klc = end_klc
        self.idx = idx
        self.is_sure = is_sure
        self.used_to_be_sure = is_sure
        self.dir = UP if begin_klc.fx == BOTTOM else DOWN
        self.pre: Optional["CBi"] = None
        self.next: Optional["CBi"] = None
        self.parent_seg = None
        self.sure_end: list[MergedKLine] = []
        if not self.check_ok():
            raise ValueError("bi direction and begin/end value mismatch")

    # ── chan.py CBi 同名接口 ─────────────────────
    def is_up(self) -> bool:
        return self.dir == UP

    def is_down(self) -> bool:
        return self.dir == DOWN

    def get_begin_val(self) -> float:
        return self.begin_klc.low if self.is_up() else self.begin_klc.high

    def get_end_val(self) -> float:
        return self.end_klc.high if self.is_up() else self.end_klc.low

    def get_begin_klu(self) -> RawBar:
        return self.begin_klc.get_peak(is_high=self.is_down())

    def get_end_klu(self) -> RawBar:
        return self.end_klc.get_peak(is_high=self.is_up())

    def _high(self) -> float:
        return self.end_klc.high if self.is_up() else self.begin_klc.high

    def _low(self) -> float:
        return self.begin_klc.low if self.is_up() else self.end_klc.low

    def check_ok(self) -> bool:
        """chan.py CBi.check 的非抛错版本：方向须与首尾极值一致。"""
        if self.is_down():
            return self.begin_klc.high > self.end_klc.low
        return self.begin_klc.low < self.end_klc.high

    def update_new_end(self, new_klc: MergedKLine) -> bool:
        old = self.end_klc
        self.end_klc = new_klc
        if not self.check_ok():
            self.end_klc = old
            return False
        return True

    def update_virtual_end(self, new_klc: MergedKLine):
        """延长为虚笔：保留原确认终点以便回退，并把本笔标记为未确认。"""
        self.sure_end.append(self.end_klc)
        if not self.update_new_end(new_klc):
            self.sure_end.pop()
            return False
        self.used_to_be_sure = self.is_sure
        self.is_sure = False
        return True

    @property
    def is_used_to_be_sure(self) -> bool:
        return self.is_sure or self.used_to_be_sure

    def __repr__(self):  # pragma: no cover - 调试用
        return f"bi{self.idx}:{self.dir} #{self.begin_klc.idx}->#{self.end_klc.idx}"


def end_is_peak(last_end: MergedKLine, cur_end: MergedKLine) -> bool:
    """
    chan.py BiList.end_is_peak：笔的端点必须是区间内的极值，中途不能有更极端的K线。
    """
    if last_end.next is None or cur_end.next is None:
        # 尾部未定型时保守放行（chan.py 假定尾部必有 next）
        return True
    if last_end.fx == BOTTOM:
        cmp_thred = cur_end.high
        klc = last_end.next
        while True:
            if klc.idx >= cur_end.idx:
                return True
            if klc.high > cmp_thred:
                return False
            if klc.next is None:
                return True
            klc = klc.next
    if last_end.fx == TOP:
        cmp_thred = cur_end.low
        klc = last_end.next
        while True:
            if klc.idx >= cur_end.idx:
                return True
            if klc.low < cmp_thred:
                return False
            if klc.next is None:
                return True
            klc = klc.next
    return True
