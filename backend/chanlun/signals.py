"""
买卖点判定模块 —— 移植自 chan.py 的 BuySellPoint/ 目录。

    BuySellPoint/BSPointConfig.py -> BSPointConfig / BSPConfig（见 config.py）
    BuySellPoint/BS_Point.py      -> _BSPoint
    BuySellPoint/BSPointList.py   -> BSPointList

与 chan.py 的对应关系：

    T1  一类买卖点：线段最后一个中枢的「进出中枢笔力度背驰」
    T1P 盘整背驰一类：段内没有多笔中枢时，用线段末笔与前一同向笔比力度
    T2  二类：一买后突破笔的下一笔回踩，回撤幅度 <= max_bs2_rate
    T2S 类二类：二买之后继续在中枢内震荡形成的同向回踩（多档）
    T3A 三类（中枢在一买之后）：出中枢后回踩不回中枢
    T3B 三类（中枢在一买之前）：一买后回踩不回到该中枢

判定用的「力度」不再是旧实现里现编的 (high-low)/时长，而是 chan.py 的
`CBi.cal_macd_metric`（默认 peak：区间内同号 MACD 柱的最大绝对值）。

与 chan.py 的唯一结构性差别：chan.py 是流式增量（`last_sure_pos` /
`last_sure_seg_idx` 只重算尾部），这里批处理每次全量重算所有线段，
等价于 chan.py 跑到最后一根K线时累积下来的全部买卖点。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .config import BSPConfig, BSPointConfig
from .elements import Bi, XianSegment, Zhongshu, BuySellPoint, SupportResistanceLevel

# chan.py Common/CEnum.BSP_TYPE
T1, T1P, T2, T2S, T3A, T3B = "1", "1p", "2", "2s", "3a", "3b"

# 对外只有「一/二/三买 + 一/二/三卖」六种类型，子类型写进 description
_MAIN_TYPE = {
    "1": ("一买", "一卖"),
    "2": ("二买", "二卖"),
    "3": ("三买", "三卖"),
}
_SUB_LABEL = {
    T1: "趋势背驰",
    T1P: "盘整背驰",
    T2: "",
    T2S: "类二类",
    T3A: "中枢后",
    T3B: "中枢前",
}


def _main_name(bs_type: str, is_buy: bool) -> str:
    pair = _MAIN_TYPE.get(bs_type[0], ("一买", "一卖"))
    return pair[0] if is_buy else pair[1]


class _BSPoint:
    """一个买卖点（chan.py CBS_Point）：一笔的尾部可能同时是多个类型。"""

    __slots__ = ("bi", "is_buy", "types", "relate_bsp1", "features")

    def __init__(self, bi, is_buy: bool, bs_type: str,
                 relate_bsp1: Optional["_BSPoint"] = None,
                 feature_dict: Optional[dict] = None):
        self.bi = bi
        self.is_buy = is_buy
        self.types: list[str] = [bs_type]
        self.relate_bsp1 = relate_bsp1
        self.features: dict = dict(feature_dict or {})

    def add_type(self, bs_type: str, relate_bsp1=None, feature_dict: Optional[dict] = None):
        if bs_type not in self.types:
            self.types.append(bs_type)
        if self.relate_bsp1 is None:
            self.relate_bsp1 = relate_bsp1
        if feature_dict:
            self.features.update(feature_dict)

    def __repr__(self):  # pragma: no cover
        return f"bsp(bi{self.bi.idx},{'buy' if self.is_buy else 'sell'},{','.join(self.types)})"


class BSPointList:
    """买卖点计算器 —— chan.py CBSPointList 的批处理移植。"""

    def __init__(self, config: Optional[BSPConfig] = None, macd_series=None):
        self.config = config or BSPConfig()
        self.macd_series = macd_series
        # 目标买卖点：type -> [买点列表, 卖点列表]
        self.bsp_store: dict[str, list[list[_BSPoint]]] = {}
        self.bsp_flat: dict[int, _BSPoint] = {}     # bi.idx -> 已收库的买卖点
        self.bsp1_dict: dict[int, _BSPoint] = {}    # bi.idx -> 一类买卖点（含非目标）

    # ── 收纳 ────────────────────────────────────────────
    def get_conf(self, is_buy: bool) -> BSPointConfig:
        return self.config.get(is_buy)

    def store_add_bsp(self, bs_type: str, bsp: _BSPoint):
        slot = self.bsp_store.setdefault(bs_type, [[], []])
        slot[0 if bsp.is_buy else 1].append(bsp)
        self.bsp_flat[bsp.bi.idx] = bsp

    def add_bsp1(self, bsp: _BSPoint):
        self.bsp1_dict[bsp.bi.idx] = bsp

    def add_bs(self, bs_type: str, bi, relate_bsp1: Optional[_BSPoint] = None,
               is_target_bsp: bool = True, feature_dict: Optional[dict] = None):
        """chan.py CBSPointList.add_bs：买点只在向下笔的尾部，卖点只在向上笔的尾部。"""
        is_buy = bi.is_down()
        if exist := self.bsp_flat.get(bi.idx):
            exist.add_type(bs_type, relate_bsp1, feature_dict)
            return
        conf = self.get_conf(is_buy)
        if bs_type not in conf.target_types:
            is_target_bsp = False
        # 非目标买卖点除了 1/1p（后续 2/3 类还要引用）之外直接丢弃
        if not is_target_bsp and bs_type not in (T1, T1P):
            return
        bsp = _BSPoint(bi=bi, is_buy=is_buy, bs_type=bs_type,
                       relate_bsp1=relate_bsp1, feature_dict=feature_dict)
        if is_target_bsp:
            self.store_add_bsp(bs_type, bsp)
        if bs_type in (T1, T1P):
            self.add_bsp1(bsp)

    # ── 主入口 ──────────────────────────────────────────
    def cal(self, bi_list: list, seg_list):
        if seg_list is None or len(seg_list) == 0 or len(bi_list) == 0:
            return
        self.cal_seg_bs1point(seg_list, bi_list)
        self.cal_seg_bs2point(seg_list, bi_list)
        self.cal_seg_bs3point(seg_list, bi_list)

    def get_sorted_bsp(self) -> list[_BSPoint]:
        return sorted(self.bsp_flat.values(), key=lambda b: b.bi.idx)

    # ── 一类（含盘整背驰） ───────────────────────────────
    def cal_seg_bs1point(self, seg_list, bi_list: list):
        for seg in seg_list:
            self.cal_single_bs1point(seg, bi_list)

    def cal_single_bs1point(self, seg, bi_list: list):
        conf = self.get_conf(seg.is_down())
        zs_cnt = seg.get_multi_bi_zs_cnt() if conf.bsp1_only_multibi_zs else len(seg.zs_lst)
        is_target_bsp = (conf.min_zs_cnt <= 0 or zs_cnt >= conf.min_zs_cnt)
        last_zs = seg.zs_lst[-1] if seg.zs_lst else None
        if last_zs is not None and not last_zs.is_one_bi_zs() and self._zs_can_bsp1(seg, last_zs):
            self.treat_bsp1(seg, last_zs, conf, is_target_bsp)
        else:
            self.treat_pz_bsp1(seg, conf, bi_list, is_target_bsp)

    @staticmethod
    def _zs_can_bsp1(seg, last_zs) -> bool:
        """chan.py：中枢必须已经被线段末笔（或更晚的笔）走出去。"""
        bi_in = last_zs.get_bi_in()
        if bi_in is None:
            return False
        if (last_zs.bi_out is not None and last_zs.bi_out.idx >= seg.end_bi.idx) or \
           (last_zs.bi_lst and last_zs.bi_lst[-1].idx >= seg.end_bi.idx):
            return seg.end_bi.idx - bi_in.idx > 2
        return False

    def treat_bsp1(self, seg, last_zs, conf: BSPointConfig, is_target_bsp: bool):
        break_peak, _ = last_zs.out_bi_is_peak(seg.end_bi.idx)
        if conf.bs1_peak and not break_peak:
            is_target_bsp = False
        is_diver, divergence_rate = last_zs.is_divergence(conf, out_bi=seg.end_bi)
        if not is_diver:
            is_target_bsp = False
        feature_dict = {
            "divergence_rate": divergence_rate,
            "zs_cnt": len(seg.zs_lst),
            "zs_low": float(last_zs.low),
            "zs_high": float(last_zs.high),
        }
        self.add_bs(bs_type=T1, bi=seg.end_bi, relate_bsp1=None,
                    is_target_bsp=is_target_bsp, feature_dict=feature_dict)

    def treat_pz_bsp1(self, seg, conf: BSPointConfig, bi_list: list, is_target_bsp: bool):
        """盘整背驰一类：段内没有中枢时，比较线段末笔与前一同向笔的力度。"""
        last_bi = seg.end_bi
        if last_bi.idx - 2 < 0:
            return
        pre_bi = bi_list[last_bi.idx - 2]
        if last_bi.seg_idx != pre_bi.seg_idx:
            return
        if last_bi.dir != seg.dir:
            return
        if last_bi.is_down() and last_bi._low() > pre_bi._low():    # 必须创新低
            return
        if last_bi.is_up() and last_bi._high() < pre_bi._high():    # 必须创新高
            return
        in_metric = pre_bi.cal_macd_metric(conf.macd_algo, is_reverse=False)
        out_metric = last_bi.cal_macd_metric(conf.macd_algo, is_reverse=True)
        in_metric = in_metric if in_metric else 1e-7
        divergence_rate = out_metric / in_metric
        is_diver = out_metric <= conf.divergence_rate * in_metric
        if not is_diver:
            is_target_bsp = False
        feature_dict = {
            "divergence_rate": divergence_rate,
            "bsp1_bi_amp": last_bi.amp(),
        }
        self.add_bs(bs_type=T1P, bi=last_bi, relate_bsp1=None,
                    is_target_bsp=is_target_bsp, feature_dict=feature_dict)

    # ── 二类 / 类二类 ────────────────────────────────────
    def cal_seg_bs2point(self, seg_list, bi_list: list):
        for seg in seg_list:
            conf = self.get_conf(seg.is_down())
            if T2 not in conf.target_types and T2S not in conf.target_types:
                continue
            self.treat_bsp2(seg, seg_list, bi_list)

    def treat_bsp2(self, seg, seg_list, bi_list: list):
        if len(seg_list) > 1:
            conf = self.get_conf(seg.is_down())
            bsp1_bi = seg.end_bi
            real_bsp1 = self.bsp1_dict.get(bsp1_bi.idx)
            if bsp1_bi.idx + 2 >= len(bi_list):
                return
            break_bi = bi_list[bsp1_bi.idx + 1]
            bsp2_bi = bi_list[bsp1_bi.idx + 2]
        else:
            conf = self.get_conf(seg.is_up())
            bsp1_bi, real_bsp1 = None, None
            if len(bi_list) == 1:
                return
            bsp2_bi = bi_list[1]
            break_bi = bi_list[0]
        if conf.bsp2_follow_1 and (not bsp1_bi or bsp1_bi.idx not in self.bsp_flat):
            return
        break_amp = break_bi.amp()
        retrace_rate = bsp2_bi.amp() / break_amp if break_amp else 1.0
        bsp2_flag = retrace_rate <= conf.max_bs2_rate
        if bsp2_flag:
            self.add_bs(
                bs_type=T2, bi=bsp2_bi, relate_bsp1=real_bsp1,
                feature_dict={
                    "bsp2_retrace_rate": retrace_rate,
                    "bsp2_break_bi_amp": break_amp,
                    "bsp2_bi_amp": bsp2_bi.amp(),
                },
            )
        elif conf.bsp2s_follow_2:
            return
        if T2S not in self.get_conf(seg.is_down()).target_types:
            return
        self.treat_bsp2s(seg_list, bi_list, bsp2_bi, break_bi, real_bsp1, conf)

    def treat_bsp2s(self, seg_list, bi_list: list, bsp2_bi, break_bi,
                    real_bsp1, conf: BSPointConfig):
        bias = 2
        _low, _high = None, None
        while bsp2_bi.idx + bias < len(bi_list):  # 计算类二买
            bsp2s_bi = bi_list[bsp2_bi.idx + bias]
            if conf.max_bsp2s_lv is not None and bias / 2 > conf.max_bsp2s_lv:
                break
            if bsp2s_bi.seg_idx != bsp2_bi.seg_idx and (
                bsp2s_bi.seg_idx < len(seg_list) - 1
                or bsp2s_bi.seg_idx - bsp2_bi.seg_idx >= 2
                or seg_list[bsp2_bi.seg_idx].is_sure
            ):
                break
            if bias == 2:
                if not _has_overlap(bsp2_bi._low(), bsp2_bi._high(),
                                    bsp2s_bi._low(), bsp2s_bi._high()):
                    break
                _low = max(bsp2_bi._low(), bsp2s_bi._low())
                _high = min(bsp2_bi._high(), bsp2s_bi._high())
            elif not _has_overlap(_low, _high, bsp2s_bi._low(), bsp2s_bi._high()):
                break
            if _bsp2s_break_bsp1(bsp2s_bi, break_bi):
                break
            break_amp = break_bi.amp()
            retrace_rate = abs(bsp2s_bi.get_end_val() - break_bi.get_end_val()) / break_amp \
                if break_amp else 1.0
            if retrace_rate > conf.max_bs2_rate:
                break
            self.add_bs(
                bs_type=T2S, bi=bsp2s_bi, relate_bsp1=real_bsp1,
                feature_dict={
                    "bsp2s_retrace_rate": retrace_rate,
                    "bsp2s_break_bi_amp": break_amp,
                    "bsp2s_bi_amp": bsp2s_bi.amp(),
                    "bsp2s_lv": bias / 2,
                },
            )
            bias += 2

    # ── 三类 ────────────────────────────────────────────
    def cal_seg_bs3point(self, seg_list, bi_list: list):
        for seg in seg_list:
            conf = self.get_conf(seg.is_down())
            if T3A not in conf.target_types and T3B not in conf.target_types:
                continue
            if len(seg_list) > 1:
                bsp1_bi = seg.end_bi
                bsp1_bi_idx = bsp1_bi.idx
                real_bsp1 = self.bsp1_dict.get(bsp1_bi.idx)
                next_seg_idx = seg.idx + 1
                next_seg = seg.next  # 可能为 None
            else:
                next_seg = seg
                next_seg_idx = seg.idx
                bsp1_bi, real_bsp1 = None, None
                bsp1_bi_idx = -1
                conf = self.get_conf(seg.is_up())
            if conf.bsp3_follow_1 and (not bsp1_bi or bsp1_bi.idx not in self.bsp_flat):
                continue
            if next_seg is not None:
                self.treat_bsp3_after(seg_list, next_seg, conf, bi_list,
                                      real_bsp1, bsp1_bi_idx, next_seg_idx)
            self.treat_bsp3_before(seg_list, seg, next_seg, bsp1_bi, conf,
                                   bi_list, real_bsp1, next_seg_idx)

    def treat_bsp3_after(self, seg_list, next_seg, conf: BSPointConfig, bi_list: list,
                         real_bsp1, bsp1_bi_idx, next_seg_idx):
        """3a：中枢在一买之后 —— 出中枢后的回踩不回到中枢里。"""
        first_zs = next_seg.get_first_multi_bi_zs()
        if first_zs is None:
            return
        first_bi_in = first_zs.get_bi_in()
        if conf.strict_bsp3 and (first_bi_in is None or first_bi_in.idx != bsp1_bi_idx + 1):
            return
        bsp3a_max_zs_cnt = self.get_conf(next_seg.is_down()).bsp3a_max_zs_cnt
        for zs_idx, zs in enumerate(next_seg.get_multi_bi_zs_lst()):
            if zs_idx >= bsp3a_max_zs_cnt:
                break
            if zs.bi_out is None or zs.bi_out.idx + 1 >= len(bi_list):
                break
            bsp3_bi = bi_list[zs.bi_out.idx + 1]
            if bsp3_bi.parent_seg is None:
                if next_seg.idx != len(seg_list) - 1:
                    break
            elif bsp3_bi.parent_seg.idx != next_seg.idx:
                if len(bsp3_bi.parent_seg.bi_list) >= 3:
                    break
            if bsp3_bi.dir == next_seg.dir:
                break
            if bsp3_bi.seg_idx != next_seg_idx and next_seg_idx < len(seg_list) - 2:
                break
            if _bsp3_back2zs(bsp3_bi, zs):
                continue
            if conf.bsp3_peak and not _bsp3_break_zspeak(bsp3_bi, zs):
                continue
            self.add_bs(
                bs_type=T3A, bi=bsp3_bi, relate_bsp1=real_bsp1,
                feature_dict={
                    "bsp3_zs_height": (zs.high - zs.low) / zs.low if zs.low else 0.0,
                    "bsp3_bi_amp": bsp3_bi.amp(),
                    "zs_low": float(zs.low),
                    "zs_high": float(zs.high),
                },
            )

    def treat_bsp3_before(self, seg_list, seg, next_seg, bsp1_bi, conf: BSPointConfig,
                          bi_list: list, real_bsp1, next_seg_idx):
        """3b：中枢在一买之前 —— 一买之后的回踩不回到该中枢。"""
        cmp_zs = seg.get_final_multi_bi_zs()
        if cmp_zs is None or bsp1_bi is None:
            return
        if conf.strict_bsp3 and (cmp_zs.bi_out is None or cmp_zs.bi_out.idx != bsp1_bi.idx):
            return
        end_bi_idx = _cal_bsp3_bi_end_idx(next_seg)
        for bsp3_bi in bi_list[bsp1_bi.idx + 2::2]:
            if bsp3_bi.idx > end_bi_idx:
                break
            if bsp3_bi.seg_idx != next_seg_idx and bsp3_bi.seg_idx < len(seg_list) - 1:
                break
            if _bsp3_back2zs(bsp3_bi, cmp_zs):
                continue
            self.add_bs(
                bs_type=T3B, bi=bsp3_bi, relate_bsp1=real_bsp1,
                feature_dict={
                    "bsp3_zs_height": (cmp_zs.high - cmp_zs.low) / cmp_zs.low if cmp_zs.low else 0.0,
                    "bsp3_bi_amp": bsp3_bi.amp(),
                    "zs_low": float(cmp_zs.low),
                    "zs_high": float(cmp_zs.high),
                },
            )
            break

    # ── 输出 ────────────────────────────────────────────
    def to_models(self, level: str) -> list[BuySellPoint]:
        out: list[BuySellPoint] = []
        for bsp in self.get_sorted_bsp():
            out.append(self._to_model(bsp, level))
        return out

    def _to_model(self, bsp: _BSPoint, level: str) -> BuySellPoint:
        bi = bsp.bi
        price = float(bi.get_end_val())
        dt = bi.end_date
        if not isinstance(dt, datetime):
            dt = bi.start_date
        zs_low = bsp.features.get("zs_low")
        zs_high = bsp.features.get("zs_high")
        # 主类型取优先级最高的（1 > 1p > 2 > 2s > 3a > 3b）
        order = {T1: 0, T1P: 1, T2: 2, T2S: 3, T3A: 4, T3B: 5}
        main = sorted(bsp.types, key=lambda t: order.get(t, 9))[0]
        subs = "/".join(_SUB_LABEL.get(t, "") for t in bsp.types if _SUB_LABEL.get(t))
        return BuySellPoint(
            type=_main_name(main, bsp.is_buy),
            level=level,
            price=price,
            datetime=dt if isinstance(dt, datetime) else datetime.min,
            confidence=self._confidence(bsp, main),
            stop_loss=self._stop_loss(bsp, price, zs_low, zs_high),
            take_profit=self._take_profit(bsp, price, zs_low, zs_high),
            description=self._description(bsp, main, subs, price),
        )

    @staticmethod
    def _confidence(bsp: _BSPoint, main_type: str) -> float:
        if main_type in (T1, T1P):
            return _rate_confidence(bsp.features.get("divergence_rate"))
        if main_type == T2:
            rate = bsp.features.get("bsp2_retrace_rate")
            return round(max(0.5, min(0.85, 0.8 - 0.3 * (rate if rate is not None else 0.5))), 2)
        if main_type == T2S:
            rate = bsp.features.get("bsp2s_retrace_rate")
            return round(max(0.45, min(0.8, 0.75 - 0.3 * (rate if rate is not None else 0.5))), 2)
        return 0.8 if main_type == T3A else 0.75

    @staticmethod
    def _stop_loss(bsp: _BSPoint, price: float, zs_low, zs_high) -> Optional[float]:
        if bsp.is_buy:
            ref = zs_low if zs_low is not None else price
            return float(min(price, ref) * 0.98)
        ref = zs_high if zs_high is not None else price
        return float(max(price, ref) * 1.02)

    @staticmethod
    def _take_profit(bsp: _BSPoint, price: float, zs_low, zs_high) -> Optional[float]:
        if bsp.is_buy:
            return float(zs_high) if zs_high is not None else round(price * 1.06, 4)
        return float(zs_low) if zs_low is not None else round(price * 0.94, 4)

    @staticmethod
    def _description(bsp: _BSPoint, main_type: str, subs: str, price: float) -> str:
        parts = [_main_name(main_type, bsp.is_buy)]
        if subs:
            parts.append(subs)
        rate = bsp.features.get("divergence_rate")
        if rate is not None:
            parts.append(f"背驰比{rate:.2f}")
        retrace = bsp.features.get("bsp2_retrace_rate", bsp.features.get("bsp2s_retrace_rate"))
        if retrace is not None:
            parts.append(f"回撤{retrace:.2f}")
        zs_cnt = bsp.features.get("zs_cnt")
        if zs_cnt is not None:
            parts.append(f"中枢{zs_cnt}个")
        return f"{parts[0]}({' '.join(parts[1:])}) @ {price:.2f}" if len(parts) > 1 else f"{parts[0]} @ {price:.2f}"


def _rate_confidence(rate) -> float:
    """背驰比越小力度衰减越明显，置信度越高。"""
    if rate is None:
        return 0.6
    if rate <= 0.5:
        return 0.9
    if rate <= 0.7:
        return 0.85
    if rate <= 0.9:
        return 0.8
    if rate <= 1.0:
        return 0.7
    return 0.6


# [冗余-已注释] B1: 与 base.has_overlap 逐字重复，改用 base 版本（见下方 import）
# def _has_overlap(l1: float, h1: float, l2: float, h2: float, equal: bool = False) -> bool:
#     return (h2 >= l1 and h1 >= l2) if equal else (h2 > l1 and h1 > l2)
from .base import has_overlap as _has_overlap  # noqa: E402  (B1: 复用 base 实现)


def _bsp2s_break_bsp1(bsp2s_bi, bsp2_break_bi) -> bool:
    return (bsp2s_bi.is_down() and bsp2s_bi._low() < bsp2_break_bi._low()) or \
        (bsp2s_bi.is_up() and bsp2s_bi._high() > bsp2_break_bi._high())


def _bsp3_back2zs(bsp3_bi, zs) -> bool:
    """回踩又回到中枢内部 —— 三买/三卖不成立。"""
    return (bsp3_bi.is_down() and bsp3_bi._low() < zs.high) or \
        (bsp3_bi.is_up() and bsp3_bi._high() > zs.low)


def _bsp3_break_zspeak(bsp3_bi, zs) -> bool:
    return (bsp3_bi.is_down() and bsp3_bi._high() >= zs.peak_high) or \
        (bsp3_bi.is_up() and bsp3_bi._low() <= zs.peak_low)


def _cal_bsp3_bi_end_idx(seg):
    if seg is None:
        return float("inf")
    if seg.get_multi_bi_zs_cnt() == 0 and seg.next is None:
        return float("inf")
    end_bi_idx = seg.end_bi.idx - 1
    for zs in seg.zs_lst:
        if zs.is_one_bi_zs():
            continue
        if zs.bi_out is not None:
            end_bi_idx = zs.bi_out.idx
            break
    return end_bi_idx


class SignalDetector:
    """
    三类买卖点判定（chan.py BuySellPoint 口径）。

    一买/一卖：线段末笔走出段内最后一个中枢且力度背驰（趋势背驰），
              段内无多笔中枢时退化为末笔与前一同向笔的力度比较（盘整背驰）。
    二买/二卖：一买之后「突破笔」的下一笔回踩，回撤幅度 <= max_bs2_rate。
    三买/三卖：出中枢后回踩不回到中枢内（3a 中枢在一买之后 / 3b 中枢在一买之前）。
    """

    def __init__(self, bis: list[Bi],
                 segments: list[XianSegment],
                 zhongshus: list[Zhongshu],
                 level: str = "daily",
                 config: Optional[BSPConfig] = None,
                 bi_lines: Optional[list] = None,
                 seg_list=None,
                 macd_series=None):
        self.bis = bis
        self.segments = segments
        self.zhongshus = zhongshus
        self.level = level
        self.config = config or BSPConfig()
        # 买卖点必须在内部结构（笔序列 + 段内中枢 + 力度序列）上算，
        # 只有 pydantic 输出模型时信息不足，返回空列表而不是硬凑信号
        self.bi_lines = bi_lines
        self.seg_list = seg_list
        self.macd_series = macd_series

    def detect_all(self) -> list[BuySellPoint]:
        """检测所有买卖点。"""
        if not self.bi_lines or self.seg_list is None:
            return []
        bsp_list = BSPointList(self.config, self.macd_series)
        bsp_list.cal(self.bi_lines, self.seg_list)
        signals = bsp_list.to_models(self.level)
        return sorted(signals, key=lambda s: s.datetime)

    def detect_trend(self) -> str:
        """判断当前走势类型。

        中枢数量不再作为判据 —— chan.py 口径下中枢数量天然偏多，
        改用最近若干线段的高低点是否逐级抬高/降低来判定趋势。
        """
        if not self.segments:
            return "未知"
        recent = self.segments[-3:]
        if len(recent) >= 2:
            highs = [s.high for s in recent]
            lows = [s.low for s in recent]
            if all(highs[i] > highs[i - 1] and lows[i] > lows[i - 1]
                   for i in range(1, len(recent))):
                return "上涨"
            if all(highs[i] < highs[i - 1] and lows[i] < lows[i - 1]
                   for i in range(1, len(recent))):
                return "下跌"

        downs = sum(1 for s in recent if s.direction == "down")
        ups = sum(1 for s in recent if s.direction == "up")
        if downs > ups:
            return "下跌"
        if ups > downs:
            return "上涨"
        return "盘整"

    def detect_support_resistance(self, klines: list, signals: list[BuySellPoint]) -> list[SupportResistanceLevel]:
        """
        计算支撑位和阻力位

        支撑位来源:
        - 中枢下沿 (resistance=支撑)
        - 笔低点 / 线段低点 (support)
        - 历史K线低点附近 (support)
        - 买卖点价位 (signal)

        阻力位来源:
        - 中枢上沿 (resistance)
        - 笔高点 / 线段高点 (resistance)
        - 历史K线高点附近 (resistance)
        """
        levels: list[SupportResistanceLevel] = []

        # ── 1. 中枢 ──────────────────────────────
        for zs in self.zhongshus:
            levels.append(SupportResistanceLevel(
                type="support",
                price=zs.range_low,
                source="zhongshu",
                related_id=zs.id,
                datetime=zs.start,
                strength=0.85
            ))
            levels.append(SupportResistanceLevel(
                type="resistance",
                price=zs.range_high,
                source="zhongshu",
                related_id=zs.id,
                datetime=zs.start,
                strength=0.85
            ))

        # ── 2. 笔 ───────────────────────────────
        # 只取最近10笔，太多反而干扰
        recent_bis = self.bis[-10:]
        for b in recent_bis:
            if b.direction == "down":
                levels.append(SupportResistanceLevel(
                    type="support",
                    price=b.low,
                    source="bi_low",
                    related_id=b.id,
                    datetime=b.end,
                    strength=0.6
                ))
            else:
                levels.append(SupportResistanceLevel(
                    type="resistance",
                    price=b.high,
                    source="bi_high",
                    related_id=b.id,
                    datetime=b.end,
                    strength=0.6
                ))

        # ── 3. 线段 ─────────────────────────────
        recent_xians = self.segments[-6:]
        for x in recent_xians:
            if x.direction == "down":
                levels.append(SupportResistanceLevel(
                    type="support",
                    price=x.low,
                    source="kline_low",
                    related_id=x.id,
                    datetime=x.end,
                    strength=0.75
                ))
            else:
                levels.append(SupportResistanceLevel(
                    type="resistance",
                    price=x.high,
                    source="kline_high",
                    related_id=x.id,
                    datetime=x.end,
                    strength=0.75
                ))

        # ── 4. 买卖点 ────────────────────────────
        for sig in signals:
            if "买" in sig.type:
                levels.append(SupportResistanceLevel(
                    type="support",
                    price=sig.price,
                    source="signal",
                    related_id="",
                    datetime=sig.datetime,
                    strength=sig.confidence
                ))
            else:
                levels.append(SupportResistanceLevel(
                    type="resistance",
                    price=sig.price,
                    source="signal",
                    related_id="",
                    datetime=sig.datetime,
                    strength=sig.confidence
                ))

        # ── 5. 近期K线高低价 ─────────────────────
        if klines:
            recent_kl = klines[-20:]
            lows = [k for k in recent_kl if k.get("low")]
            highs = [k for k in recent_kl if k.get("high")]
            if lows:
                min_kl = min(lows, key=lambda x: x.get("low", float("inf")))
                levels.append(SupportResistanceLevel(
                    type="support",
                    price=float(min_kl.get("low")),
                    source="kline_low",
                    related_id="",
                    datetime=min_kl.get("date") if isinstance(min_kl, dict) else min_kl.date,
                    strength=0.5
                ))
            if highs:
                max_kl = max(highs, key=lambda x: x.get("high", float("-inf")))
                levels.append(SupportResistanceLevel(
                    type="resistance",
                    price=float(max_kl.get("high")),
                    source="kline_high",
                    related_id="",
                    datetime=max_kl.get("date") if isinstance(max_kl, dict) else max_kl.date,
                    strength=0.5
                ))

        # ── 去重：相同价格 ±0.5% 内的只保留最强的 ─────
        deduped: list[SupportResistanceLevel] = []
        seen_prices: dict[str, int] = {}  # key -> index in deduped

        def price_key(p: float) -> str:
            return f"{p:.2f}"

        for lvl in levels:
            pk = price_key(lvl.price)
            if pk not in seen_prices:
                seen_prices[pk] = len(deduped)
                deduped.append(lvl)
            else:
                existing = deduped[seen_prices[pk]]
                if lvl.strength > existing.strength:
                    deduped[seen_prices[pk]] = lvl

        # 按强度降序返回
        return sorted(deduped, key=lambda x: -x.strength)
