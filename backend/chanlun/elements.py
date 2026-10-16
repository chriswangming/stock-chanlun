"""
缠论数据结构定义模块。

本模块用 pydantic 定义了缠论分析链路上各个环节的数据模型，是整个 ``chanlun``
包对外的数据契约层，对应链路如下::

    KLine(原始K线) → Bi(笔) → XianSegment(线段) → Zhongshu(中枢)
                                    ↓
                    BuySellPoint(买卖点) / SupportResistanceLevel(支撑阻力)
                                    ↓
                              ChanlunAnalysis(完整分析结果)

Note:
    本模块只负责「数据长什么样」与字段级校验（如 confidence 的 0~1 约束），
    不含任何计算逻辑。计算分别由 fenxing_detector / bi_detector /
    segment_detector / signals 完成，由 engine 统一编排。
"""

from dataclasses import dataclass, field
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Literal, Optional


class KLine(BaseModel):
    """
    单根K线数据。

    链路位置：最底层的输入单元，同时也是 ``analyze()`` 对外返回的原始行情序列。

    Note:
        这里保存的是**未做包含处理**的原始 OHLC；而笔、中枢内部计算使用的是
        包含合并后的序列，两者下标不可混用（详见 ``engine._to_klines`` 注释）。
    """
    # K线时间戳（日线=交易日，分钟线=Bar 起始时间），全序列按此升序排列
    date: datetime
    # 开盘价
    open: float
    # 最高价（分型、笔的极值判定都基于 high/low）
    high: float
    # 最低价
    low: float
    # 收盘价
    close: float
    # 成交量（单位取决于数据源口径：股 / 手）
    volume: float
    amount: Optional[float] = 0.0  # 成交额
    # 该K线在**原始（清洗后）序列**中的下标。
    # 算法内部使用：合并K线的 start_idx / end_idx 要靠它定位到原始K线。
    # exclude=True —— 不进 API 响应，前端拿到的数据结构保持不变。
    idx: int = Field(default=-1, exclude=True)

class IncludedRaw(BaseModel):
    """被合并进合并K线的原始K线（chan.py 里画成蓝色的那部分）。"""
    date: datetime
    high: float
    low: float

# @dataclass
# class MergedKLine:
#     """
#     包含处理后的**合并K线**（算法内部对象，对应 chan.py 的 ``CKLine``）。

#     与 :class:`MergedKLineInfo` 的区别：本对象是计算期用的活对象，持有原始
#     KLine 引用；``MergedKLineInfo`` 是序列化给前端的扁平 DTO，由
#     ``engine._to_merged_klines`` 负责两者之间的转换。

#     Attributes:
#         lst: 被合并进来的原始K线（按时间升序，至少 1 根）。保留它是关键 ——
#             老实现只留区间高低点，把「谁被谁包住了」这个信息丢掉了，
#             前端就无法画出 chan.py 那种蓝色被包含K线。
#         high / low: 合并后的区间高低点。注意**不是** ``lst`` 里的最高/最低，
#             而是按合并方向「削掉」被包住部分之后的结果。
#         dir: 合并方向，决定 high/low 取 max 还是 min。
#         fx: 该合并K线的分型（``detect()`` 阶段回填，初始为 None）。
#         date: 合并K线的「代表日期」。沿用老实现的规则：只有「前一根包住当前根」
#             时才把日期推进到当前根，否则沿用前一根日期；分型与笔的端点日期取它。
#         open / close: 区间首根的开盘、区间末根的收盘。
#         volume: 区间内所有原始K线的成交量之和。
#     """
#     lst: list = field(default_factory=list)
#     high: float = 0.0
#     low: float = 0.0
#     dir: Literal["up", "down"] = "up"
#     fx: Optional[Literal["top", "bottom"]] = None
#     date: Optional[datetime] = None
#     open: float = 0.0
#     close: float = 0.0
#     volume: float = 0.0

