"""
系统级路由：/health 健康检查、/api/settings 设置的读写。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from ai.llm_client import set_llm_model
from config import SCREENING_WORKERS
from stores.local_json import load_settings, save_settings
from ai.chat_sessions import chat_session_stats
from services.akshare_service import akshare_cache_stats
from services.futu_hk_service import futu_cache_stats
from utils import (
    ai_signal_llm_cache,
    ai_signal_rule_cache,
    chanlun_cache,
    chanlun_global_limiter,
    chanlun_ip_limiter,
    kline_global_limiter,
    market_overview_cache,
    sector_board_cache,
    stock_news_cache,
    watchlist_quote_cache,
)

router = APIRouter()

log = logging.getLogger(__name__)


@router.get("/health", tags=["系统"])
def health_check():
    """健康检查：顺带清理限流器的过期 key，并汇总各缓存/限流器/会话的当前状态。
    """
    chanlun_global_limiter.prune_stale_keys()
    return {
        "status": "ok",
        "version": "0.2.0",
        "cache": {
            "chanlun": chanlun_cache.stats(),
            "ai_signal_rule": ai_signal_rule_cache.stats(),
            "ai_signal_llm": ai_signal_llm_cache.stats(),
            "watchlist_quote": watchlist_quote_cache.stats(),
            "market_overview": market_overview_cache.stats(),
            "sector_board": sector_board_cache.stats(),
            "stock_news": stock_news_cache.stats(),
            "akshare": akshare_cache_stats(),
            "futu": futu_cache_stats(),
        },
        "rate_limiters": {
            "chanlun_global_remaining": chanlun_global_limiter.get_remaining("global"),
            "chanlun_ip_buckets": chanlun_ip_limiter.tracked_key_count(),
            "kline_global_remaining": kline_global_limiter.get_remaining("global"),
        },
        "chat_sessions": chat_session_stats(),
        "screening_workers": SCREENING_WORKERS,
    }


@router.get("/api/settings", tags=["系统"])
def get_settings():
    """GET 系统设置（目前只有 ai_model）。
    """
    return load_settings()


@router.put("/api/settings", tags=["系统"])
def update_settings(model: str = Query(..., description="AI 模型 deepseek / gemini")):
    """PUT 切换 AI 模型：即时生效（重建 LLM 客户端）并落盘持久化；只认 deepseek/gemini。
    """
    if model not in ("deepseek", "gemini"):
        raise HTTPException(status_code=400, detail="只支持 deepseek 和 gemini")
    s = load_settings()
    old = s.get("ai_model")
    set_llm_model(model)
    s["ai_model"] = model
    save_settings(s)
    log.info("AI 模型切换：%s -> %s", old, model)
    return {"ai_model": model, "ok": True}
