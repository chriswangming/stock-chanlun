"""
AI 策略信号的输出契约。

本模块从 ``chanlun/elements.py`` 迁来。原文件曾定义缠论链路上所有的数据模型
（KLine / Bi / XianSegment / Zhongshu / BuySellPoint ……），但随着计算引擎换成
``newchan``，那些模型已全部作废并被清理；只剩 ``AiSignal`` 还在被策略层使用，
因此把它单独搬到这里，紧挨着它的生产者 ``strategy_engine``。

Note:
    ``signals`` 里放的是 newchan 的 ``CBS_Point`` 实例 —— 缠论引擎实际产出的买卖点
    对象，自带 KLU、类型列表与特征表。这里不重新造一份镜像模型，直接引用它。
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from newchan.BuySellPoint.BS_Point import CBS_Point


class AiSignal(BaseModel):
    """
    AI策略信号 — 在缠论买卖点之上叠加方向、风险与持仓建议的复合输出。

    属于「策略层」而非「结构层」：先由结构层产出买卖点（``CBS_Point``），
    再由 ``StrategyEngine`` 综合成一条可执行的建议。
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
    take_profit: Optional[float] = None  # 止盈价
    holding_period: str = ""  # 如 "1-3天", "1-2周"
    multi_level_resonance: Optional[list[str]] = None  # 多级别共振信号
    # 人类可读的信号说明
    description: str = ""

    # CBS_Point 不是 pydantic 模型，pydantic v2 默认拒绝把它当字段类型，
    # 打开 arbitrary_types_allowed 后会退化成 isinstance 校验（只验类型不解结构）
    model_config = ConfigDict(arbitrary_types_allowed=True)

    # 关联的买卖点列表，元素是 newchan 的 CBS_Point
    signals: list[CBS_Point]
