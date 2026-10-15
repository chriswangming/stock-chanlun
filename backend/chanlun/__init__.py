"""
``chanlun`` 包 — 缠论技术分析的计算与数据契约层。

对外只暴露两样东西：

1. 数据模型（来自 ``elements``）：``KLine`` / ``Bi`` / ``XianSegment`` /
   ``Zhongshu`` / ``BuySellPoint`` / ``ChanlunAnalysis`` / ``MACDData`` /
   ``PowerMetrics`` / ``AiSignal`` / ``StockInfo``，即分析结果的数据契约；
2. 本包自带的 ``ScreenResult``：选股结果条目。

Note:
    具体算法（分型、笔、线段、中枢、买卖点）需要显式导入各自的检测器模块，
    本包 ``__init__`` 不导入它们，避免循环引用与不必要的启动开销。
    通常用 ``from chanlun.engine import ChanlunEngine`` 作为唯一入口。
"""

from pydantic import BaseModel
from typing import Optional

# 从 elements 统一 re-export，使调用方可以 `from chanlun import Bi` 而不必层层下钻
from .elements import (
    KLine, Bi, XianSegment, Zhongshu, BuySellPoint,
    ChanlunAnalysis, StockInfo, MACDData, PowerMetrics, AiSignal
)

# 显式声明对外 API；注意 ScreenResult 不在其中，需要时请直接 `from chanlun import ScreenResult`
__all__ = [
    "KLine", "Bi", "XianSegment", "Zhongshu", "BuySellPoint",
    "ChanlunAnalysis", "StockInfo", "MACDData", "PowerMetrics", "AiSignal"
]


class ScreenResult(BaseModel):
    """
    选股结果条目 — 一条「标的 + 行情 + 最近缠论信号」的扁平记录，用于选股列表展示。

    Note:
        这里的 ``latest_signal*`` 字段存的是**字符串 / 原始类型**而非
        ``BuySellPoint`` 对象，目的是让选股结果可以直接落表 / 序列化，
        不依赖结构层模型。
    """
    # 证券代码
    code: str
    # 证券简称
    name: str
    # 最新价
    price: float
    # 涨跌幅（%）
    change_pct: float
    # 成交量
    volume: float
    # 成交额
    amount: float
    # 所属行业
    industry: Optional[str] = None
    # 市盈率
    pe: Optional[float] = None
    # 市净率
    pb: Optional[float] = None
    latest_signal: Optional[str] = None          # 如 "一买", "二卖"
    latest_signal_date: Optional[str] = None      # YYYY-MM-DD
    latest_signal_conf: Optional[float] = None
    # 是否存在多级别（如日线 + 30分钟）信号共振
    has_dual_cross: bool = False
    # 共振发生日期（YYYY-MM-DD）
    dual_cross_date: Optional[str] = None
    # 当前走势：上涨 / 下跌 / 盘整 / 未知，默认"未知"
    trend: str = "未知"
