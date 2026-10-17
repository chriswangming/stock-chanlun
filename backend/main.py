"""
ChanStock — 缠论智能股票分析系统
FastAPI 后端入口（路由已拆至 routers/）。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

# 1) 环境变量
import config  # noqa: F401

# 2) 日志装配：uvicorn 是独立进程，必须自己装配一次，否则各模块的 logger
#    没有 handler，INFO 全被丢掉、WARNING+ 只会走 stderr 的兜底输出
from utils.log import setup_logging

setup_logging(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    log_file=config.LOG_FILE or None,
)

# 3) 必须在任何 akshare/requests 金融请求之前加载
import http_adapter_patch  # noqa: F401

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import cors_allow_credentials, cors_allow_origins
from routers import chanlun_routes, comments, diagnosis, stocks, system, watchlist
from stores.local_json import apply_startup_ai_model
from utils import (
    ai_signal_llm_cache,
    ai_signal_rule_cache,
    chanlun_cache,
    chanlun_multi_cache,
    market_overview_cache,
    sector_board_cache,
    stock_news_cache,
    watchlist_quote_cache,
)


log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时恢复 AI 模型并清理各缓存的过期项；yield 之后（关停）不做额外处理。
    """
    log.info("服务启动：%s v%s", _app.title, _app.version)
    apply_startup_ai_model()
    chanlun_cache.purge_expired()
    chanlun_multi_cache.purge_expired()
    ai_signal_rule_cache.purge_expired()
    ai_signal_llm_cache.purge_expired()
    watchlist_quote_cache.purge_expired()
    market_overview_cache.purge_expired()
    sector_board_cache.purge_expired()
    stock_news_cache.purge_expired()
    yield
    log.info("服务关闭")


app = FastAPI(
    title="ChanStock 缠论分析 API",
    description="缠论智能股票分析系统 — 提供 K 线数据、市场行情、缠论中枢/笔/段分析、AI 策略信号、选股等多维度功能",
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins(),
    allow_credentials=cors_allow_credentials(),
    allow_methods=["*"],
    allow_headers=["*"],
)

# app.include_router(stocks.router)
app.include_router(chanlun_routes.router)
app.include_router(watchlist.router)
app.include_router(comments.router)
app.include_router(system.router)
# app.include_router(diagnosis.router)


if __name__ == "__main__":
    import uvicorn

    from config import PORT

    uvicorn.run(app, host="0.0.0.0", port=PORT)