#     @property
#     def start_idx(self) -> int:
#         """首根原始K线在原始序列中的下标（空区间时为 -1）。"""
#         return self.lst[0].idx if self.lst else -1

#     @property
#     def end_idx(self) -> int:
#         """末根原始K线在原始序列中的下标（空区间时为 -1）。"""
#         return self.lst[-1].idx if self.lst else -1


class MergedKLine(BaseModel):
    """包含处理后的合并K线（chan.py 的 CKLine），供前端在主图上画出合并区间。

    start/end 是该合并K线覆盖的原始K线首/尾日期；count 为被合并进来的原始K线根数。
    画法参考 chan.py 出图：dir=up 红空心框 / dir=down 绿空心框，
    included 里的被包含K线用蓝色细线画真实高低范围。
    """
    start: datetime
    end: datetime
    start_idx: int = -1          # 首根原始K线在（清洗后）序列里的下标
    end_idx: int = -1            # 末根原始K线下标
    high: float                  # 合并后的区间高点
    low: float                   # 合并后的区间低点
    dir: Literal["up", "down"] = "up"   # 合并方向（决定高低点如何取）
    count: int = 1               # 包含多少根原始K线
    fx: Optional[Literal["top", "bottom"]] = None  # 该合并K线的分型
    # 被包含进来的原始K线（不含首根）：count>1 时非空，前端画蓝色高低线
    included: list[IncludedRaw] = []

class Fenxing(BaseModel):
    """
    一个顶分型或底分型（三根相邻**合并K线**构成的价格转折点）。

    Attributes:
        date: 中间那根合并K线的``start``日期，即这段合并区间的起始日，
            用于与原始K线对齐。注意它不是「中间那根原始K线」的日期。
        type: "top"=顶分型（阶段高点），"bottom"=底分型（阶段低点）。
        high / low: 中间那根合并K线的高低点——判定分型时就是拿它跟
            左右两根比（见 :meth:`FenxingDetector.detect`）。
        index: 该分型在**合并后序列**中的下标。

    Note:
        ``index`` 是**包含合并后序列**中的下标，**不是**原始K线的位置。
        合并会让序列变短，因此不能用它在原始K线列表里直接取元素。
    """
    # 分型中间那根合并K线的起始日期（用于与原始K线对齐）
    date: datetime
    # 分型类型："top"=顶分型，"bottom"=底分型
    type: Literal["top", "bottom"]
    # 分型中间合并K线的最高价
    high: float
    # 分型中间合并K线的最低价
    low: float
    # 该分型在**合并后序列**中的下标（易踩坑，见类文档 Note）
    index: int

class Bi(BaseModel):
    """
    笔 — 缠论最小结构单元，由一个底分型与一个顶分型（或反向）连接而成。

    链路位置：原始K线 → 包含处理 → 分型 → **笔** → 线段 → 中枢 的第四环。

    Note:
        ``start`` / ``end`` 直接取自两端分型的日期。向上笔 ``direction="up"``
        表示「底→顶」，其 start_price 取底分型 low、end_price 取顶分型 high；
        向下笔反之。
    """
    # 笔ID，形如 "bi_up_1" / "bi_down_2"，在一次分析窗口内唯一
    id: str
    # 笔起点时间（起点分型的日期）
    start: datetime
    # 笔终点时间（终点分型的日期）
    end: datetime
    # 笔方向："up"=底→顶（上涨），"down"=顶→底（下跌）
    direction: Literal["up", "down"]
    # 笔覆盖区间的最高价
    high: float
    # 笔覆盖区间的最低价
    low: float
    start_price: float  # 笔起点价格
    end_price: float   # 笔终点价格


