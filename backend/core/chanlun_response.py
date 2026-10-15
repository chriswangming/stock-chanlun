"""缠论分析 API 响应序列化：ChanlunAnalysis → 前端 JSON。

位于 core 层的最外沿，只做「模型字段 → 前端约定字段名」的搬运与清洗：

    routers/chanlun_routes.py → core/chanlun_analysis.run_analysis()
      → 本模块 serialize_chanlun_analysis() → JSON

字段命名沿用前端已有约定（zhongshus / supportResistance 等驼峰混排），
改动字段名会直接打断前端渲染，需前后端同步。
"""
from __future__ import annotations

from chanlun.elements import ChanlunAnalysis
from core.chanlun_analysis import level_to_period


def serialize_chanlun_analysis(result: ChanlunAnalysis) -> dict:
    """将 ChanlunAnalysis 转为前端 JSON（含 K 线，减少单独 /kline 请求）。

    Args:
        result: 缠论引擎产出的分析结果（run_analysis 的返回值）。

    Returns:
        dict，字段与前端一一对应：

        - stock_code / level / trend / summary：头部概览卡片。
        - data_period：实际数据口径（1min 会被降级标注为 5min，见下）。
        - klines / total：K 线主图数据。
        - bis / xians / zhongshus：笔 / 线段 / 中枢，叠加层绘制用。
        - signals：买卖点标记。
        - supportResistance：支撑压力位（注意驼峰，前端历史字段）。

    Note:
        所有时间统一用 str(x)[:19] 截断到「秒」：既去掉 pandas Timestamp 的
        时区/毫秒尾巴，也让 JSON 里的时间是定长字符串，前端可直接比较与展示。
    """
    klines = [
        {
            "date": str(k.date)[:19],
            "open": k.open,
            "high": k.high,
            "low": k.low,
            "close": k.close,
            "volume": k.volume,
        }
        for k in result.klines
    ]
    period = level_to_period(result.level)
    # A 股数据源的坑：1min 线长期取不全（拿到的往往是 5min 采样或空表），
    # 所以把实际口径降级标注成 5min，前端据此提示用户，避免误以为看到的是真 1min
    data_period_note = "5min" if result.level == "1min" else period
    return {
        "stock_code": result.stock_code,
        "level": result.level,
        "data_period": data_period_note,
        "trend": result.trend,
        "summary": result.summary,
        "klines": klines,
        "total": len(klines),
        "bis": [
            {
                "id": b.id,
                "start": str(b.start)[:19],
                "end": str(b.end)[:19],
                "direction": b.direction,
                "high": b.high,
                "low": b.low,
                "start_price": b.start_price,
                "end_price": b.end_price,
            }
            for b in result.bis
        ],
        "xians": [
            {
                "id": s.id,
                "start": str(s.start)[:19],
                "end": str(s.end)[:19],
                "direction": s.direction,
                "high": s.high,
                "low": s.low,
                "start_price": s.start_price,
                "end_price": s.end_price,
            }
            for s in result.xians
        ],
        "zhongshus": [
            {
                "id": z.id,
                "start": str(z.start)[:19],
                "end": str(z.end)[:19],
                "range_high": z.range_high,
                "range_low": z.range_low,
            }
            for z in result.zhongshus
        ],
        "signals": [
            {
                "type": s.type,
                "level": s.level,
                "price": s.price,
                "datetime": str(s.datetime)[:19],
                "confidence": s.confidence,
                "stop_loss": s.stop_loss,
                "take_profit": s.take_profit,
                "description": s.description,
            }
            for s in result.signals
        ],
        "supportResistance": [
            {
                "type": lvl.type,
                "price": lvl.price,
                "source": lvl.source,
                "relatedId": lvl.related_id,
                "datetime": str(lvl.datetime)[:19],
                "strength": lvl.strength,
            }
            for lvl in result.support_resistance
        ],
    }
