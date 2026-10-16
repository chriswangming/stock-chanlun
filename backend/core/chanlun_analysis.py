"""A 股缠论分析编排层：K 线拉取 + 缠论引擎 + 结果缓存。

在分层中位于 routers/ 与 chanlun/ 之间：

    routers/（HTTP：限流 + 参数校验 + asyncio.to_thread 包装）
      └─ core/（本模块：缓存 → 拉数 → 引擎 → 缓存失效）
           ├─ chanlun/（纯算法：分型 / 笔 / 线段 / 中枢 / 买卖点）
           └─ services/（数据源：akshare）

对外只暴露两个入口：

    - run_analysis()：拿到完整 ChanlunAnalysis（优先命中 chanlun_cache）。
    - get_kline_df_for_ai()：连同 K 线 DataFrame 一起交给 AI 链路复用。

注意：本模块所有函数都是同步（阻塞）的，调用方必须包 asyncio.to_thread。
"""
from __future__ import annotations

import pandas as pd
from fastapi import HTTPException

from chanlun.elements import ChanlunAnalysis
from chanlun.engine import ChanlunEngine
from core.kline_serialize import analysis_klines_to_df
from services.akshare_service import get_kline_hist  # used by run_analysis
from utils import chanlun_cache, chanlun_multi_cache

# 全量分析默认窗口：个股详情页 / AI 链路使用
DEFAULT_KLINE_LIMIT = 500
# 选股场景的窗口：判断买卖点只需近期结构，200 根足够且能大幅降低数据源压力。
# 该值会拼进缓存键，因此与 500 的分析结果互不污染
SCREENING_KLINE_LIMIT = 200


def chanlun_cache_key(code: str, level: str, kline_limit: int = DEFAULT_KLINE_LIMIT) -> str:
    """构造单级别缠论结果的缓存键。

    Args:
        code: 股票代码。
        level: 分析级别。
        kline_limit: K 线窗口长度。

    Returns:
        "code:level:kline_limit" 形式的缓存键。

    Note:
        键里带 kline_limit 是关键：选股只用 200 根、详情页用 500 根，
        两者算出的笔/中枢并不相同，若共用键会让选股结果被全量结果污染（反之亦然）。
    """
    return f"{code}:{level}:{kline_limit}"


def level_to_period(level: str) -> str:
    """前端级别名 → akshare 的 period 参数。

    A 股的分钟线在数据源侧是「周期参数化」的：1/5/15/30/60 直接表示分钟数；
    日线及以上则传 daily/weekly/monthly。未知级别一律兜底为 daily，
    宁可返回日线也不要向数据源传非法参数。
    """
    mapping = {
        "1min": "5",
        "5min": "5",
        "15min": "15",
        "30min": "30",
        "60min": "60",
        "daily": "daily",
        "weekly": "weekly",
        "monthly": "monthly",
    }
    return mapping.get(level, "daily")


def run_analysis(code: str, level: str, start_date: str, kline_limit: int = DEFAULT_KLINE_LIMIT) -> ChanlunAnalysis:
    """执行一次缠论分析（带缓存），是 A 股缠论链路唯一的取数入口。

    流程：查缓存 → 拉 K 线 → 20 根门槛 → 截断到窗口 → 跑引擎 →
    回填 stock_code → 写单级别缓存 → 失效该股的多级别聚合缓存。

    Args:
        code: 股票代码，原样透传给行情源。
        level: 分析级别（1min/5min/15min/30min/60min/daily/weekly/monthly）。
        kline_limit: K 线窗口长度，同时参与缓存键（见 chanlun_cache_key）。

    Returns:
        ChanlunAnalysis：含笔 / 线段 / 中枢 / 买卖点 / 支撑压力位。

    Raises:
        HTTPException: 404，有效 K 线不足 20 根（无法构成笔）。
    """
    # 键里必须带 kline_limit：选股(200) 与全量(500) 的结构不同，共用一条缓存会串味
    cache_key = chanlun_cache_key(code, level, kline_limit)
    cached = chanlun_cache.get(cache_key)
    if cached is not None:
        return cached

    # 同步阻塞调用（网络 + CPU），由上层用 asyncio.to_thread 丢线程池
    period = level_to_period(level)
    df = get_kline_hist(code, period=period, start_date=start_date, adjust="qfq", limit=kline_limit)

    # 20 根是「能画出一笔」的经验下限，不足时直接 404，不返回空壳结果误导前端
    if df.empty or len(df) < 20:
        raise HTTPException(
            status_code=404,
            detail=f"{code} {level}级别K线数据不足（仅{len(df) if not df.empty else 0}根），请换日线/30分钟级别尝试",
        )

    # 数据源可能返回超过窗口的长度，只保留最近 kline_limit 根，保证同一键的结果可复现
    if len(df) > kline_limit:
        df = df.tail(kline_limit).reset_index(drop=True)

    engine = ChanlunEngine(df)
    result = engine.analyze(level=level)
    # 引擎只认 DataFrame、不知道标的，回填代码后前端与缓存才可区分不同股票
    result.stock_code = code

    chanlun_cache.set(cache_key, result)
    # 本级结果变了 => 依赖它的多级别聚合结果连带失效，按 code 前缀批量删除
    chanlun_multi_cache.delete_prefix(f"multi:{code}:")
    return result


def get_kline_df_for_ai(
    code: str, level: str, kline_limit: int = DEFAULT_KLINE_LIMIT
) -> tuple[pd.DataFrame, ChanlunAnalysis]:
    """一次性拿到「K 线 DataFrame + 缠论分析结果」，供 AI 链路（背驰检测等）复用。

    Args:
        code: 股票代码。
        level: 分析级别。
        kline_limit: K 线窗口长度，需与调用方 run_analysis 的口径一致才能命中缓存。

    Returns:
        (DataFrame, ChanlunAnalysis) 二元组。

    Note:
        刻意从 result.klines 反推 DataFrame，而不是再调一次 get_kline_hist：
        既省一次网络往返，也保证与缠论引擎看到的 K 线完全一致（同样的截断与对齐）。
    """
    result = run_analysis(code, level, kline_limit=kline_limit)
    return analysis_klines_to_df(result.klines), result
