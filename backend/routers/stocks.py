# """行情与选股 HTTP 路由。

# 本层只做：限流 → 参数校验 → asyncio.to_thread 包装 → 出参清洗。
# 取数全在 services/akshare_service.py 与 services/screening_service.py，
# 数值兜底用 utils/numbers.finite_float，级别映射用 core/chanlun_analysis.level_to_period。

# 限流分档：

#     - check_light_api_rate_limits：搜索 / 新闻 / 盘口等轻量接口；
#     - check_kline_rate_limits：K 线 / 行情快照 / 导出 / 大盘概览；
#     - check_screening_rate_limits：选股（最重，逐票跑缠论）。

# 选股的两个入口差别很大：`/screen` 是一次性返回，`/screen-stream` 是 SSE 流式，
# 边算边推，并支持前端断连时提前终止。
# """
# from __future__ import annotations

# import asyncio
# import csv
# import io
# import json
# import logging
# import re
# from typing import Optional

# log = logging.getLogger(__name__)

# import pandas as pd
# from fastapi import APIRouter, HTTPException, Query, Request
# from fastapi.responses import StreamingResponse

# from core.chanlun_analysis import level_to_period
# from core.kline_serialize import df_to_kline_dicts
# from utils.numbers import finite_float
# from deps import check_kline_rate_limits, check_light_api_rate_limits, check_screening_rate_limits, client_ip
# from utils import market_overview_cache, sector_board_cache, stock_news_cache
# from services.akshare_service import (
#     get_board_constituents_em,
#     get_daily_hot_stocks,
#     get_kline_hist,
#     get_market_overview_bundle,
#     get_realtime_quote,
#     get_stock_boards_em,
#     get_stock_depth_em,
#     get_stock_info,
#     get_stock_news,
#     get_stock_symbol_news_em,
#     normalize_stock_code,
#     search_stocks,
# )

# router = APIRouter()


# def _search_stock_impl(q: str):
#     """按关键字搜索股票（同步，供 to_thread 调用）。

#     Args:
#         q: 关键字（代码或名称片段）。

#     Returns:
#         {"stocks": 前 20 条 [{code, name}], "total": 命中总数}；无命中返回空列表。
#     """
#     df = search_stocks(q)
#     if df.empty:
#         return {"stocks": [], "total": 0}
#     return {
#         "stocks": df.head(20)[["code", "name"]].to_dict(orient="records"),
#         "total": len(df),
#     }


# @router.get("/api/stocks/search", tags=["数据"])
# async def search_stock(request: Request, q: str = Query(..., min_length=1)):
#     """股票搜索（轻量接口，走轻限流）。

#     Args:
#         request: 用于取客户端 IP 做限流。
#         q: 搜索关键字，至少 1 个字符。

#     Returns:
#         _search_stock_impl 的结果。
#     """
#     check_light_api_rate_limits(client_ip(request))
#     return await asyncio.to_thread(_search_stock_impl, q)


# @router.get("/api/stocks/hot", tags=["数据"])
# async def hot_stocks(request: Request, limit: int = Query(15, ge=1, le=50)):
#     """当日热门股票榜（轻量接口，走轻限流）。

#     Args:
#         request: 用于取客户端 IP 做限流。
#         limit: 返回条数，1~50，默认 15。

#     Returns:
#         {"stocks": [...], "total": n, "error": None 或提示文案}。
#     """
#     check_light_api_rate_limits(client_ip(request))
#     def _run():
#         """线程池内执行：取热门榜并把空结果转成带 error 的响应。"""
#         stocks = get_daily_hot_stocks(limit)
#         return {
#             "stocks": stocks,
#             "total": len(stocks),
#             "error": None if stocks else "获取失败，请稍后重试",
#         }

#     return await asyncio.to_thread(_run)


