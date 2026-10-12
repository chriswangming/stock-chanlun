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


class XianSegment(BaseModel):
    """线段 — 由笔构成"""
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


class Zhongshu(BaseModel):
    """中枢 — 价格重叠区域"""
    id: str
    start: datetime
    end: datetime
    range_high: float  # 中枢区间最高价
    range_low: float   # 中枢区间最低价
    xian_ids: list[str]  # 构成该中枢的线段ID
    level: int


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
