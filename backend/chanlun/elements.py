from pydantic import BaseModel, Field
from datetime import datetime
from typing import Literal, Optional


class KLine(BaseModel):
    """单根K线数据"""
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: Optional[float] = 0.0  # 成交额


class IncludedRaw(BaseModel):
    """被合并进合并K线的原始K线（chan.py 里画成蓝色的那部分）。"""
    date: datetime
    high: float
    low: float


class MergedKLineInfo(BaseModel):
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


class Bi(BaseModel):
    """笔 — 缠论最小结构单元"""
    id: str
    start: datetime
    end: datetime
    direction: Literal["up", "down"]
    high: float
    low: float
    start_price: float  # 笔起点价格
    end_price: float   # 笔终点价格
    # chan.py CBi.is_sure / CBi.used_to_be_sure 两套标记：
    # 虚笔若只是把已确认的笔「延长」，则 is_sure=False 但 used_to_be_sure=True，
    # 线段/中枢判定用的是 is_used_to_be_sure = is_sure or used_to_be_sure
    is_sure: bool = True
    used_to_be_sure: bool = True


class XianSegment(BaseModel):
    """线段 — 由笔构成（chan.py Seg/Seg.py，默认特征序列法）"""
    id: str
    start: datetime
    end: datetime
    direction: Literal["up", "down"]
    high: float
    low: float
    start_price: float = 0.0
    end_price: float = 0.0
    bi_ids: list[str]  # 组成该线段的笔ID列表
    level: int = 2     # 1=笔级, 2=段级, 3=更大级别
    is_sure: bool = True   # 是否已确认（尾部线段多为未确认）


class Zhongshu(BaseModel):
    """中枢 — 价格重叠区域（chan.py ZS/ZS.py）

    由连续奇数个次级别（笔）的重叠区间构成，区间为 ZD=max(low) ~ ZG=min(high)。
    """
    id: str
    start: datetime
    end: datetime
    range_high: float  # 中枢区间最高价 ZG
    range_low: float   # 中枢区间最低价 ZD
    xian_ids: list[str] = []   # 构成该中枢的线段ID（跨段时会多个）
    bi_ids: list[str] = []     # 构成该中枢的笔ID
    bi_cnt: int = 0            # 中枢跨越的笔数（chan.py 段内中枢恒为奇数）
    center: float = 0.0        # 中枢中位价 (ZG+ZD)/2
    is_sure: bool = True
    level: int = 2


class MACDData(BaseModel):
    """MACD指标数据"""
    dif: float
    dea: float
    bar: float  # MACD柱 = (dif - dea) * 2


class PowerMetrics(BaseModel):
    """力度指标（用于背驰判断）"""
    macd_area: float      # 同向段MACD面积
    price_change: float    # 价格变化幅度
    slope: float           # 均线斜率
    rsi: Optional[float] = None


class BuySellPoint(BaseModel):
    """买卖点"""
    type: Literal["一买", "二买", "三买", "一卖", "二卖", "三卖"]
    level: str            # 如 "30min", "daily"
    price: float
    datetime: datetime
    confidence: float = Field(ge=0.0, le=1.0)  # 置信度 0-1
    stop_loss: Optional[float] = None   # 止损价
    take_profit: Optional[float] = None # 止盈价
    description: str = ""


class SupportResistanceLevel(BaseModel):
    """支撑位 / 阻力位"""
    type: Literal["support", "resistance"]
    price: float
    # 来源说明
    source: Literal["zhongshu", "bi_high", "bi_low", "kline_high", "kline_low", "signal"]
    # 哪段历史笔 / 中枢产生的（便于定位）
    related_id: str = ""
    datetime: datetime
    strength: float = Field(ge=0.0, le=1.0)  # 强度 0-1，越高越重要


class ChanlunAnalysis(BaseModel):
    """完整缠论分析结果"""
    stock_code: str
    level: str
    klines: list[KLine]
    bis: list[Bi]
    xians: list[XianSegment]
    zhongshus: list[Zhongshu]
    signals: list[BuySellPoint]
    trend: Literal["上涨", "下跌", "盘整", "未知"]
    summary: str  # 自然语言总结
    support_resistance: list[SupportResistanceLevel] = []
    # 包含处理后的合并K线（与 klines 同源、等长或更短），用于前端画合并区间
    merged_klines: list[MergedKLineInfo] = []


class StockInfo(BaseModel):
    """股票基本信息"""
    code: str
    name: str
    exchange: str       # 上交所/深交所
    industry: Optional[str] = None
    market_cap: Optional[float] = None  # 总市值(万)


class AiSignal(BaseModel):
    """AI策略信号"""
    stock_code: str
    level: str
    direction: Literal["买入", "卖出", "观望"]
    confidence: float = Field(ge=0.0, le=1.0)
    risk_level: Literal["高", "中", "低"]
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None # 止盈价
    holding_period: str = ""  # 如 "1-3天", "1-2周"
    multi_level_resonance: Optional[list[str]] = None  # 多级别共振信号
    description: str = ""
    signals: list[BuySellPoint]  # 关联的买卖点列表
