from pydantic import BaseModel
from typing import Optional

from .elements import (
    KLine, Bi, XianSegment, Zhongshu, BuySellPoint,
    ChanlunAnalysis, StockInfo, MACDData, PowerMetrics, AiSignal
)

__all__ = [
    "KLine", "Bi", "XianSegment", "Zhongshu", "BuySellPoint",
    "ChanlunAnalysis", "StockInfo", "MACDData", "PowerMetrics", "AiSignal"
]


class ScreenResult(BaseModel):
    """选股结果条目"""
    code: str
    name: str
    price: float
    change_pct: float
    volume: float
    amount: float
    industry: Optional[str] = None
    pe: Optional[float] = None
    pb: Optional[float] = None
    latest_signal: Optional[str] = None          # 如 "一买", "二卖"
    latest_signal_date: Optional[str] = None      # YYYY-MM-DD
    latest_signal_conf: Optional[float] = None
    has_dual_cross: bool = False
    dual_cross_date: Optional[str] = None
    trend: str = "未知"