# @router.get("/api/stocks/screen", tags=["选股"])
# async def screen_stocks_api(
#     request: Request,
#     change_pct_min: Optional[float] = Query(None, description="最小涨跌幅（%）"),
#     change_pct_max: Optional[float] = Query(None, description="最大涨跌幅（%）"),
#     volume_min: Optional[float] = Query(None, description="最小成交量（手）"),
#     volume_max: Optional[float] = Query(None, description="最大成交量（手）"),
#     industry: Optional[str] = Query(None, description="行业板块名称（精确匹配）"),
#     pe_max: Optional[float] = Query(None, description="市盈率上限"),
#     pb_max: Optional[float] = Query(None, description="市净率上限"),
#     signals: Optional[str] = Query(None, description="逗号分隔买卖点类型，如 一买,二买"),
#     dual_cross: bool = Query(False, description="是否必须 MACD+SKDJ 双金叉共振"),
#     level: str = Query(
#         "daily",
#         pattern="^(1min|5min|15min|30min|60min|daily|weekly|monthly)$",
#     ),
#     pool_size: int = Query(100, ge=10, le=1000, description="候选池大小（最高 1000）"),
# ):
#     """选股（一次性返回）：基本面条件 + 缠论买卖点 + 双金叉过滤。

#     Args:
#         request: 用于取客户端 IP 做选股限流。
#         change_pct_min / change_pct_max: 涨跌幅区间（%）。
#         volume_min / volume_max: 成交量区间（手）。
#         industry: 行业板块名，精确匹配。
#         pe_max / pb_max: 市盈率 / 市净率上限。
#         signals: 逗号分隔的缠论买卖点类型，如 "一买,二买"。
#         dual_cross: 是否要求 MACD+SKDJ 双金叉共振。
#         level: 缠论分析级别。
#         pool_size: 候选池大小，10~1000。

#     Returns:
#         {"results": [...], "total": n}；全部算完后才一次性返回。
#         候选池较大时会明显偏慢，需要进度反馈请改用 /api/stocks/screen-stream。
#     """
#     check_screening_rate_limits(client_ip(request))
#     from services.screening_service import screen_stocks

#     # 买卖点类型是逗号分隔串，拆成列表后传给筛选服务；空串视为不加该条件
#     signal_types: list[str] | None = None
#     if signals:
#         raw = [s.strip() for s in signals.split(",") if s.strip()]
#         if raw:
#             signal_types = raw

#     def _run():
#         """线程池内执行真正的选股（同步、最重，可能跑几十秒）。"""
#         return screen_stocks(
#             change_pct_min=change_pct_min,
#             change_pct_max=change_pct_max,
#             volume_min=volume_min,
#             volume_max=volume_max,
#             industry=industry,
#             pe_max=pe_max,
#             pb_max=pb_max,
#             signal_types=signal_types,
#             require_dual_cross=dual_cross,
#             level=level,
#             pool_size=pool_size,
#         )

#     results = await asyncio.to_thread(_run)
#     return {"results": results, "total": len(results)}


# @router.get("/api/stocks/screen-stream", tags=["选股"])
# def screen_stocks_stream_api(
#     request: Request,
#     change_pct_min: Optional[float] = Query(None, description="最小涨跌幅（%）"),
#     change_pct_max: Optional[float] = Query(None, description="最大涨跌幅（%）"),
#     volume_min: Optional[float] = Query(None, description="最小成交量（手）"),
#     volume_max: Optional[float] = Query(None, description="最大成交量（手）"),
#     industry: Optional[str] = Query(None, description="行业板块名称（精确匹配）"),
#     pe_max: Optional[float] = Query(None, description="市盈率上限"),
#     pb_max: Optional[float] = Query(None, description="市净率上限"),
#     signals: Optional[str] = Query(None, description="逗号分隔买卖点类型，如 一买,二买"),
#     dual_cross: bool = Query(False, description="是否必须 MACD+SKDJ 双金叉共振"),
#     level: str = Query(
#         "daily",
#         pattern="^(1min|5min|15min|30min|60min|daily|weekly|monthly)$",
#     ),
#     pool_size: int = Query(100, ge=10, le=1000, description="候选池大小（最高 1000）"),
#     max_results: int = Query(50, ge=1, le=200, description="最多返回条数"),
# ):
#     """选股（SSE 流式）：边算边推，前端可实时看到命中结果。

#     与 screen_stocks_api 的三个差别：

#         1. 出参是 SSE 流（每条命中一个 `data:` 帧），而不是一次性 JSON；
#         2. 每轮循环先检查 request.is_disconnected()，客户端断开就停止计算；
#         3. 收尾在 finally 里调用 iterator.close()，确保生成器被正常关闭。

