from __future__ import annotations

import asyncio
import logging

import pandas as pd
from fastapi import APIRouter
from core.numbers import finite_float
from services.akshare_service import get_realtime_quote, normalize_stock_code
from stores.local_json import get_watchlist_map, watchlist_add, watchlist_remove
from utils import watchlist_quote_cache

router = APIRouter()
log = logging.getLogger(__name__)


def _fetch_quote_rows(codes: list[str]) -> tuple[dict[str, dict], bool]:
    """返回 (代码 -> 行情行, 是否所有代码都取到了行情)。

    富途的批量快照只要混入一个它不认识的代码就会整批报错，所以整批失败时
    退回逐只拉取——否则一只脏数据会把用户攒的整个自选列表打空。
    """
    try:
        df = get_realtime_quote(codes)
    except Exception as e:  # get_realtime_quote 内部已兜底，这里再兜一层
        log.warning("[自选] 批量行情失败: %s", e)
        df = pd.DataFrame()

    rows: dict[str, dict] = {}
    if not df.empty:
        for _, row in df.iterrows():
            rows[str(row.get("代码", ""))] = row
        return rows, len(rows) >= len(codes)

    for c in codes:
        try:
            one = get_realtime_quote([c])
        except Exception as e:
            log.warning("[自选] %s 行情失败: %s", c, e)
            continue
        if one.empty:
            continue
        row = one.iloc[0]
        rows[str(row.get("代码", ""))] = row
    return rows, len(rows) >= len(codes)


def _get_watchlist_response():
    wl = get_watchlist_map()
    if not wl:
        return {"stocks": [], "total": 0, "quote_ok": True}

    codes = sorted(wl.keys(), key=lambda c: (wl.get(c, ""), c))
    cache_key = ",".join(codes)
    cached = watchlist_quote_cache.get(cache_key)
    if cached is not None:
        return cached

    rows, all_ok = _fetch_quote_rows(codes)

    stocks = []
    for code in codes:
        row = rows.get(code)
        if row is None:
            # 行情没取到也要保留这一条：返回空数组会让前端把整个自选列表清空
            stocks.append({
                "code": code,
                "name": "",
                "price": 0.0,
                "change_pct": 0.0,
                "added_at": wl.get(code, ""),
                "quote_ok": False,
            })
            continue
        stocks.append({
            "code": code,
            "name": str(row.get("名称", "") or ""),
            "price": finite_float(row.get("最新价")),
            "change_pct": finite_float(row.get("涨跌幅")),
            "added_at": wl.get(code, ""),
            "quote_ok": True,
        })

    payload = {"stocks": stocks, "total": len(wl), "quote_ok": all_ok}
    # 只有全部取到才缓存，避免把「行情缺失」这个中间态缓存住
    if all_ok:
        watchlist_quote_cache.set(cache_key, payload)
    return payload


@router.get("/api/watchlist", tags=["自选"])
async def get_watchlist():
    return await asyncio.to_thread(_get_watchlist_response)


def _invalidate_watchlist_cache() -> None:
    watchlist_quote_cache.clear()


@router.post("/api/watchlist/{code}", tags=["自选"])
async def add_watchlist(code: str):
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
    sym, _ = normalize_stock_code(code)
    watchlist_remove(sym)
    _invalidate_watchlist_cache()
    wl = get_watchlist_map()
    return {"code": sym, "removed": True, "total": len(wl)}
