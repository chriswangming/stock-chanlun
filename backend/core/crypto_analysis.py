"""加密货币缠论分析：K 线拉取 + 引擎（数据源换成 Binance）。

与 A 股版 core/chanlun_analysis.py 保持结构对齐（同样的 20 根门槛、
同样的窗口截断、同样的引擎调用），差异只有三处，改动时请对照维护：

    1. 缓存：用独立的 crypto_chanlun_cache，避免与 A 股 chanlun_cache 键冲突
       （币种代码如 "BTCUSDT" 与 A 股代码格式不同，但键格式完全一致）。
    2. 无多级别缓存失效：加密货币目前没有 multi-level 聚合接口，
       因此不需要 A 股那句 chanlun_multi_cache.delete_prefix()。
    3. 无周期降级备注：Binance 的 level 与拉取周期一一对应、1min 数据真实可用，
       所以响应里没有 A 股 1min → 5min 的 data_period 降级标注。
"""
from __future__ import annotations

from fastapi import HTTPException

from chanlun.elements import ChanlunAnalysis
from chanlun.engine import ChanlunEngine
from services.binance_service import get_kline_hist
from utils import LRUCache

DEFAULT_KLINE_LIMIT = 500

# 独立缓存实例，避免与 A 股缠论缓存（utils.chanlun_cache）键冲突
# 注意这是模块级单例：进程重启即失效，且多 worker 部署时各进程各存一份
crypto_chanlun_cache = LRUCache(maxsize=64, ttl=60.0)


def crypto_cache_key(symbol: str, level: str, kline_limit: int = DEFAULT_KLINE_LIMIT) -> str:
    """构造加密货币缠论结果的缓存键（"symbol:level:kline_limit"）。

    与 A 股 chanlun_cache_key 同构，但落在不同缓存实例里，所以不会互相覆盖。
    """
    return f"{symbol}:{level}:{kline_limit}"


def run_crypto_analysis(symbol: str, level: str, kline_limit: int = DEFAULT_KLINE_LIMIT) -> ChanlunAnalysis:
    """执行一次加密货币缠论分析（带缓存），与 A 股 run_analysis 流程对齐。

    Args:
        symbol: 交易对，如 BTCUSDT（调用方通常先过 normalize_symbol）。
        level: K 线级别，直接就是 Binance 的 interval，无需像 A 股那样做周期映射。
        kline_limit: K 线窗口长度，参与缓存键。

    Returns:
        ChanlunAnalysis 结果。

    Raises:
        HTTPException: 404，有效 K 线不足 20 根（多为交易对代码写错）。
    """
    cache_key = crypto_cache_key(symbol, level, kline_limit)
    cached = crypto_chanlun_cache.get(cache_key)
    if cached is not None:
        return cached

    # Binance 的 level 即 interval，无需 A 股那样的 level_to_period 映射
    df = get_kline_hist(symbol, period=level, limit=kline_limit)

    # 与 A 股一致的 20 根门槛：不足则无法成笔
    if df.empty or len(df) < 20:
        raise HTTPException(
            status_code=404,
            detail=f"{symbol} {level}级别K线数据不足（仅{len(df) if not df.empty else 0}根），请确认交易对代码或换用日线级别尝试",
        )

    if len(df) > kline_limit:
        df = df.tail(kline_limit).reset_index(drop=True)

    engine = ChanlunEngine(df)
    result = engine.analyze(level=level)
    # 引擎只认 DataFrame，回填交易对代码供响应与前端展示使用
    result.stock_code = symbol

    # 无多级别聚合缓存，故不需要 A 股那句 delete_prefix 失效逻辑
    crypto_chanlun_cache.set(cache_key, result)
    return result


def serialize_crypto_analysis(result: ChanlunAnalysis) -> dict:
    """将 ChanlunAnalysis 转为前端 JSON。

    与 A 股 serialize_chanlun_analysis 的差异：

        - 顶层用 symbol 而不是 stock_code；
        - 没有 data_period 字段（加密货币级别与周期一一对应，无需降级标注）；
        - 没有 xians（线段）字段，前端加密货币图表目前只画笔与中枢。

    Args:
        result: 缠论引擎产出的分析结果。

    Returns:
        可直接作为响应体的 dict。
    """
    return {
        "symbol": result.stock_code,
        "level": result.level,
        "trend": result.trend,
        "summary": result.summary,
        "klines": [
            {
                "date": str(k.date)[:19],
                "open": k.open,
                "high": k.high,
                "low": k.low,
                "close": k.close,
                "volume": k.volume,
            }
            for k in result.klines
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
            }
            for b in result.bis
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