#     注意本函数是同步 def（不是 async）：FastAPI 会在线程池里执行它，
#     响应体交给 StreamingResponse，因此函数体只做参数拼装、立刻返回。

#     Args:
#         request: 用于取客户端 IP 做限流，同时在流里检测断连。
#         change_pct_min / change_pct_max: 涨跌幅区间（%）。
#         volume_min / volume_max: 成交量区间（手）。
#         industry: 行业板块名，精确匹配。
#         pe_max / pb_max: 市盈率 / 市净率上限。
#         signals: 逗号分隔的缠论买卖点类型。
#         dual_cross: 是否要求 MACD+SKDJ 双金叉共振。
#         level: 缠论分析级别。
#         pool_size: 候选池大小。
#         max_results: 最多返回条数，1~200（流式入口才有的截断参数）。

#     Returns:
#         StreamingResponse（text/event-stream）。
#     """
#     check_screening_rate_limits(client_ip(request))
#     from services.screening_service import screen_stocks_stream

#     signal_types: list[str] | None = None
#     if signals:
#         raw = [s.strip() for s in signals.split(",") if s.strip()]
#         if raw:
#             signal_types = raw

#     async def event_stream():
#         """SSE 生成器：反复从选股迭代器取一条，取到就发一帧。

#         Yields:
#             "data: {...}\\n\\n" 形式的帧。

#         Note:
#             取一条用的是 asyncio.to_thread(_next_item)：选股迭代内部是同步且
#             可能耗时的（单只股票跑缠论），不能直接卡在事件循环上。
#         """
#         iterator = screen_stocks_stream(
#             change_pct_min=change_pct_min,
#             change_pct_max=change_pct_max,
#             volume_min=volume_min,
#             volume_max=volume_max,
#             industry=industry,
#             pe_max=pe_max,
#             pb_max=pb_max,
#             signal_types=signal_types,
#             require_dual_cross=dual_cross,
#             level=level,
#             pool_size=pool_size,
#             max_results=max_results,
#         )

#         def _next_item():
#             """在工作线程里取下一项，用 (是否取到, 值) 表达迭代结束。

#             StopIteration 不能跨线程传播（to_thread 会把它变成 RuntimeError），
#             所以在这里就地捕获并转成布尔标记。
#             """
#             try:
#                 return True, next(iterator)
#             except StopIteration:
#                 return False, None

#         try:
#             while True:
#                 # 客户端已断开（关页面/取消）就停止计算，别白跑完整个候选池
#                 if await request.is_disconnected():
#                     break
#                 ok, item = await asyncio.to_thread(_next_item)
#                 if not ok:
#                     break
#                 yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
#         finally:
#             # 无论正常结束、断连还是异常，都要 close 生成器，释放其内部资源
#             iterator.close()

#     return StreamingResponse(
#         event_stream(),
#         media_type="text/event-stream",
#         headers={
#             "Cache-Control": "no-cache",
#             "X-Accel-Buffering": "no",
#         },
#     )


# @router.get("/api/market/overview", tags=["数据"])
# async def market_overview(request: Request):
#     """大盘概览：指数 + 涨跌家数 + 板块排行（走 K 线限流，结果按固定键缓存）。

#     Args:
#         request: 用于取客户端 IP 做限流。

#     Returns:
#         概览字典；数据源异常时返回带 stale=True 与 error 的空壳，
#         宁可让前端显示「数据暂不可用」也不要 500。
#     """
#     check_kline_rate_limits(client_ip(request))
#     def _run():
#         """线程池内执行：查缓存 → 拉概览 → 失败降级成 stale 空壳。"""
#         cached = market_overview_cache.get("overview")
#         if cached is not None:
#             return cached
#         try:
#             payload = get_market_overview_bundle()
#             market_overview_cache.set("overview", payload)
#             return payload
#         except Exception as e:
#             log.warning("大盘概览获取失败: %s", e)
#             return {
#                 "indices": {},
#                 "market_breadth": {"advancers": 0, "decliners": 0, "unchanged": 0},
#                 "sectors": [],
#                 "sectors_top": [],
#                 "sectors_bottom": [],
#                 "stale": True,
#                 "error": str(e),
#             }