class XianSegment(BaseModel):
    """
    线段 — 由至少 3 笔相互重叠构成的中级结构，是中枢的组成单元。

    链路位置：原始K线 → 包含处理 → 分型 → 笔 → **线段** → 中枢 的第五环。
    """
    # 线段ID，形如 "xian_1"
    id: str
    # 线段起点时间（首笔起点）
    start: datetime
    # 线段终点时间（末笔终点，含延伸后并入的笔）
    end: datetime
    # 线段方向：与其首笔方向一致
    direction: Literal["up", "down"]
    # 线段覆盖区间的最高价
    high: float
    # 线段覆盖区间的最低价
    low: float
    # 线段起点价格（首笔的 start_price）
    start_price: float = 0.0
    # 线段终点价格（末笔的 end_price）
    end_price: float = 0.0
    bi_ids: list[str]  # 组成该线段的笔ID列表
    level: int = 2     # 1=笔级, 2=段级, 3=更大级别


class Zhongshu(BaseModel):
    """
    中枢 — 至少 3 个连续线段的价格重叠区域，是判断趋势与三买/三卖的核心参照。

    链路位置：原始K线 → 包含处理 → 分型 → 笔 → 线段 → **中枢** 的第六环，
    其输出直接供 ``signals`` 判定三买/三卖与支撑阻力。

    Note:
        中枢区间在延伸时取的是**交集**（不断收窄），不是并集，
        这与一般直觉相反，详见 ``segment_detector.detect_zhongshus``。
    """
    # 中枢ID，形如 "zs_1"
    id: str
    # 中枢起始时间（第一个构成线段的起点）
    start: datetime
    # 中枢结束时间（延伸并入的最后一个线段的终点）
    end: datetime
    # 中枢区间上沿 = 各构成线段 high 的 min（交集），随延伸只会下移
    range_high: float  # 中枢区间最高价
    # 中枢区间下沿 = 各构成线段 low 的 max（交集），随延伸只会上升
    range_low: float   # 中枢区间最低价
    xian_ids: list[str]  # 构成该中枢的线段ID
    # 中枢级别，沿用其构成线段的 level
    level: int


class MACDData(BaseModel):
    """
    MACD指标数据，承载标准 MACD 的三个序列值。

    Note:
        目前的背驰判定（``signals``）并未走标准 MACD 面积法，而是用
        「(high-low) / 时间秒数」的简化力度近似。本模型保留用于后续
        接入标准 MACD 面积背驰时承载指标值。
    """
    # 快线 DIF = 12日EMA - 26日EMA
    dif: float
    # 慢线 DEA = DIF 的 9日EMA
    dea: float
    bar: float  # MACD柱 = (dif - dea) * 2


class PowerMetrics(BaseModel):
    """
    力度指标（用于背驰判断）的数据容器。

    Note:
        当前 ``signals`` 模块并未真正实例化本模型做背驰计算，那里用的是
        「(high-low) / 时间秒数」的简化近似。保留本模型以便后续把标准
        MACD 面积、涨跌幅、斜率、RSI 统一接入力度评估。
    """
    macd_area: float      # 同向段MACD面积
    price_change: float    # 价格变化幅度
    slope: float           # 均线斜率
    # RSI，可选（数据源缺失时为 None）
    rsi: Optional[float] = None


class BuySellPoint(BaseModel):
    """
    买卖点。

    ``type`` 的六个取值即缠论三类买卖点：
    一买 / 二买 / 三买 属下跌侧（买入），一卖 / 二卖 / 三卖 属上涨侧（卖出）。

    Note:
        ``price`` 对买点取低点、对卖点取高点；``datetime`` 取触发结构的
        结束时间，因此买卖点总是「事后确认」的，不会早于该结构完成。
    """
    # 买卖点类型：一买 / 二买 / 三买 / 一卖 / 二卖 / 三卖
    type: Literal["一买", "二买", "三买", "一卖", "二卖", "三卖"]
    level: str            # 如 "30min", "daily"
    # 触发价格（买点=结构低点，卖点=结构高点）
    price: float
    # 触发时间
    datetime: datetime
    confidence: float = Field(ge=0.0, le=1.0)  # 置信度 0-1
    stop_loss: Optional[float] = None   # 止损价
    take_profit: Optional[float] = None # 止盈价
    # 人类可读的判定说明，便于前端直接展示
    description: str = ""


