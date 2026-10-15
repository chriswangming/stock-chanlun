"""K 线 DataFrame / 缠论 KLine 模型 与 API JSON 之间的互转。

两个方向各司其职：

    df_to_kline_dicts()        DataFrame → list[dict]  行情接口出参
    analysis_klines_to_df()    list[KLine] → DataFrame AI / 指标计算入参

放在 core 层是为了让 routers（行情）与 core（AI 链路）共用同一套序列化口径，
避免两处各写一遍导致日期格式或数值类型不一致。
"""
from __future__ import annotations

import pandas as pd

from chanlun.elements import ChanlunAnalysis, KLine


def df_to_kline_dicts(df: pd.DataFrame) -> list[dict]:
    """将 K 线 DataFrame 序列化为 API 响应格式（行情接口出参方向）。

    Args:
        df: 至少含 date/open/high/low/close 列的 K 线表，volume 可缺省。

    Returns:
        list[dict]，每项形如
        {"date": "YYYY-MM-DD HH:MM:SS", "open": ..., "volume": ...}。
        空表返回 []，交由路由层决定是 404 还是空列表。
    """
    if df.empty:
        return []
    out: list[dict] = []
    # itertuples 比 iterrows 快得多；volume 在部分数据源缺失，故用 getattr 兜底为 0
    for row in df.itertuples(index=False):
        out.append(
            {
                "date": str(row.date)[:19],
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(getattr(row, "volume", 0) or 0),
            }
        )
    return out


def analysis_klines_to_df(klines: list[KLine]) -> pd.DataFrame:
    """从缠论分析结果中的 K 线对象重建 DataFrame（避免重复拉取行情）。

    Args:
        klines: ChanlunAnalysis.klines，即引擎内部已处理过的 KLine 列表。

    Returns:
        DataFrame，列为 date/open/high/low/close/volume，按日期升序且索引重置。
        传入空列表时返回空 DataFrame（不是 None），调用方用 .empty 判断即可。

    Note:
        重建后必须再按 date 排一次序：引擎内部做过包含处理与截断，
        klines 的顺序不保证严格时间升序，而 MACD / SKDJ 等指标对顺序敏感。
    """
    if not klines:
        return pd.DataFrame()
    records = [
        {
            "date": k.date,
            "open": k.open,
            "high": k.high,
            "low": k.low,
            "close": k.close,
            "volume": k.volume,
        }
        for k in klines
    ]
    df = pd.DataFrame(records)
    # 统一转成 datetime64，下游才能直接与 pd.Timestamp 比较、参与时间过滤
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)
