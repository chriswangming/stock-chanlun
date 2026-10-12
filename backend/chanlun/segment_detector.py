"""
线段（特征序列法）与中枢 —— 移植自 chan.py 的 Seg/ 与 ZS/ 目录。

    Seg/Seg.py           -> CSeg
    Seg/Eigen.py         -> Eigen
    Seg/EigenFX.py       -> EigenFX
    Seg/SegListChan.py   -> SegListChan
    Seg/SegListDef.py    -> SegListDef（线段破坏法，seg_algo="break"）
    Seg/SegListComm.py   -> SegListComm / FindPeakBi
    ZS/ZS.py             -> CZS
    ZS/ZSList.py         -> CZSList
    KLine/KLine_List.py  -> cal_seg / update_zs_in_seg

与 chan.py 的结构性差异：chan.py 是流式增量计算（每根新K线触发一次重算），
这里改为批处理：一次性拿到全部笔后跑同样的算法。因此 chan.py 中专门为流式服务的
部分（do_init 裁剪不确定尾部、虚笔 "virtual"、 calibration `last_sure_pos` 增量优化）
被简化掉 —— 批处理每次都是全量重算，天然等价于 chan.py 跑到最后一根K线的状态。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Union

from .base import (
    BOTTOM, COMBINE, DOWN, INCLUDED, TOP, UP,
    CBi, _Combiner, has_overlap,
)
from .config import ChanlunConfig, SegConfig, ZSConfig
from .elements import Bi, XianSegment, Zhongshu


# ══════════════════════════════════════════════════════════════
#  笔 -> 内部线性_traits 适配器
# ══════════════════════════════════════════════════════════════
class Line:
    """
    把对外暴露的 pydantic `Bi`（或内部 `CBi`）适配成 chan.py 里 CBi 的接口，
    供特征序列/中枢算法消费。
    """

    def __init__(self, src: Union[Bi, CBi], idx: int, macd_series=None):
        self.src = src
        self.idx = idx
        self.macd_series = macd_series
        self.dir = UP if (src.direction if isinstance(src, Bi) else src.dir) == UP else DOWN
        # 尾部「虚笔」的确认标记：chan.py 用 is_sure / used_to_be_sure 两个标记，
        # 线段与主段的 is_sure 判定用的是 is_used_to_be_sure
        self.is_sure = bool(getattr(src, "is_sure", True))
        self.used_to_be_sure = bool(getattr(src, "used_to_be_sure", self.is_sure))
        self.pre: Optional["Line"] = None
        self.next: Optional["Line"] = None
        self.parent_seg = None
        self.seg_idx: Optional[int] = None

    # ── chan.py CBi 同名接口 ─────────────────────
    def _high(self) -> float:
        return float(self.src.high)

    def _low(self) -> float:
        return float(self.src.low)

    def get_begin_val(self) -> float:
        return float(self.src.start_price)

    def get_end_val(self) -> float:
        return float(self.src.end_price)

    def is_up(self) -> bool:
        return self.dir == UP

    def is_down(self) -> bool:
        return self.dir == DOWN

    # ── 幅度与力度（chan.py CBi.amp / cal_macd_metric） ──
    def amp(self) -> float:
        return abs(self.get_end_val() - self.get_begin_val())

    @property
    def begin_klu_idx(self) -> int:
        """笔起点极值所在原始K线下标（chan.py CBi.get_begin_klu().idx）。"""
        idx = getattr(self.src, "start_idx", -1)
        return int(idx) if idx is not None and idx >= 0 else self.klc_begin_idx

    @property
    def end_klu_idx(self) -> int:
        idx = getattr(self.src, "end_idx", -1)
        return int(idx) if idx is not None and idx >= 0 else self.klc_end_idx

    @property
    def klc_begin_idx(self) -> int:
        idx = getattr(self.src, "klc_start_idx", -1)
        return int(idx) if idx is not None and idx >= 0 else self.begin_klu_idx

    @property
    def klc_end_idx(self) -> int:
        idx = getattr(self.src, "klc_end_idx", -1)
        return int(idx) if idx is not None and idx >= 0 else self.end_klu_idx

    def cal_macd_metric(self, macd_algo: str, is_reverse: bool) -> float:
        """笔的力度（chan.py CBi.cal_macd_metric）。"""
        from .macd import cal_macd_metric
        return cal_macd_metric(self.macd_series, self, macd_algo, is_reverse)

    @property
    def is_used_to_be_sure(self) -> bool:
        return self.is_sure or self.used_to_be_sure

    @property
    def start_date(self):
        return getattr(self.src, "start", None)

    @property
    def end_date(self):
        return getattr(self.src, "end", None)

    @property
    def bi_id(self) -> str:
        return getattr(self.src, "id", f"idx_{self.idx}")

    def __repr__(self):  # pragma: no cover
        return f"line{self.idx}:{self.dir}"


def build_lines(bis, macd_series=None) -> list[Line]:
    lines: list[Line] = []
    for i, b in enumerate(bis):
        lines.append(Line(b, i, macd_series))
    for i, ln in enumerate(lines):
        ln.pre = lines[i - 1] if i > 0 else None
        ln.next = lines[i + 1] if i + 1 < len(lines) else None
    return lines


# ══════════════════════════════════════════════════════════════
#  线段（Seg/）
# ══════════════════════════════════════════════════════════════
class CSeg:
    """线段（chan.py CSeg）。"""

    def __init__(self, idx: int, start_bi: Line, end_bi: Line,
                 is_sure: bool = True, seg_dir: Optional[str] = None, reason: str = "normal"):
        self.idx = idx
        self.start_bi = start_bi
        self.end_bi = end_bi
        self.is_sure = is_sure
        self.used_to_be_sure = is_sure
        self.dir = end_bi.dir if seg_dir is None else seg_dir
        self.zs_lst: list = []
        self.eigen_fx = None
        self.seg_idx: Optional[int] = None
        self.parent_seg = None
        self.pre: Optional[CSeg] = None
        self.next: Optional[CSeg] = None
        self.bi_list: list[Line] = []
        self.reason = reason
        # [冗余-已注释] C3: 该字段无处赋 True，update_zs_in_seg 里的判读恒不触发
        # self.ele_inside_is_sure = False
        if end_bi.idx - start_bi.idx < 2:
            self.is_sure = False

    def is_up(self) -> bool:
        return self.dir == UP

    def is_down(self) -> bool:
        return self.dir == DOWN

    def get_begin_val(self) -> float:
        return self.start_bi.get_begin_val()

    def get_end_val(self) -> float:
        return self.end_bi.get_end_val()

    def check_ok(self) -> bool:
        """chan.py CSeg.check 的非抛错版本。

        注意构造时若笔数<2 已自动把 is_sure 置为 False，此时 chan.py 直接跳过校验，
        所以「只有两笔的首段」是合法的；这里必须保持同样行为，否则会漏掉第一段。
        """
        if not self.is_sure:
            return True
        if self.end_bi.idx - self.start_bi.idx < 2:
            return False
        if self.is_down():
            return self.get_begin_val() > self.get_end_val()
        return self.get_begin_val() < self.get_end_val()

    def add_zs(self, zs):
        self.zs_lst = [zs] + self.zs_lst  # 中枢是反序加入的

    def clear_zs_lst(self):
        self.zs_lst = []

    # ── 段内中枢访问（chan.py Seg/Seg.py 同名方法） ──
    def get_first_multi_bi_zs(self):
        """段内最早的一个多笔中枢。"""
        return next((zs for zs in self.zs_lst if not zs.is_one_bi_zs()), None)

    def get_multi_bi_zs_lst(self) -> list:
        return [zs for zs in self.zs_lst if not zs.is_one_bi_zs()]

    def get_final_multi_bi_zs(self):
        """段内最末（最新）的一个多笔中枢。"""
        for zs in reversed(self.zs_lst):
            if not zs.is_one_bi_zs():
                return zs
        return None

    def get_multi_bi_zs_cnt(self) -> int:
        return sum(not zs.is_one_bi_zs() for zs in self.zs_lst)

    def update_bi_list(self, bi_lst: list[Line], idx1: int, idx2: int):
        self.bi_list = []
        for i in range(idx1, idx2 + 1):
            bi_lst[i].parent_seg = self
            self.bi_list.append(bi_lst[i])

    @property
    def is_used_to_be_sure(self) -> bool:
        return self.is_sure or self.used_to_be_sure

    def __repr__(self):  # pragma: no cover
        return f"seg{self.idx}:{self.start_bi.idx}->{self.end_bi.idx} {self.dir} sure={self.is_sure} ({self.reason})"


class Eigen(_Combiner):
    """特征序列元素（chan.py CEigen）—— 对若干笔再做一次包含处理。"""

    def __init__(self, bi: Line, _dir: str):
        super().__init__(bi, _dir)
        self.gap = False

    def _item_high(self, x: Line) -> float:
        return x._high()

    def _item_low(self, x: Line) -> float:
        return x._low()

    def update_fx(self, pre, nxt, exclude_included: bool = False,
                  allow_top_equal: Optional[int] = None):
        super().update_fx(pre, nxt, exclude_included, allow_top_equal)
        # 出现缺口：第一个元素和第二个元素之间没有重叠
        if (self.fx == TOP and pre.high < self.low) or \
           (self.fx == BOTTOM and pre.low > self.high):
            self.gap = True

    def GetPeakBiIdx(self) -> int:
        bi_dir = self.lst[0].dir
        if bi_dir == UP:  # 下降线段 → 找最低
            return self.get_peak(is_high=False).idx - 1
        return self.get_peak(is_high=True).idx - 1


class EigenFX:
    """特征序列（chan.py CEigenFX）：上升线段取下降笔，下降线段取上升笔。"""

    def __init__(self, _dir: str, exclude_included: bool = True):
        self.dir: str = _dir            # 线段方向
        self.ele: list[Optional[Eigen]] = [None, None, None]
        self.lst: list[Line] = []
        self.exclude_included = exclude_included
        self.kl_dir = UP if _dir == UP else DOWN
        self.last_evidence_bi: Optional[Line] = None
        self.last_evidence_bi_is_sure: bool = False
        self.actual_break_flag = True

    # ── 三元素处理 ───────────────────────────────
    def treat_first_ele(self, bi: Line) -> bool:
        self.ele[0] = Eigen(bi, self.kl_dir)
        return False

    def treat_second_ele(self, bi: Line) -> bool:
        # exclude_included=True 下：COMBINE=被 ele[0] 吸收；INCLUDED=反包 ele[0]，
        # 反包时不合并，必须另起一个元素（chan.py: combine_dir != COMBINE）
        combine_dir = self.ele[0].try_add(bi, exclude_included=self.exclude_included)
        if combine_dir != COMBINE:  # 不能合并
            self.ele[1] = Eigen(bi, self.kl_dir)
            if (self.is_up() and self.ele[1].high < self.ele[0].high) or \
               (self.is_down() and self.ele[1].low > self.ele[0].low):
                # 前两元素不可能成为分形
                return self.reset()
        return False

    def treat_third_ele(self, bi: Line) -> bool:
        self.last_evidence_bi = bi
        self.last_evidence_bi_is_sure = bi.is_used_to_be_sure
        allow_top_equal = (1 if bi.is_down() else -1) if self.exclude_included else None
        # 此处没传 exclude_included，两种包含形态都返回 COMBINE
        combine_dir = self.ele[1].try_add(bi, allow_top_equal=allow_top_equal)
        if combine_dir == COMBINE:
            return False
        self.ele[2] = Eigen(bi, combine_dir)
        if not self.actual_break():
            return self.reset()
        self.ele[1].update_fx(self.ele[0], self.ele[2],
                              exclude_included=self.exclude_included,
                              allow_top_equal=allow_top_equal)
        fx = self.ele[1].fx
        is_fx = (self.is_up() and fx == TOP) or (self.is_down() and fx == BOTTOM)
        return True if is_fx else self.reset()

    def add(self, bi: Line) -> bool:
        self.lst.append(bi)
        if self.ele[0] is None:
            return self.treat_first_ele(bi)
        if self.ele[1] is None:
            return self.treat_second_ele(bi)
        if self.ele[2] is None:
            return self.treat_third_ele(bi)
        raise RuntimeError(f"特征序列3个都找齐了还没处理!! 当前笔:{bi.idx}")

    def reset(self) -> bool:
        bi_tmp_list = list(self.lst[1:])
        if self.exclude_included:
            self.clear()
            for bi in bi_tmp_list:
                if self.add(bi):
                    return True
        else:
            ele2_begin_idx = self.ele[1].lst[0].idx
            self.ele[0], self.ele[1], self.ele[2] = self.ele[1], self.ele[2], None
            self.lst = [bi for bi in bi_tmp_list if bi.idx >= ele2_begin_idx]
        return False

    # ── 是否能作为线段终点 ───────────────────────
    def can_be_end(self, bi_lst: list[Line]):
        """True=正常结束；None=反向分型找到尾部也没找到；False=被反向分型否定。"""
        if self.ele[1] is None:
            return False
        if self.ele[1].gap:
            end_bi_idx = self.GetPeakBiIdx()
            thred_value = bi_lst[end_bi_idx].get_end_val()
            break_thred = self.ele[0].low if self.is_up() else self.ele[0].high
            return self.find_revert_fx(bi_lst, end_bi_idx + 2, thred_value, break_thred)
        if not self.actual_break_flag:
            return None
        return True

    def find_revert_fx(self, bi_list: list[Line], begin_idx: int,
                       thred_value: float, break_thred: float):
        if begin_idx >= len(bi_list):
            return None
        first_bi_dir = bi_list[begin_idx].dir
        eigen_fx = EigenFX(DOWN if first_bi_dir == UP else UP,
                           exclude_included=True)
        for bi in bi_list[begin_idx::2]:
            if eigen_fx.add(bi):
                while True:
                    _test = eigen_fx.can_be_end(bi_list)
                    if not eigen_fx.actual_break_flag:
                        _test = None
                    if _test in (True, None):
                        if eigen_fx.ele[2] is None:
                            break
                        self.last_evidence_bi = eigen_fx.ele[2].lst[-1]
                        if _test is True:
                            self.last_evidence_bi_is_sure = (
                                self.last_evidence_bi.is_used_to_be_sure
                                and eigen_fx.last_evidence_bi_is_sure
                            )
                        return _test
                    if not eigen_fx.reset():
                        break
        return None

    # ── 工具方法 ─────────────────────────────────
    def is_down(self) -> bool:
        return self.dir == DOWN

    def is_up(self) -> bool:
        return self.dir == UP

    def GetPeakBiIdx(self) -> int:
        return self.ele[1].GetPeakBiIdx()

    def all_bi_is_sure(self) -> bool:
        if self.last_evidence_bi is None:
            return False
        return (all(bi.is_used_to_be_sure for bi in self.lst)
                and self.last_evidence_bi_is_sure)

    def clear(self):
        self.ele = [None, None, None]
        self.lst = []

    def actual_break(self) -> bool:
        """防止第二元素因为合并导致后面没有实际突破。"""
        if not self.exclude_included:
            return True
        ele2, ele1 = self.ele[2], self.ele[1]
        if (self.is_up() and ele2.low < ele1[-1]._low()) or \
           (self.is_down() and ele2.high > ele1[-1]._high()):
            return True
        ele2_bi = ele2[0]
        if ele2_bi.next and ele2_bi.next.next:
            nxt2 = ele2_bi.next.next
            if ele2_bi.is_down():
                if nxt2._low() < ele2_bi._low():
                    self.last_evidence_bi = nxt2
                    self.last_evidence_bi_is_sure = nxt2.is_used_to_be_sure
                    return True
                if not nxt2.is_used_to_be_sure or nxt2.next is None:
                    self.actual_break_flag = False
                    return True
            elif ele2_bi.is_up():
                if nxt2._high() > ele2_bi._high():
                    self.last_evidence_bi = nxt2
                    self.last_evidence_bi_is_sure = nxt2.is_used_to_be_sure
                    return True
                if not nxt2.is_used_to_be_sure or nxt2.next is None:
                    self.actual_break_flag = False
                    return True
        else:
            if ele2_bi.next:
                if ((self.is_up() and ele2_bi.next._high() > ele1.high)
                        or (self.is_down() and ele2_bi.next._low() < ele1.low)):
                    return False
                self.actual_break_flag = False
                return True
            self.actual_break_flag = False
            return True
        return False

    def __repr__(self):  # pragma: no cover
        ele = ["[]" if e is None else ",".join(str(b.idx) for b in e.lst) for e in self.ele]
        return " | ".join(ele)


class SegListComm:
    """线段的公共容器（chan.py CSegListComm）。"""

    def __init__(self, seg_config: SegConfig):
        self.lst: list[CSeg] = []
        self.config = seg_config

    def __iter__(self):
        yield from self.lst

    def __len__(self):
        return len(self.lst)

    def __getitem__(self, i):
        return self.lst[i]

    def exist_sure_seg(self) -> bool:
        return any(seg.is_sure for seg in self.lst)

    # ── 尾部残余笔的处理 ─────────────────────────
    def collect_first_seg(self, bi_lst: list[Line]):
        if len(bi_lst) < 3:
            return
        if self.config.left_method == "all":
            _dir = UP if bi_lst[-1].get_end_val() >= bi_lst[0].get_begin_val() else DOWN
            self.add_new_seg(bi_lst, bi_lst[-1].idx, is_sure=False,
                             seg_dir=_dir, split_first_seg=False, reason="0seg_collect_all")
        else:  # peak
            _high = max(bi._high() for bi in bi_lst)
            _low = min(bi._low() for bi in bi_lst)
            if abs(_high - bi_lst[0].get_begin_val()) >= abs(_low - bi_lst[0].get_begin_val()):
                peak_bi = FindPeakBi(bi_lst, is_high=True)
                if peak_bi is not None:
                    self.add_new_seg(bi_lst, peak_bi.idx, is_sure=False,
                                     seg_dir=UP, split_first_seg=False, reason="0seg_find_high")
                self.collect_left_as_seg(bi_lst)
            else:
                peak_bi = FindPeakBi(bi_lst, is_high=False)
                if peak_bi is not None:
                    self.add_new_seg(bi_lst, peak_bi.idx, is_sure=False,
                                     seg_dir=DOWN, split_first_seg=False, reason="0seg_find_low")
                self.collect_left_as_seg(bi_lst)

    def collect_left_seg_peak_method(self, last_seg_end_bi: Line, bi_lst: list[Line]):
        find_new_seg = False
        if last_seg_end_bi.is_down():
            peak_bi = FindPeakBi(bi_lst[last_seg_end_bi.idx + 3:], is_high=True)
            if peak_bi and peak_bi.idx - last_seg_end_bi.idx >= 3:
                self.add_new_seg(bi_lst, peak_bi.idx, is_sure=False,
                                 seg_dir=UP, reason="collectleft_find_high")
                find_new_seg = True
        else:
            peak_bi = FindPeakBi(bi_lst[last_seg_end_bi.idx + 3:], is_high=False)
            if peak_bi and peak_bi.idx - last_seg_end_bi.idx >= 3:
                self.add_new_seg(bi_lst, peak_bi.idx, is_sure=False,
                                 seg_dir=DOWN, reason="collectleft_find_low")
                find_new_seg = True
        if not find_new_seg:
            self.collect_left_as_seg(bi_lst)
            return
        last_seg_end_bi = self[-1].end_bi
        self.collect_left_seg_peak_method(last_seg_end_bi, bi_lst)

    def collect_segs(self, bi_lst: list[Line]):
        if len(bi_lst) == 0:
            return
        last_bi = bi_lst[-1]
        last_seg_end_bi = self[-1].end_bi
        if last_bi.idx - last_seg_end_bi.idx < 3:
            return
        if last_seg_end_bi.is_down() and last_bi.get_end_val() <= last_seg_end_bi.get_end_val():
            if peak_bi := FindPeakBi(bi_lst[last_seg_end_bi.idx + 3:], is_high=True):
                self.add_new_seg(bi_lst, peak_bi.idx, is_sure=False, seg_dir=UP,
                                 reason="collectleft_find_high_force")
                self.collect_left_seg(bi_lst)
        elif last_seg_end_bi.is_up() and last_bi.get_end_val() >= last_seg_end_bi.get_end_val():
            if peak_bi := FindPeakBi(bi_lst[last_seg_end_bi.idx + 3:], is_high=False):
                self.add_new_seg(bi_lst, peak_bi.idx, is_sure=False, seg_dir=DOWN,
                                 reason="collectleft_find_low_force")
                self.collect_left_seg(bi_lst)
        elif self.config.left_method == "all":
            self.collect_left_as_seg(bi_lst)
        else:  # peak
            self.collect_left_seg_peak_method(last_seg_end_bi, bi_lst)

    def collect_left_seg(self, bi_lst: list[Line], _depth: int = 0):
        if len(bi_lst) == 0 or _depth > 20:
            return
        if len(self) == 0:
            self.collect_first_seg(bi_lst)
        else:
            self.collect_segs(bi_lst)
            if (len(bi_lst) > 0 and len(self.lst) > 0
                    and bi_lst[-1].idx - self.lst[-1].bi_list[-1].idx > 2
                    and not self.lst[-1].is_sure):
                self.lst = self.lst[:-1]
                self.collect_left_seg(bi_lst, _depth + 1)

    def collect_left_as_seg(self, bi_lst: list[Line]):
        if len(self) == 0:
            return
        last_bi = bi_lst[-1]
        last_seg_end_bi = self[-1].end_bi
        if last_seg_end_bi.idx + 1 >= len(bi_lst):
            return
        if last_seg_end_bi.dir == last_bi.dir:
            self.add_new_seg(bi_lst, last_bi.idx - 1, is_sure=False, reason="collect_left_1")
        else:
            self.add_new_seg(bi_lst, last_bi.idx, is_sure=False, reason="collect_left_0")

    # ── 新增线段 ─────────────────────────────────
    def try_add_new_seg(self, bi_lst: list[Line], end_bi_idx: int, is_sure: bool = True,
                        seg_dir: Optional[str] = None, split_first_seg: bool = True,
                        reason: str = "normal") -> bool:
        # chan.py 一样：第一段再做一次切分，避免第一段方向/首尾值异常。
        # 注意 chan.py 对这两段强制 is_sure=False，并且直接用 add_new_seg 递归
        # （第二段的 bi1 会自动取第一段末尾+1），这里保持一致。
        if len(self) == 0 and split_first_seg and end_bi_idx >= 3:
            peak_bi = FindPeakBi(bi_lst[end_bi_idx - 3::-1], bi_lst[end_bi_idx].is_down())
            if peak_bi is not None:
                if ((peak_bi.is_down() and (peak_bi._low() < bi_lst[0]._low() or peak_bi.idx == 0))
                        or (peak_bi.is_up() and (peak_bi._high() > bi_lst[0]._high() or peak_bi.idx == 0))):
                    self.add_new_seg(bi_lst, peak_bi.idx, is_sure=False,
                                     seg_dir=peak_bi.dir, reason="split_first_1st")
                    self.add_new_seg(bi_lst, end_bi_idx, is_sure=False,
                                     reason="split_first_2nd")
                    return True
        bi1_idx = 0 if len(self) == 0 else self[-1].end_bi.idx + 1
        return self._append_seg(bi_lst, end_bi_idx, is_sure, seg_dir, bi1_idx, reason)

    def _append_seg(self, bi_lst: list[Line], end_bi_idx: int, is_sure: bool,
                    seg_dir: Optional[str], bi1_idx: int, reason: str) -> bool:
        if bi1_idx > end_bi_idx or bi1_idx >= len(bi_lst) or end_bi_idx >= len(bi_lst):
            return False
        seg = CSeg(len(self.lst), bi_lst[bi1_idx], bi_lst[end_bi_idx],
                   is_sure=is_sure, seg_dir=seg_dir, reason=reason)
        if not seg.check_ok():
            # chan.py 此处会抛 SEG_END_VALUE_ERR；批处理场景改为跳过该线段，
            # 避免脏数据把整次分析打挂
            return False
        self.lst.append(seg)
        if len(self.lst) >= 2:
            self.lst[-2].next = self.lst[-1]
            self.lst[-1].pre = self.lst[-2]
        self.lst[-1].update_bi_list(bi_lst, bi1_idx, end_bi_idx)
        return True

    def add_new_seg(self, bi_lst: list[Line], end_bi_idx: int, is_sure: bool = True,
                    seg_dir: Optional[str] = None, split_first_seg: bool = True,
                    reason: str = "normal") -> bool:
        return self.try_add_new_seg(bi_lst, end_bi_idx, is_sure, seg_dir,
                                    split_first_seg, reason)


class SegListChan(SegListComm):
    """特征序列法线段（chan.py CSegListChan）。"""

    def update(self, bi_lst: list[Line]):
        begin_idx = self._cal_seg_sure(bi_lst, 0)
        self.collect_left_seg(bi_lst)
        return begin_idx

    def _cal_seg_sure(self, bi_lst: list[Line], begin_idx: int):
        up_eigen = EigenFX(UP)
        down_eigen = EigenFX(DOWN)
        last_seg_dir = None if len(self) == 0 else self[-1].dir
        for bi in bi_lst[begin_idx:]:
            fx_eigen = None
            if bi.is_down() and last_seg_dir != UP:
                if up_eigen.add(bi):
                    fx_eigen = up_eigen
            elif bi.is_up() and last_seg_dir != DOWN:
                if down_eigen.add(bi):
                    fx_eigen = down_eigen
            if len(self) == 0:
                # 第一段方向：不要以谁先成为分形来决定
                last_seg_dir = self._guess_first_dir(up_eigen, down_eigen, bi, last_seg_dir)
            if fx_eigen:
                self._treat_fx_eigen(fx_eigen, bi_lst)
                break

    @staticmethod
    def _guess_first_dir(up_eigen: EigenFX, down_eigen: EigenFX, bi: Line, last_seg_dir):
        if up_eigen.ele[1] is not None and bi.is_down():
            last_seg_dir = DOWN
            down_eigen.clear()
        elif down_eigen.ele[1] is not None and bi.is_up():
            up_eigen.clear()
            last_seg_dir = UP
        if up_eigen.ele[1] is None and last_seg_dir == DOWN and bi.dir == DOWN:
            last_seg_dir = None
        elif down_eigen.ele[1] is None and last_seg_dir == UP and bi.dir == UP:
            last_seg_dir = None
        return last_seg_dir

    def _treat_fx_eigen(self, fx_eigen: EigenFX, bi_lst: list[Line], _depth: int = 0):
        if _depth > 20:
            return
        _test = fx_eigen.can_be_end(bi_lst)
        end_bi_idx = fx_eigen.GetPeakBiIdx()
        if _test in (True, None):
            is_true = _test is not None
            if not self.add_new_seg(bi_lst, end_bi_idx,
                                    is_sure=is_true and fx_eigen.all_bi_is_sure()):
                if end_bi_idx + 1 < len(bi_lst):
                    self._cal_seg_sure(bi_lst, end_bi_idx + 1)
                return
            self.lst[-1].eigen_fx = fx_eigen
            if is_true and end_bi_idx + 1 < len(bi_lst):
                self._cal_seg_sure(bi_lst, end_bi_idx + 1)
        else:
            if fx_eigen.lst and fx_eigen.lst[1].idx < len(bi_lst):
                self._cal_seg_sure(bi_lst, fx_eigen.lst[1].idx)


class SegListDef(SegListComm):
    """线段破坏法（chan.py CSegListDef）。"""

    def update(self, bi_lst: list[Line]):
        self._cal_bi_sure(bi_lst)
        self.collect_left_seg(bi_lst)

    @staticmethod
    def is_up_seg(bi: Line, pre_bi: Line) -> bool:
        return bi._high() > pre_bi._high()

    @staticmethod
    def is_down_seg(bi: Line, pre_bi: Line) -> bool:
        return bi._low() < pre_bi._low()

    def _cal_bi_sure(self, bi_lst: list[Line]):
        peak_bi = None
        if len(bi_lst) == 0:
            return
        for idx, bi in enumerate(bi_lst):
            if idx < 2:
                continue
            if peak_bi and ((bi.is_up() and peak_bi.is_up() and bi._high() >= peak_bi._high())
                            or (bi.is_down() and peak_bi.is_down() and bi._low() <= peak_bi._low())):
                peak_bi = bi
                continue
            pre_bi = bi_lst[idx - 2]
            if (bi.is_up() and self.is_up_seg(bi, pre_bi)) or \
               (bi.is_down() and self.is_down_seg(bi, pre_bi)):
                if peak_bi is None:
                    if len(self) == 0 or bi.dir != self[-1].dir:
                        peak_bi = bi
                        continue
                elif peak_bi.dir != bi.dir:
                    if bi.idx - peak_bi.idx <= 2:
                        continue
                    self.add_new_seg(bi_lst, peak_bi.idx)
                    peak_bi = bi
                    continue
        if peak_bi is not None:
            self.add_new_seg(bi_lst, peak_bi.idx, is_sure=False)


def FindPeakBi(bi_lst, is_high: bool):
    """chan.py SegListComm.FindPeakBi。"""
    peak_val = float("-inf") if is_high else float("inf")
    peak_bi = None
    for bi in bi_lst:
        if ((is_high and bi.get_end_val() >= peak_val and bi.is_up())
                or (not is_high and bi.get_end_val() <= peak_val and bi.is_down())):
            if bi.pre and bi.pre.pre and (
                (is_high and bi.pre.pre.get_end_val() > bi.get_end_val())
                or (not is_high and bi.pre.pre.get_end_val() < bi.get_end_val())
            ):
                continue
            peak_val = bi.get_end_val()
            peak_bi = bi
    return peak_bi


def cal_seg(bi_list: list[Line], seg_list: SegListComm):
    """chan.py KLine_List.cal_seg：给每根笔标记所属线段序号。"""
    if len(seg_list) == 0:
        for bi in bi_list:
            bi.seg_idx = 0
        return -1

    cur_seg: CSeg = seg_list[-1]
    bi_idx = len(bi_list) - 1
    while bi_idx >= 0:
        bi = bi_list[bi_idx]
        # [冗余-已注释] C2: bi.idx 恒 >= 0，该条件永不成立（chan.py 流式增量残留）
        # if bi.seg_idx is not None and bi.idx < -1:  # 批处理无增量，恒不触发
        #     break
        if bi.idx > cur_seg.end_bi.idx:
            bi.seg_idx = cur_seg.idx + 1
            bi_idx -= 1
            continue
        if bi.idx < cur_seg.start_bi.idx:
            if cur_seg.pre is None:
                break
            cur_seg = cur_seg.pre
        bi.seg_idx = cur_seg.idx
        bi_idx -= 1

    last_sure_seg_start_bi_idx = -1
    seg = seg_list[-1]
    guard = 0
    while seg and guard < len(seg_list) + 1:
        if seg.is_sure:
            last_sure_seg_start_bi_idx = seg.start_bi.idx
            break
        seg = seg.pre
        guard += 1
    return last_sure_seg_start_bi_idx


# ══════════════════════════════════════════════════════════════
#  中枢（ZS/）
# ══════════════════════════════════════════════════════════════
class CZS:
    """中枢（chan.py ZS/ZS.py）。"""

    def __init__(self, lst: Optional[list], is_sure: bool = True):
        self.is_sure = is_sure
        self.sub_zs_lst: list["CZS"] = []
        self.bi_in = None
        self.bi_out = None
        self.bi_lst: list = []
        if lst is None:
            return
        self.begin_bi = lst[0]
        self.end_bi = lst[-1]
        # [冗余-已注释] C1: 自赋值空操作，无任何副作用
        # for item in lst:
        #     item.parent_seg = item.parent_seg  # noqa - 保持引用
        self._update_range(lst)
        self.peak_high = float("-inf")
        self.peak_low = float("inf")
        for item in lst:
            self._update_end(item)

    # ── 区间 ─────────────────────────────────────
    def _update_range(self, lst):
        self.low = max(item._low() for item in lst)
        self.high = min(item._high() for item in lst)
        self.mid = (self.low + self.high) / 2

    def _update_end(self, item):
        self.end_bi = item
        self.peak_low = min(self.peak_low, item._low())
        self.peak_high = max(self.peak_high, item._high())

    @property
    def begin(self):
        return self.begin_bi.start_date

    @property
    def end(self):
        return self.end_bi.end_date

    def is_one_bi_zs(self) -> bool:
        return self.begin_bi.idx == self.end_bi.idx

    def uniq_bi_cnt(self) -> int:
        return self.end_bi.idx - self.begin_bi.idx + 1

    # ── 进出中枢的笔（chan.py ZS/ZS.py） ──
    def get_bi_in(self):
        return self.bi_in

    # [冗余-已注释] A3: 零引用（外部直接读 zs.bi_out 属性）
    # def get_bi_out(self):
    #     return self.bi_out

    def end_bi_break(self, end_bi=None) -> bool:
        """出中枢笔是否突破中枢区间。"""
        if end_bi is None:
            end_bi = self.bi_out
        if end_bi is None:
            return False
        return (end_bi.is_down() and end_bi._low() < self.low) or \
            (end_bi.is_up() and end_bi._high() > self.high)

    def is_divergence(self, config, out_bi=None):
        """
        背驰判定（chan.py ZS/ZS.py）：出中枢笔力度 <= divergence_rate * 进中枢笔力度。
        返回 (是否背驰, 力度比)。
        """
        if not self.end_bi_break(out_bi):  # 出中枢笔必须突破中枢
            return False, None
        in_bi = self.bi_in
        if in_bi is None:
            return False, None
        if out_bi is None:
            out_bi = self.bi_out
        if out_bi is None:
            return False, None
        in_metric = in_bi.cal_macd_metric(config.macd_algo, is_reverse=False)
        out_metric = out_bi.cal_macd_metric(config.macd_algo, is_reverse=True)
        in_metric = in_metric if in_metric else 1e-7
        if config.divergence_rate > 100:  # 保送
            return True, out_metric / in_metric
        return out_metric <= config.divergence_rate * in_metric, out_metric / in_metric

    def out_bi_is_peak(self, end_bi_idx: int):
        """出中枢笔是否是中枢区间内的极值点（chan.py ZS/ZS.py）。"""
        if len(self.bi_lst) == 0 or self.bi_out is None:
            return False, None
        peak_rate = float("inf")
        for bi in self.bi_lst:
            if bi.idx > end_bi_idx:
                break
            if (self.bi_out.is_down() and bi._low() < self.bi_out._low()) or \
               (self.bi_out.is_up() and bi._high() > self.bi_out._high()):
                return False, None
            end_val = self.bi_out.get_end_val()
            r = abs(bi.get_end_val() - end_val) / end_val if end_val else float("inf")
            if r < peak_rate:
                peak_rate = r
        return True, peak_rate

    # ── 合并 / 延伸 ──────────────────────────────
    def combine(self, zs2: "CZS", combine_mode: str) -> bool:
        if zs2.is_one_bi_zs():
            return False
        if getattr(self.begin_bi, "seg_idx", None) != getattr(zs2.begin_bi, "seg_idx", None):
            return False
        if combine_mode == "zs":
            if not has_overlap(self.low, self.high, zs2.low, zs2.high, equal=True):
                return False
            self._do_combine(zs2)
            return True
        if combine_mode == "peak":
            if has_overlap(self.peak_low, self.peak_high, zs2.peak_low, zs2.peak_high):
                self._do_combine(zs2)
                return True
            return False
        raise ValueError(f"{combine_mode} is unsupport zs combine mode")

    def _do_combine(self, zs2: "CZS"):
        if len(self.sub_zs_lst) == 0:
            self.sub_zs_lst.append(self.make_copy())
        self.sub_zs_lst.append(zs2)
        self.low = min(self.low, zs2.low)
        self.high = max(self.high, zs2.high)
        self.peak_low = min(self.peak_low, zs2.peak_low)
        self.peak_high = max(self.peak_high, zs2.peak_high)
        self.end_bi = zs2.end_bi
        self.bi_out = zs2.bi_out

    def try_add_to_end(self, item) -> bool:
        if not self.in_range(item):
            return False
        if self.is_one_bi_zs():
            self._update_range([self.begin_bi, item])
        self._update_end(item)
        return True

    def in_range(self, item) -> bool:
        return has_overlap(self.low, self.high, item._low(), item._high())

    def is_inside(self, seg: CSeg) -> bool:
        return seg.start_bi.idx <= self.begin_bi.idx <= seg.end_bi.idx

    def make_copy(self) -> "CZS":
        copy = CZS(lst=None, is_sure=self.is_sure)
        copy.begin_bi = self.begin_bi
        copy.end_bi = self.end_bi
        copy.low = self.low
        copy.high = self.high
        copy.peak_high = self.peak_high
        copy.peak_low = self.peak_low
        copy.bi_in = self.bi_in
        copy.bi_out = self.bi_out
        return copy

    def __repr__(self):  # pragma: no cover
        return f"zs[{self.begin_bi.idx}->{self.end_bi.idx}] {self.low:.2f}~{self.high:.2f}"


class CZSList:
    """中枢列表（chan.py ZS/ZSList.py 的 normal / over_seg / auto 算法）。"""

    def __init__(self, zs_config: ZSConfig):
        self.zs_lst: list[CZS] = []
        self.config = zs_config
        self.free_item_lst: list = []

    def __iter__(self):
        yield from self.zs_lst

    def __len__(self):
        return len(self.zs_lst)

    def __getitem__(self, i):
        return self.zs_lst[i]

    # ── 构造 ─────────────────────────────────────
    def add_to_free_lst(self, item, is_sure: bool, zs_algo: str):
        if len(self.free_item_lst) != 0 and item.idx == self.free_item_lst[-1].idx:
            # 防止笔新高/新低的更新带来bug
            self.free_item_lst = self.free_item_lst[:-1]
        self.free_item_lst.append(item)
        res = self._try_construct_zs(self.free_item_lst, is_sure, zs_algo)
        if res is not None and res.begin_bi.idx > 0:  # 禁止第一笔就是中枢的起点
            self.zs_lst.append(res)
            self.free_item_lst = []
            self._try_combine()

    def clear_free_lst(self):
        self.free_item_lst = []

    def update(self, bi, is_sure: bool = True):
        if len(self.free_item_lst) == 0 and self._try_add_to_end(bi):
            # zs_combine_mode=peak 合并模式下会触发生效
            self._try_combine()
            return
        self.add_to_free_lst(bi, is_sure, "normal")

    def _try_add_to_end(self, bi) -> bool:
        return False if len(self.zs_lst) == 0 else self.zs_lst[-1].try_add_to_end(bi)

    def add_zs_from_bi_range(self, seg_bi_lst: list, seg_dir: str, seg_is_sure: bool):
        deal_bi_cnt = 0
        for bi in seg_bi_lst:
            if bi.dir == seg_dir:
                continue
            if deal_bi_cnt < 1:  # 防止 try_add_to_end 执行到上一个线段的中枢里面去
                self.add_to_free_lst(bi, seg_is_sure, "normal")
                deal_bi_cnt += 1
            else:
                self.update(bi, seg_is_sure)

    def _try_construct_zs(self, lst, is_sure: bool, zs_algo: str):
        if zs_algo == "normal":
            if not self.config.one_bi_zs:
                if len(lst) == 1:
                    return None
                lst = lst[-2:]
        elif zs_algo == "over_seg":
            if len(lst) < 3:
                return None
            lst = lst[-3:]
            if lst[0].dir == getattr(lst[0].parent_seg, "dir", None):
                return None
        else:
            raise ValueError(f"unknown zs_algo {zs_algo}")
        min_high = min(item._high() for item in lst)
        max_low = max(item._low() for item in lst)
        return CZS(lst, is_sure=is_sure) if min_high > max_low else None

    # ── 主入口 ───────────────────────────────────
    def cal_bi_zs(self, bi_lst: list[Line], seg_lst: SegListComm):
        if self.config.zs_algo == "normal":
            for seg in seg_lst:
                self.clear_free_lst()
                seg_bi_lst = bi_lst[seg.start_bi.idx:seg.end_bi.idx + 1]
                self.add_zs_from_bi_range(seg_bi_lst, seg.dir, seg.is_sure)
            # 处理未生成新线段的部分
            if len(seg_lst):
                self.clear_free_lst()
                self.add_zs_from_bi_range(
                    bi_lst[seg_lst[-1].end_bi.idx + 1:],
                    DOWN if seg_lst[-1].dir == UP else UP,
                    False,
                )
        elif self.config.zs_algo == "over_seg":
            self.clear_free_lst()
            begin_bi_idx = self.zs_lst[-1].end_bi.idx + 1 if self.zs_lst else 0
            for bi in bi_lst[begin_bi_idx:]:
                self._update_overseg_zs(bi)
        elif self.config.zs_algo == "auto":
            sure_seg_appear = False
            exist_sure_seg = seg_lst.exist_sure_seg()
            for seg in seg_lst:
                if seg.is_sure:
                    sure_seg_appear = True
                if seg.is_sure or (not sure_seg_appear and exist_sure_seg):
                    self.clear_free_lst()
                    self.add_zs_from_bi_range(
                        bi_lst[seg.start_bi.idx:seg.end_bi.idx + 1], seg.dir, seg.is_sure)
                else:
                    self.clear_free_lst()
                    for bi in bi_lst[seg.start_bi.idx:]:
                        self._update_overseg_zs(bi)
                    break
        else:
            raise ValueError(f"unknown zs_algo {self.config.zs_algo}")

    def _update_overseg_zs(self, bi):
        if len(self.zs_lst) and len(self.free_item_lst) == 0:
            if bi.next is None:
                return
            if (bi.idx - self.zs_lst[-1].end_bi.idx <= 1
                    and self.zs_lst[-1].in_range(bi.next)
                    and self.zs_lst[-1].try_add_to_end(bi)):
                return
        if (len(self.zs_lst) and len(self.free_item_lst) == 0
                and self.zs_lst[-1].in_range(bi)
                and bi.idx - self.zs_lst[-1].end_bi.idx <= 1):
            return
        self.add_to_free_lst(bi, bi.is_sure, zs_algo="over_seg")

    def _try_combine(self):
        if not self.config.need_combine:
            return
        while len(self.zs_lst) >= 2 and self.zs_lst[-2].combine(
                self.zs_lst[-1], combine_mode=self.config.zs_combine_mode):
            self.zs_lst = self.zs_lst[:-1]


def update_zs_in_seg(bi_lst: list[Line], seg_lst: SegListComm, zs_list: CZSList):
    """chan.py KLine_List.update_zs_in_seg：算出线段内的中枢以及中枢的 bi_in/bi_out。"""
    seg_idx = len(seg_lst) - 1
    while seg_idx >= 0:
        seg = seg_lst[seg_idx]
        # [冗余-已注释] C3: ele_inside_is_sure 无处赋 True（流式残留），该分支永不触发
        # if seg.ele_inside_is_sure:
        #     break
        seg.clear_zs_lst()
        _zs_idx = len(zs_list.zs_lst) - 1
        while _zs_idx >= 0:
            zs = zs_list.zs_lst[_zs_idx]
            if zs.end_bi.idx < seg.start_bi.idx:
                break
            if zs.is_inside(seg):
                seg.add_zs(zs)
            if zs.begin_bi.idx > 0:
                zs.bi_in = bi_lst[zs.begin_bi.idx - 1]
            if zs.end_bi.idx + 1 < len(bi_lst):
                zs.bi_out = bi_lst[zs.end_bi.idx + 1]
            zs.bi_lst = list(bi_lst[zs.begin_bi.idx:zs.end_bi.idx + 1])
            _zs_idx -= 1
        seg_idx -= 1


# ══════════════════════════════════════════════════════════════
#  对外 API
# ══════════════════════════════════════════════════════════════
class SegmentDetector:
    """
    线段 + 中枢检测器。

    线段采用 chan.py 的特征序列法（默认）或线段破坏法；
    中枢采用 chan.py 的 normal 段内中枢：由奇数个连续次级别重叠区域构成，
    且同样方向的中枢可以延伸 / 合并 —— 而不是旧实现里「三个线段围一块」。
    """

    def __init__(self, bis: list[Bi], config: Optional[ChanlunConfig] = None,
                 macd_series=None):
        self.bis = list(bis)
        self.config = config or ChanlunConfig()
        self.macd_series = macd_series
        self._lines = build_lines(self.bis, macd_series)
        self._seg_list: Optional[SegListComm] = None
        self._zs_list: Optional[CZSList] = None

    # ── 内部结构（买卖点判定需要笔序列/线段/段内中枢） ──
    @property
    def lines(self) -> list[Line]:
        return self._lines

    @property
    def seg_list(self) -> Optional[SegListComm]:
        return self._seg_list

    # [冗余-已注释] A4: 零引用（engine 只用 lines / seg_list）
    # @property
    # def zs_list(self) -> Optional[CZSList]:
    #     return self._zs_list

    # ── 线段 ─────────────────────────────────────
    def detect_segments(self, algo: Optional[str] = None, **_deprecated) -> list[XianSegment]:
        """
        algo 取自 SegConfig.seg_algo；旧签名里的 min_overlap_bis / max_iterations
        属于已被替换的「三笔重叠成段」实现，这里忽略。
        """
        algo = algo or self.config.seg.seg_algo
        if algo == "chan":
            seg_list: SegListComm = SegListChan(self.config.seg)
        elif algo == "break":
            seg_list = SegListDef(self.config.seg)
        else:
            raise NotImplementedError(f"seg_algo={algo} 暂未实现（支持 chan/break）")

        seg_list.update(self._lines)
        cal_seg(self._lines, seg_list)
        self._seg_list = seg_list
        return self._to_segment_models(seg_list)

    def _to_segment_models(self, seg_list: SegListComm) -> list[XianSegment]:
        out: list[XianSegment] = []
        for seg in seg_list:
            out.append(
                XianSegment(
                    id=f"xian_{len(out) + 1}",
                    start=seg.start_bi.start_date,
                    end=seg.end_bi.end_date,
                    direction="up" if seg.is_up() else "down",
                    high=max(b._high() for b in seg.bi_list),
                    low=min(b._low() for b in seg.bi_list),
                    start_price=float(seg.get_begin_val()),
                    end_price=float(seg.get_end_val()),
                    bi_ids=[b.bi_id for b in seg.bi_list],
                    level=2,
                    is_sure=bool(seg.is_sure),
                )
            )
        return out

    # ── 中枢 ─────────────────────────────────────
    def detect_zhongshus(self, segments: Optional[list[XianSegment]] = None,
                         zs_algo: Optional[str] = None) -> list[Zhongshu]:
        """
        中枢计算依赖线段的划分结果（段内中枢不跨段），因此必须同时拿到线段。
        若未完成线段划分会先自动跑一遍。
        """
        if self._seg_list is None:
            self.detect_segments()
        assert self._seg_list is not None

        cfg = self.config.zs
        if zs_algo and zs_algo != cfg.zs_algo:
            cfg = ZSConfig(need_combine=cfg.need_combine,
                           zs_combine_mode=cfg.zs_combine_mode,
                           one_bi_zs=cfg.one_bi_zs, zs_algo=zs_algo)

        zs_list = CZSList(cfg)
        zs_list.cal_bi_zs(self._lines, self._seg_list)
        update_zs_in_seg(self._lines, self._seg_list, zs_list)
        self._zs_list = zs_list
        return self._to_zs_models(zs_list)

    def _to_zs_models(self, zs_list: CZSList) -> list[Zhongshu]:
        seg_items = list(self._seg_list or [])
        out: list[Zhongshu] = []
        for zs in zs_list:
            # 中枢所属线段：chan.py 的 update_zs_in_seg 用同样的包含判定
            xian_ids = [f"xian_{i + 1}" for i, seg in enumerate(seg_items) if zs.is_inside(seg)]
            out.append(
                Zhongshu(
                    id=f"zs_{len(out) + 1}",
                    start=zs.begin,
                    end=zs.end,
                    range_high=float(zs.high),
                    range_low=float(zs.low),
                    xian_ids=xian_ids,
                    bi_ids=[b.bi_id for b in zs.bi_lst],
                    level=2,
                    center=float(zs.mid),
                    is_sure=bool(zs.is_sure),
                    bi_cnt=zs.uniq_bi_cnt(),
                )
            )
        return out

    # ── 其它 ─────────────────────────────────────
    # [冗余-已注释] D2: 生产代码无引用，仅 test_segment_detector 用到
    # @staticmethod
    # def get_zhongshu_for_price(zhongshus: list[Zhongshu], price: float):
    #     """找到价格所在的中枢（取最近的一个）。"""
    #     for zs in reversed(zhongshus):
    #         if zs.range_low <= price <= zs.range_high:
    #             return zs
    #     return None