#     return await asyncio.to_thread(_run)


# @router.get("/api/news", tags=["数据"])
# async def news(request: Request, limit: int = Query(10, ge=1, le=30)):
#     """财经新闻列表（轻量接口，按 limit 分键缓存）。

#     Args:
#         request: 用于取客户端 IP 做限流。
#         limit: 条数，1~30，默认 10。

#     Returns:
#         {"items": [...]}。
#     """
#     check_light_api_rate_limits(client_ip(request))

#     def _run():
#         """线程池内执行：按 limit 取缓存，未命中才拉新闻源。"""
#         key = f"limit={limit}"
#         cached = stock_news_cache.get(key)
#         if cached is not None:
#             return cached
#         payload = {"items": get_stock_news(limit)}
#         stock_news_cache.set(key, payload)
#         return payload

#     return await asyncio.to_thread(_run)


# @router.get("/api/sector/{name}/stocks", tags=["数据"])
# async def sector_stocks(request: Request, name: str):
#     """板块成分股列表（轻量接口，按板块名缓存）。

#     Args:
#         request: 用于取客户端 IP 做限流。
#         name: 板块名称，可能带首尾空格（内部会 strip 后作为缓存键）。

#     Returns:
#         成分股字典；数据源异常时返回空的 {"stocks": [], "total": 0}。
#     """
#     check_light_api_rate_limits(client_ip(request))
#     def _run():
#         """线程池内执行：strip 后作为缓存键，失败降级为空列表。"""
#         key = name.strip()
#         cached = sector_board_cache.get(key)
#         if cached is not None:
#             return cached
#         try:
#             payload = get_board_constituents_em(name)
#             sector_board_cache.set(key, payload)
#             return payload
#         except Exception as e:
#             log.warning("板块「%s」获取失败: %s", name, e)
#             return {"sector_name": name, "board_type": None, "stocks": [], "total": 0}

#     return await asyncio.to_thread(_run)


# @router.get("/api/stocks/{code}/info", tags=["数据"])
# async def stock_info(request: Request, code: str):
#     """个股基本信息（轻量接口）。

#     Args:
#         request: 用于取客户端 IP 做限流。
#         code: 股票代码，内部归一化。

#     Returns:
#         {"code": 归一化代码, "exchange": 交易所, "info": 信息字典}。
#     """
#     check_light_api_rate_limits(client_ip(request))
#     def _run():
#         """线程池内执行：归一化代码后取基本信息。"""
#         sym, exchange = normalize_stock_code(code)
#         info = get_stock_info(sym)
#         return {"code": sym, "exchange": exchange, "info": info}

#     return await asyncio.to_thread(_run)


# @router.get("/api/stocks/{code}/extras", tags=["数据"])
# async def stock_extras(
#     request: Request,
#     code: str,
#     news_limit: int = Query(8, ge=1, le=20),
# ):
#     """个股扩展信息聚合：盘口 + 所属板块 + 个股新闻。

#     Args:
#         request: 用于取客户端 IP 做限流。
#         code: 股票代码。
#         news_limit: 新闻条数，1~20，默认 8。

#     Returns:
#         {"code", "exchange", "depth", "boards", "news"}；
#         三个子查询任一失败都只降级该项，不影响其余字段。

#     Note:
#         函数体已在 to_thread 里，内部再开一个 3 线程的线程池并发拉三个源：
#         外层那个线程会阻塞等待，但事件循环不受影响。
#     """
#     check_light_api_rate_limits(client_ip(request))
#     def _run():
#         """线程池内执行：三个数据源并行拉取，各自独立降级。"""
#         from concurrent.futures import ThreadPoolExecutor

