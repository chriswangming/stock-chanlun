"""缠论分析 API 响应序列化。"""
from chanlun.elements import ChanlunAnalysis
from core.chanlun_analysis import level_to_period


def serialize_chanlun_analysis(result: ChanlunAnalysis, save_to_local: bool = False) -> dict:
    """将 ChanlunAnalysis 转为前端 JSON（含 K 线，减少单独 /kline 请求）。"""
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
    data_period_note = "5min" if result.level == "1min" else period
    res = {
        "stock_code": result.stock_code,
        "level": result.level,
        "data_period": data_period_note,
        "trend": result.trend,
        "summary": result.summary,
        "klines": klines,
        "total": len(klines),
        # 包含处理后的合并K线：start/end 是它覆盖的原始K线区间，count>1 表示发生过合并
        "merged_klines": [
            {
                "start": str(m.start)[:19],
                "end": str(m.end)[:19],
                "start_idx": m.start_idx,
                "end_idx": m.end_idx,
                "high": m.high,
                "low": m.low,
                "dir": m.dir,
                "count": m.count,
                "fx": m.fx,
                # 被包含的原始K线：前端画蓝色高低线（chan.py 出图风格）
                "included": [
                    {"date": str(r.date)[:19], "high": r.high, "low": r.low}
                    for r in m.included
                ],
            }
            for m in result.merged_klines
        ],
        "fenxings": [
            {
                "date": str(f.date)[:19],
                "high": f.high,
                "low": f.low,
                "type": f.type,
                "index": f.index
            }
            for f in result.fenxings
        ],
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
                # # chan.py 口径：False 表示这是尾部「虚笔」（正在走、尚未确认）
                # "is_sure": b.is_sure,
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
                # # 尾部线段 / 首段切分出来的段多为未确认
                # "is_sure": s.is_sure,
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
                # "center": z.center,
                # "is_sure": z.is_sure,
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

    res['count'] = {"kline": len(res['klines']),
                    "kline_combine": len(res['merged_klines']),
                    "fenxings":len(res['fenxings']),
                    "bi": len(res['bis'])
                    }
    


    if save_to_local is True:
        import json
        from pathlib import Path
        dir_path = Path(__file__).resolve().parent
        dir_path.mkdir(parents=True, exist_ok=True)
        with open(dir_path / f"{res['stock_code']}.json", "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)

    return res
