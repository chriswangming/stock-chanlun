"""
自选股（/api/watchlist）路由：列表（带行情缓存）与增删。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from utils.numbers import finite_float
from services.akshare_service import get_realtime_quote, normalize_stock_code
from stores.local_json import get_watchlist_map, watchlist_add, watchlist_remove
from utils import watchlist_quote_cache

router = APIRouter()


def _get_watchlist_response():
    """取自选列表 + 实时行情。
    
    要点：
    - 缓存 key 是全部代码拼成的串，所以自选集合一变就不会命中旧缓存
    - 行情为空时也会把空结果写进缓存，避免上游异常时被反复打
    """
    wl = get_watchlist_map()
    if not wl:
        return {"stocks": []}

    codes = sorted(wl.keys())
    cache_key = ",".join(codes)
    cached = watchlist_quote_cache.get(cache_key)
    if cached is not None:
        return cached

    df = get_realtime_quote(codes)
    if df.empty:
        empty = {"stocks": [], "total": len(wl)}
        watchlist_quote_cache.set(cache_key, empty)
        return empty

    payload = {
        "stocks": [
            {
                "code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "price": finite_float(row.get("最新价")),
                "change_pct": finite_float(row.get("涨跌幅")),
                "added_at": wl.get(str(row.get("代码", "")), ""),
            }
            for _, row in df.iterrows()
        ],
        "total": len(wl),
    }
    watchlist_quote_cache.set(cache_key, payload)
    return payload


@router.get("/api/watchlist", tags=["自选"])
async def get_watchlist():
    """GET 自选列表：转线程池执行（底层是同步取行情）。
    """
    return await asyncio.to_thread(_get_watchlist_response)


def _invalidate_watchlist_cache() -> None:
    """增删自选后整体清空行情缓存（key 含全部代码，任一变动都会失效）。
    """
    watchlist_quote_cache.clear()


@router.post("/api/watchlist/{code}", tags=["自选"])
async def add_watchlist(code: str):
    """POST 添加自选：幂等，已存在时返回原 added_at 且 is_new=False。
    """
    sym, _ = normalize_stock_code(code)
    added_at, is_new = watchlist_add(sym)
    _invalidate_watchlist_cache()
    wl = get_watchlist_map()
    return {
        "code": sym,
        "added": True,
        "added_at": added_at,
        "total": len(wl),
    }


@router.delete("/api/watchlist/{code}", tags=["自选"])
async def remove_watchlist(code: str):
    """DELETE 删除自选：原本不存在也返回 removed=True。
    """
    sym, _ = normalize_stock_code(code)
    watchlist_remove(sym)
    _invalidate_watchlist_cache()
    wl = get_watchlist_map()
    return {"code": sym, "removed": True, "total": len(wl)}