class SupportResistanceLevel(BaseModel):
    """
    支撑位 / 阻力位。

    由 ``SignalDetector.detect_support_resistance`` 从 5 类来源汇总：
    中枢上下沿、笔高低点、线段高低点、买卖点价位、近期K线极值。

    Note:
        同一价位（按两位小数判定）若来自多个来源，只保留 strength 最高的那条，
        详见 ``detect_support_resistance`` 的去重逻辑。
    """
    # 方向："support"=支撑（价格下方的托底），"resistance"=阻力（价格上方的压制）
    type: Literal["support", "resistance"]
    # 具体价位
    price: float
    # 来源说明
    source: Literal["zhongshu", "bi_high", "bi_low", "kline_high", "kline_low", "signal"]
    # 哪段历史笔 / 中枢产生的（便于定位）
    related_id: str = ""
    # 该价位形成的时间
    datetime: datetime
    strength: float = Field(ge=0.0, le=1.0)  # 强度 0-1，越高越重要


class ChanlunAnalysis(BaseModel):
    """
    完整缠论分析结果，是 ``ChanlunEngine.analyze()`` 的返回值，也是对外的响应体。

    Note:
        ``klines`` 为原始K线序列（未做包含处理），而 ``bis`` / ``zhongshus``
        是基于包含合并后序列算出来的；前端靠日期做映射（``dateToIdxRobust``），
        不能直接用下标对齐。
    """
    # 股票代码；引擎本身不解析代码，由上层填充，单序列分析时为空串
    stock_code: str
    # 分析级别，如 "daily" / "30min"
    level: str
    # 原始K线序列
    klines: list[KLine]
    merged_klines: list[MergedKLine]
    fenxings: list[Fenxing]
    # 识别出的笔
    bis: list[Bi]
    # 识别出的线段
    xians: list[XianSegment]
    # 识别出的中枢
    zhongshus: list[Zhongshu]
    # 买卖点（按时间升序）
    signals: list[BuySellPoint]
    # 当前走势：上涨 / 下跌 / 盘整 / 未知
    trend: Literal["上涨", "下跌", "盘整", "未知"]
    summary: str  # 自然语言总结
    # 支撑阻力位（按强度降序）
    support_resistance: list[SupportResistanceLevel] = []
    # 包含处理后的合并K线（与 klines 同源、等长或更短），用于前端画合并区间
    merged_klines: list[MergedKLine] = []


class StockInfo(BaseModel):
    """
    股票基本信息。

    与缠论计算链路无关，属于标的元数据，供选股 / 展示层使用。
    """
    # 证券代码
    code: str
    # 证券简称
    name: str
    exchange: str       # 上交所/深交所
    # 所属行业
    industry: Optional[str] = None
    market_cap: Optional[float] = None  # 总市值(万)


class AiSignal(BaseModel):
    """
    AI策略信号 — 在缠论买卖点之上叠加方向、风险与持仓建议的复合输出。

    与本模块其它模型不同，它属于「策略层」而非「结构层」：先由结构层产出
    ``signals``（缠论买卖点），再综合成一条可执行的建议。
    """
    # 股票代码
    stock_code: str
    # 分析级别
    level: str
    # 操作方向：买入 / 卖出 / 观望
    direction: Literal["买入", "卖出", "观望"]
    # 信号置信度 0-1
    confidence: float = Field(ge=0.0, le=1.0)
    # 风险等级：高 / 中 / 低
    risk_level: Literal["高", "中", "低"]
    # 建议入场价
    entry_price: Optional[float] = None
    # 建议止损价
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None # 止盈价
    holding_period: str = ""  # 如 "1-3天", "1-2周"
    multi_level_resonance: Optional[list[str]] = None  # 多级别共振信号
    # 人类可读的信号说明
    description: str = ""
    signals: list[BuySellPoint]  # 关联的买卖点列表
