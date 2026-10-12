from .elements import (
    KLine, Bi, XianSegment, Zhongshu, BuySellPoint,
    ChanlunAnalysis, AiSignal
)

__all__ = [
    "KLine", "Bi", "XianSegment", "Zhongshu", "BuySellPoint",
    "ChanlunAnalysis", "AiSignal"
]

# [冗余-已注释] A6: ScreenResult 零引用（选股结果走 services/screening_service 的结构）
# 若需恢复，同时补回 `from pydantic import BaseModel` 与 `from typing import Optional`
# class ScreenResult(BaseModel):
#     """选股结果条目"""
#     code: str
#     name: str
#     price: float
#     change_pct: float
#     volume: float
#     amount: float
#     industry: Optional[str] = None
#     pe: Optional[float] = None
#     pb: Optional[float] = None
#     latest_signal: Optional[str] = None          # 如 "一买", "二卖"
#     latest_signal_date: Optional[str] = None      # YYYY-MM-DD
#     latest_signal_conf: Optional[float] = None
#     has_dual_cross: bool = False
#     dual_cross_date: Optional[str] = None
#     trend: str = "未知"