#         sym, exchange = normalize_stock_code(code)
#         # 三个子查询互不依赖，并行拉取；结果逐个 try 取值，失败只降级该项
#         with ThreadPoolExecutor(max_workers=3) as pool:
#             f_depth = pool.submit(get_stock_depth_em, sym)
#             f_boards = pool.submit(get_stock_boards_em, sym)
#             f_news = pool.submit(get_stock_symbol_news_em, sym, news_limit)
#             # 任一子查询失败不应拖垮整个 extras 聚合，分别回退默认值
#             try:
#                 depth = f_depth.result()
#             except Exception as e:
#                 log.warning("盘口获取失败: %s", e)
#                 depth = {"asks": [], "bids": []}
#             try:
#                 boards = f_boards.result()
#             except Exception as e:
#                 log.warning("板块信息获取失败: %s", e)
#                 boards = {"industry": "", "highlights": []}
#             try:
#                 news = f_news.result()
#             except Exception as e:
#                 log.warning("个股新闻获取失败: %s", e)
#                 news = []
#         return {
#             "code": sym,
#             "exchange": exchange,
#             "depth": depth,
#             "boards": boards,
#             "news": news,
#         }

#     return await asyncio.to_thread(_run)


# def _realtime_quote_impl(code: str):
#     """实时行情快照（同步，双数据源回退）。

#     先走批量快照接口 get_realtime_quote（快、字段全）；
#     拿不到再退到个股信息接口 get_stock_info（慢、只有基础字段）。

#     Args:
#         code: 股票代码，内部归一化。

#     Returns:
#         行情字典，数值全部经 finite_float 清洗（NaN/脏值兜底为 0）。

#     Raises:
#         HTTPException: 404，两个数据源都没有该股票。
#     """
#     sym, _ = normalize_stock_code(code)
#     df = get_realtime_quote([sym])
#     if not df.empty:
#         # 批量接口按列表返回，取第一行即目标股票
#         row = df.iloc[0]
#         return {
#             "code": row.get("代码", sym),
#             "name": row.get("名称", ""),
#             "price": finite_float(row.get("最新价")),
#             "change_pct": finite_float(row.get("涨跌幅")),
#             "volume": finite_float(row.get("成交量")),
#             "amount": finite_float(row.get("成交额")),
#             "high": finite_float(row.get("最高")),
#             "low": finite_float(row.get("最低")),
#             "open": finite_float(row.get("今开")),
#             "prev_close": finite_float(row.get("昨收")),
#         }

#     # 回退数据源：只要能拿到现价或名称就认为有效，其余字段缺失由 finite_float 兜 0
#     info = get_stock_info(sym)
#     if info and (info.get("现价") or info.get("名称")):
#         return {
#             "code": str(info.get("代码", sym)),
#             "name": str(info.get("名称", "")),
#             "price": finite_float(info.get("现价")),
#             "change_pct": finite_float(info.get("涨跌幅")),
#             "volume": finite_float(info.get("成交量")),
#             "amount": finite_float(info.get("成交额")),
#             "high": finite_float(info.get("最高")),
#             "low": finite_float(info.get("最低")),
#             "open": finite_float(info.get("今开")),
#             "prev_close": finite_float(info.get("昨收")),
#         }
#     raise HTTPException(status_code=404, detail="股票未找到")


# @router.get("/api/stocks/{code}/quote", tags=["数据"])
# async def realtime_quote(code: str, request: Request):
#     """个股实时行情快照（走 K 线限流）。

#     Args:
#         code: 股票代码。
#         request: 用于取客户端 IP 做限流。

#     Returns:
#         _realtime_quote_impl 的结果。
#     """
#     check_kline_rate_limits(client_ip(request))
#     return await asyncio.to_thread(_realtime_quote_impl, code)


# def _stock_kline_impl(
#     code: str,
#     level: str,
#     limit: int,
#     start_date: Optional[str],
#     end_date: Optional[str],
# ):
#     """取 K 线并序列化（同步，供 to_thread 调用）。

#     Args:
#         code: 股票代码。
#         level: K 线级别，内部映射成数据源 period。
#         limit: 最多返回根数（先按窗口取数，过滤后再截断）。
#         start_date: 起始日期 YYYY-MM-DD，闭区间。
#         end_date: 结束日期 YYYY-MM-DD，闭区间。

#     Returns:
#         {"klines": [...], "total": n}；无数据返回空列表而不是 404。
#     """
#     period = level_to_period(level)
#     df = get_kline_hist(code, period=period, adjust="qfq", limit=limit)
#     if df.empty:
#         return {"klines": [], "total": 0}

