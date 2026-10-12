"""
缠论计算配置 —— 参数语义与默认值对齐 chan.py 的 CChanConfig。

各字段说明直接摘自 chan.py README「CChanConfig 配置」章节。
"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class BiConfig:
    """笔的配置。"""

    # normal: 按缠论笔定义来算；fx: 顶底分形即成笔
    bi_algo: Literal["normal", "fx"] = "normal"

    # True: 只用严格笔——两个分型之间至少间隔 4 根合并K线
    # False: 至少 3 根合并K线且中间夹 >=3 根原始K线
    is_strict: bool = True

    # 检查笔两端顶底分型是否成立（互不重叠）的方法
    #   strict  : 底分型最低点 < 顶分型3元素最低点的最小值，顶分型反之
    #   totally : 底分型3元素最高点 < 顶分型3元素最低点
    #   loss    : 底分型最低点 < 顶分型中间元素低点
    #   half    : 上升笔时底分型最低点 < 顶分型前两元素最低点最小值
    bi_fx_check: Literal["strict", "totally", "loss", "half"] = "strict"

    # 缺口是否处理成一根K线
    gap_as_kl: bool = False

    # 笔的尾部是否必须是整笔中最低/最高
    bi_end_is_peak: bool = True

    # 是否允许次高点成笔
    bi_allow_sub_peak: bool = True


@dataclass
class SegConfig:
    """线段的配置。"""

    # chan: 特征序列法；break: 线段破坏法；1+1: 都业华 1+1 终结法
    seg_algo: Literal["chan", "break", "1+1"] = "chan"
    # 剩余无法归入确定线段的笔如何处理
    #   all : 收集至最后一个方向正确的笔，成为一段
    #   peak: 若出现新的可靠极值则分成两段
    left_method: Literal["peak", "all"] = "peak"


@dataclass
class ZSConfig:
    """中枢的配置。"""

    # 相邻中枢是否合并
    need_combine: bool = True
    # zs  : 两中枢区间有重叠才合并
    # peak: 两中枢有K线重叠就合并
    zs_combine_mode: Literal["zs", "peak"] = "zs"
    # 是否计算只有一笔的中枢（分析趋势时用）
    one_bi_zs: bool = False
    # normal: 段内中枢；over_seg: 跨段中枢；auto: 确定段用 normal，其余用 over_seg
    zs_algo: Literal["normal", "over_seg", "auto"] = "normal"


@dataclass
class ChanlunConfig:
    bi: BiConfig = field(default_factory=BiConfig)
    seg: SegConfig = field(default_factory=SegConfig)
    zs: ZSConfig = field(default_factory=ZSConfig)
