"""数值安全转换工具：把数据源的脏值统一转成 JSON 合法的有限浮点数。

存在的原因有两个：

    1. 数据源（akshare / 东财）常返回 "--"、""、None 这类占位值，
       直接 float() 会抛异常，不该让一个坏字段打断整个响应。
    2. NaN / inf 序列化成 JSON 会得到非法字面量（NaN / Infinity），
       浏览器 JSON.parse 会直接抛错，必须在出参前拦掉。
"""
from __future__ import annotations

import math


def finite_float(x, default: float = 0.0) -> float:
    """把任意值转成有限浮点数，失败或非法时回落到 default。

    Args:
        x: 待转换的值，通常是行情字典里的字段（可能是 str / None / NaN）。
        default: 兜底值，默认 0.0。

    Returns:
        有限浮点数；NaN / inf / 不可解析的输入都返回 default。

    Note:
        NaN 的比较语义是「任何比较都为假」，若直接放进 JSON，
        前端拿到的会是 NaN 字面量，排序与图表都会出问题，故一律兜底。
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        # 数据源的 "--" / "" / None 等占位值，按缺失处理
        return default
    # 拦掉 NaN / inf：JSON 规范不支持这两个字面量
    return v if math.isfinite(v) else default