#     # 日线及以上：date 是当天 00:00，直接比较即可包含起止日
#     if start_date:
#         df = df[df["date"] >= pd.Timestamp(start_date)]
#     # +1 天是为了让 end_date 当天被包含：分钟线的 date 带时分秒（如 15:00），
#     # 若只比到 end_date 的 00:00 会把当天整根漏掉
#     if end_date:
#         df = df[df["date"] <= pd.Timestamp(end_date) + pd.Timedelta(days=1)]

#     # 过滤后可能仍超过 limit，只保留最近 limit 根
#     df = df.tail(limit).reset_index(drop=True)

#     klines = df_to_kline_dicts(df)
#     return {"klines": klines, "total": len(klines)}


# @router.get("/api/stocks/{code}/kline", tags=["数据"])
# async def stock_kline(
#     code: str,
#     request: Request,
#     level: str = Query(
#         "daily",
#         pattern="^(1min|5min|15min|30min|60min|daily|weekly|monthly)$",
#     ),
#     limit: int = Query(500, le=2000),
#     start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
#     end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
# ):
#     """个股 K 线（走 K 线限流）。

#     Args:
#         code: 股票代码。
#         request: 用于取客户端 IP 做限流。
#         level: K 线级别，默认 daily。
#         limit: 最多根数，默认 500，上限 2000。
#         start_date / end_date: 日期区间 YYYY-MM-DD，闭区间。

#     Returns:
#         {"klines": [...], "total": n}。
#     """
#     check_kline_rate_limits(client_ip(request))
#     return await asyncio.to_thread(
#         _stock_kline_impl, code, level, limit, start_date, end_date
#     )


# def _export_stock_csv_impl(code: str, level: str, limit: int):
#     """生成 K 线 CSV 导出内容（同步，供 to_thread 调用）。

#     Args:
#         code: 股票代码（会用于文件名，必须清洗）。
#         level: K 线级别。
#         limit: 导出根数。

#     Returns:
#         (csv 文本, 文件名)。

#     Raises:
#         HTTPException: 404，K 线为空无法导出。
#     """
#     period = level_to_period(level)
#     df = get_kline_hist(code, period=period, adjust="qfq", limit=limit)
#     if df.empty:
#         raise HTTPException(status_code=404, detail="K线数据为空，无法导出")

#     output = io.StringIO()
#     writer = csv.writer(output)
#     writer.writerow(["日期", "开盘", "收盘", "最高", "最低", "成交量"])
#     for _, r in df.iterrows():
#         writer.writerow(
#             [
#                 str(r["date"])[:10],
#                 f"{r['open']:.2f}",
#                 f"{r['close']:.2f}",
#                 f"{r['high']:.2f}",
#                 f"{r['low']:.2f}",
#                 f"{r.get('volume', 0):.0f}",
#             ]
#         )

#     # 文件名来自路径参数，清洗掉换行/控制字符，避免注入 Content-Disposition 头
#     # 为什么必须清洗：code 由用户控制，带 \r\n 就能在响应头里注入额外字段
#     safe_code = re.sub(r"[\r\n\"\\]", "_", code)
#     filename = f"{safe_code}_{level}_{str(df['date'].iloc[-1])[:10]}.csv"
#     output.seek(0)
#     return output.getvalue(), filename


# @router.get("/api/stocks/{code}/export", tags=["数据"])
# async def export_stock_csv(
#     code: str,
#     request: Request,
#     level: str = Query(
#         "daily",
#         pattern="^(1min|5min|15min|30min|60min|daily|weekly|monthly)$",
#     ),
#     limit: int = Query(500, le=2000),
# ):
#     """导出个股 K 线为 CSV 文件（走 K 线限流）。

#     Args:
#         code: 股票代码（参与文件名，已在 impl 中清洗）。
#         request: 用于取客户端 IP 做限流。
#         level: K 线级别，默认 daily。
#         limit: 导出根数，上限 2000。

#     Returns:
#         StreamingResponse，text/csv，带 UTF-8 文件名编码的 Content-Disposition。
#     """
#     check_kline_rate_limits(client_ip(request))
#     # CSV 在内存里生成（io.StringIO），股票 K 线量级很小，无需落临时文件
#     body, filename = await asyncio.to_thread(_export_stock_csv_impl, code, level, limit)
#     return StreamingResponse(
#         iter([body]),
#         media_type="text/csv",
#         headers={
#             "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
#         },
#     )
