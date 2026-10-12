"""
缠论计算配置 —— 参数语义与默认值对齐 chan.py 的 CChanConfig。

各字段说明直接摘自 chan.py README「CChanConfig 配置」章节。
"""

import math
from dataclasses import dataclass, field
from typing import Literal, Optional


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


# chan.py BuySellPoint/BSPointConfig.py：买卖点参数（买/卖各一套）
BSP_TYPES = ("1", "1p", "2", "2s", "3a", "3b")


@dataclass
class BSPointConfig:
    """单侧（买或卖）买卖点配置 —— 字段语义与默认值同 chan.py CPointConfig。"""

    # 背驰阈值：出中枢笔力度 / 进中枢笔力度 <= 该值才算背驰；>100 表示「保送」
    divergence_rate: float = math.inf
    # 线段内至少要有几个中枢，一买/一卖才算「目标买卖点」
    min_zs_cnt: int = 1
    # True: 只统计多笔中枢（一笔中枢不计入 min_zs_cnt）
    bsp1_only_multibi_zs: bool = True
    # 二买的最大回撤比例（二买笔幅度 / 突破笔幅度）
    max_bs2_rate: float = 0.9999
    # 力度比较算法，见 chanlun/macd.py
    macd_algo: str = "peak"
    # 一买/一卖是否要求出中枢笔为中枢内极值
    bs1_peak: bool = True
    # 参与计算的买卖点类型集合
    bs_type: str = "1,1p,2,2s,3a,3b"
    # 二买是否必须紧跟一买
    bsp2_follow_1: bool = True
    # 三买是否必须紧跟一买
    bsp3_follow_1: bool = True
    # 三买是否要求突破中枢极值
    bsp3_peak: bool = False
    # 类二买是否必须已有二买
    bsp2s_follow_2: bool = False
    # 类二买的最大层数，None 表示不限
    max_bsp2s_lv: Optional[int] = None
    # 三买是否严格要求中枢紧邻一买
    strict_bsp3: bool = False
    # 3a 最多考察几个中枢
    bsp3a_max_zs_cnt: int = 1

    @property
    def target_types(self) -> tuple[str, ...]:
        types = tuple(t.strip() for t in self.bs_type.split(",") if t.strip())
        return types or BSP_TYPES


@dataclass
class BSPConfig:
    """买卖点总配置 —— 对应 chan.py CBSPointConfig（买/卖两套）。"""

    buy: BSPointConfig = field(default_factory=BSPointConfig)
    sell: BSPointConfig = field(default_factory=BSPointConfig)

    def get(self, is_buy: bool) -> BSPointConfig:
        return self.buy if is_buy else self.sell


@dataclass
class ChanlunConfig:
    bi: BiConfig = field(default_factory=BiConfig)
    seg: SegConfig = field(default_factory=SegConfig)
    zs: ZSConfig = field(default_factory=ZSConfig)
    bsp: BSPConfig = field(default_factory=BSPConfig)
