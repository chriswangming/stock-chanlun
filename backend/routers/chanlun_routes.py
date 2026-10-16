"""缠论相关 HTTP 路由：单级别分析 / 多级别并行分析 / AI 策略信号。

本层只做三件事：

    1. 限流（check_chanlun_rate_limits，多级别按级别数扣 token）；
    2. 参数校验与缓存读写（单级别结果缓存在 core 层，多级别与 AI 信号缓存在这里）；
    3. 用 asyncio.to_thread 把同步的取数/计算丢线程池，避免阻塞事件循环。

业务编排在 core/chanlun_analysis.py，AI 策略编排在本文件的
build_ai_signal_response()，算法在 chanlun/。
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, HTTPException, Query, Request

from ai.analysis_agent import SYSTEM_PROMPT, build_analysis_prompt, parse_llm_response
from ai.divergence import DivergenceDetector
from ai.llm_client import get_llm_client
from ai.strategy_engine import StrategyEngine
from ai.wave_classifier import WaveClassifier
# 注：ChanlunAnalysis 随 chanlun/elements.py 一并下线，此处不再引用
# from core.chanlun_analysis import (
#     DEFAULT_KLINE_LIMIT,
#     chanlun_cache_key,
#     get_kline_df_for_ai,
#     run_analysis,
# )
from newchan.ChanConfig import CChanConfig
from newchan.Chan import CChan
from newchan.Common.CEnum import AUTYPE, KL_TYPE, DATA_SRC
from newchan.Common.ChanException import CChanException
from newchan.Plot.ChanPicture import serialize_chanlun_analysis
from deps import check_chanlun_rate_limits, client_ip
from utils import (
    ai_signal_llm_cache,
    ai_signal_rule_cache,
    chanlun_cache,
    chanlun_multi_cache,
    to_baostock_code,
)
from utils.datetime_fmt import normalize_date_param

router = APIRouter()
log = logging.getLogger(__name__)

#: 未显式指定 start_date 时的回退窗口（自然日）。
#: 730 天 ≈ A股 500 个交易日，与前端 detail 页默认拉取的 K 线窗口对齐。
#: 不能传 None：baostock 的 query_history_k_data_plus 没有 limit 参数，
#: 起点为空会拉全历史（实测某股 2862 根、响应 1.3MB、单次 8~46 秒）。
DEFAULT_HISTORY_DAYS = 730


@router.get("/api/chanlun/{code}", tags=["缠论"], summary="缠论完整分析")
async def analyze_chanlun(
    request: Request,
    code: str,
    level: str = Query(
        "daily",
        pattern="^(1min|5min|15min|30min|60min|daily|weekly|monthly)$",
    ),
    start_date: str | None = Query(
        None,
        description="起始日期，支持 2026-05-06 / 2026/05/06；留空表示不限起点的全部历史",
    ),
):
    """单级别缠论完整分析：笔 / 线段 / 中枢 / 买卖点 / 支撑压力位。

    Args:
        request: 用于取客户端 IP 做限流。
        code: 股票代码，允许裸 6 位（``002384``）或带市场前缀（``sz.002384`` /
            ``sz002384`` / ``002384.SZ``）。
        level: 分析级别，默认 daily；白名单由 Query pattern 约束。
        start_date: 起始日期。

    Returns:
        serialize_chanlun_analysis1 产出的响应字典。

    Raises:
        HTTPException: 400 —— 代码为空，或判定为北交所（baostock 不收录北交所标的）。

    Note:
        - 入参 ``code`` 必须先过 :func:`utils.to_baostock_code` 归一化。
          ``newchan`` 的 ``CBaoStock`` **原样透传** code 给 baostock，不做归一化，
          所以裸 6 位代码会直接报「股票代码应为9位」。归一化放在这一层，
          不动 vendored 的 ``newchan``。
        - ``start_date`` 的默认值曾误写成字面量 ``"start_date"``，被原样透传给
          baostock 后触发「日期格式不正确」，而 baostock 参数非法时返回 ``None``，
          最终表现为 ``AttributeError: 'NoneType' object has no attribute
          'error_code'``，把真实原因完全盖住了。现在默认 ``None``（不限起点），
          并在 :func:`utils.normalize_date_param` 里做格式兜底。
    """
    check_chanlun_rate_limits(client_ip(request))

    # 入参归一化：裸 6 位 -> "sz.002384"；带市场标识的原样保留（尊重显式指定）
    try:
        bs_code = to_baostock_code(code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # 日期归一：认不出来时回退到 DEFAULT_HISTORY_DAYS 天前，
    # 不能传 None —— baostock 没有 limit 参数，None 会拉全历史（实测 2862 根 / 1.3MB）
    begin_time = normalize_date_param(start_date, default_days=DEFAULT_HISTORY_DAYS)

    log.debug("缠论分析入参归一化 code=%s -> %s, start_date=%s -> %s",
              code, bs_code, start_date, begin_time)

    # 初始化chan对象
    config = CChanConfig({})  # 缠论计算配置，见后文

    # 注意：CChan 的构造必须包在 try 里 —— 基类 __init__ 末尾会自动调
    # SetBasciInfo() 联网查标的基本信息，代码不存在 / 退市 / 前缀写错
    # 都是在**构造阶段**就抛 CChanException 的，包在外面会漏掉。
    try:
        chan = CChan(
            code=bs_code,
            begin_time=begin_time,
            end_time=None,
            data_src=DATA_SRC.BAO_STOCK,
            lv_list=[KL_TYPE.K_DAY],  # 多级别可以从大到小传入
            config=config,
            autype=AUTYPE.QFQ
        )
        # 异步执行
        result = await asyncio.to_thread(serialize_chanlun_analysis, chan)
    except CChanException as e:
        # 数据源侧的「代码不存在 / 退市 / 无数据 / 级别不支持」都是入参问题，
        # 直接抛 500 会让前端只看到 Internal Server Error，排查时还得翻日志，
        # 所以这里统一降级成 400 并把原因带出去。
        log.info("缠论分析失败 code=%s level=%s: %s", bs_code, level, e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        log.exception("缠论分析异常 code=%s level=%s", bs_code, level)
        raise HTTPException(status_code=500, detail="缠论分析失败，请稍后重试")

    # 回写前端入参原值的代码：前端会拿 stock_code 去拼其它接口，
    # 必须与它传进来的一致，否则带 "sz." 前缀会对不上。
    if isinstance(result, dict):
        result["stock_code"] = code
    return result

# @router.get("/api/chanlun/{code}/multi-level", tags=["缠论"], summary="多级别并行缠论分析")
# async def chanlun_multi_level(
#     request: Request,
#     code: str,
#     levels: str = Query(
#         "daily,weekly,30min",
#         description="逗号分隔的分析级别，如 daily,weekly,30min",
#     ),
#     start_date: str | None = Query(
#         None,
#         description="起始日期，支持 2026-05-06 / 2026/05/06；留空表示不限起点的全部历史",
#     ),
# ):
#     """多级别并行缠论分析：一次拿 daily / weekly / 30min 等若干个级别的结论。

#     流程：拆分并去重级别 → 按级别数扣限流 token（最多 4）→ 查多级别聚合缓存 →
#     线程池并行算各级别 → 按请求顺序组装 → 写聚合缓存。

#     Args:
#         request: 用于取客户端 IP 做限流。
#         code: 股票代码，写法同 ``/api/chanlun/{code}``（裸 6 位或带市场标识）。
#         start_date: 起始日期（透传给 ``run_analysis``）。
#         levels: 逗号分隔的级别串，默认 "daily,weekly,30min"。

#     Returns:
#         {"code", "levels": {级别: 结果或降级字符串}, "count", "elapsed_ms"}。

#     Note:
#         单个级别失败不会让整个请求失败，会被降级成字符串 "数据不足"（见 _safe_analyze）。
#         聚合缓存的失效由 core 层的 run_analysis 用 delete_prefix 触发。
#     """
#     # 去空白 + 去重（dict.fromkeys 保序），避免同一级别重复计算
#     level_list = [l.strip() for l in levels.split(",") if l.strip()]
#     level_list = list(dict.fromkeys(level_list))
#     # 按级别数扣 token，但上限 4：防止一次请求传入几十个级别打满配额外的负载
#     check_chanlun_rate_limits(client_ip(request), tokens=min(len(level_list), 4))

#     # 聚合缓存键含级别组合：不同组合是不同结果；失效时由 core 层按 code 前缀删
#     cache_key = f"multi:{code}:{','.join(level_list)}"
#     cached = chanlun_multi_cache.get(cache_key)
#     if cached is not None:
#         return cached

#     t0 = time.time()

#     def _serialize_result(result: ChanlunAnalysis) -> dict:
#         """把单个级别的结果裁剪成多级别概览（不含 K 线，避免响应过大）。

#         只保留最近 3 个买卖点与最近 5 个支撑压力位，够前端做级别对比即可。
#         """
#         return {
#             "level": result.level,
#             "trend": result.trend,
#             "summary": result.summary,
#             "bis_count": len(result.bis),
#             "zhongshus_count": len(result.zhongshus),
#             "signals_count": len(result.signals),
#             "signals": [
#                 {
#                     "type": s.type,
#                     "price": s.price,
#                     "datetime": str(s.datetime)[:19],
#                     "confidence": s.confidence,
#                     "description": s.description,
#                 }
#                 for s in result.signals[-3:]
#             ],
#             "supportResistance": [
#                 {
#                     "type": lvl.type,
#                     "price": lvl.price,
#                     "datetime": str(lvl.datetime)[:19],
#                     "strength": lvl.strength,
#                 }
#                 for lvl in result.support_resistance[-5:]
#             ],
#         }

#     # 由工作线程写入；CPython 下 dict 赋值是原子操作，GIL 保证不会写坏
#     results: dict[str, dict | str] = {}

#     def _safe_analyze(level: str) -> tuple[str, dict | str]:
#         """在工作线程里算一个级别，任何失败都降级成字符串而不是抛出。

#         Args:
#             level: 单个分析级别。

#         Returns:
#             (level, 结果 dict) 或 (level, "数据不足")。

#         Note:
#             这里刻意吞掉所有异常（HTTPException 连日志都不打），
#             代价是真实故障（数据源超时、代码 bug）也会静默变成 "数据不足"，
#             排查时只能靠 debug 级日志，生产环境默认看不到。
#         """
#         try:
#             # start_date 是 run_analysis 的必需位置参数，漏传会抛 TypeError，
#             # 而下面的 except Exception 会把它静默吞成 "数据不足" —— 务必带上。
#             result = run_analysis(code, level, start_date, kline_limit=DEFAULT_KLINE_LIMIT)
#             return level, _serialize_result(result)
#         except HTTPException:
#             return level, "数据不足"
#         except Exception:
#             log.warning("多级别缠论分析失败 code=%s level=%s", code, level, exc_info=True)
#             return level, "数据不足"

#     def _run_pool():
#         """在线程池里并行跑所有级别，谁先完成先收谁。

#         整个函数被 asyncio.to_thread 包一层，因此内部的阻塞等待不会卡住事件循环。
#         """
#         with ThreadPoolExecutor(max_workers=min(len(level_list), 4)) as pool:
#             futures = {pool.submit(_safe_analyze, lv): lv for lv in level_list}
#             # as_completed：不按提交顺序等待，慢级别不拖慢其它级别的收集
#             for future in as_completed(futures):
#                 # _safe_analyze 自身不会抛异常，timeout 只是兜底防死等
#                 lv, data = future.result(timeout=60)
#                 results[lv] = data

#     await asyncio.to_thread(_run_pool)

#     # 按请求里的级别顺序输出；results 里没有的（理论上是线程被取消）标为“未知错误”
#     ordered = {lv: results.get(lv, "未知错误") for lv in level_list}
#     t1 = time.time()
#     payload = {
#         "code": code,
#         "levels": ordered,
#         "count": len(level_list),
#         "elapsed_ms": round((t1 - t0) * 1000, 1),
#     }
#     chanlun_multi_cache.set(cache_key, payload)
#     return payload


# def _ai_signal_cache_key(code: str, level: str, model: str, use_llm: bool) -> str:
#     """构造 AI 信号的缓存键。

#     use_llm 必须进键：规则结果与 LLM 增强结果是两套完全不同的输出，
#     且调用方会据此选择 ai_signal_rule_cache / ai_signal_llm_cache 两个不同实例。

#     Returns:
#         "ai:code:level:model:llm|rule"。
#     """
#     return f"ai:{code}:{level}:{model}:{'llm' if use_llm else 'rule'}"


# def build_ai_signal_response(code: str, level: str, model: str, use_llm: bool) -> dict:
#     """生成 AI 策略信号：缠论结果 → 背驰 → 波浪分类 → 规则引擎 →（可选）LLM 覆盖。

#     完整链路：

#         1. get_kline_df_for_ai()：拿 K 线 DataFrame + 缠论结果（复用缓存，不重复拉行情）；
#         2. DivergenceDetector：在最近 200 根上检测背驰；
#         3. WaveClassifier：把线段/中枢映射成趋势标签；
#         4. StrategyEngine：规则引擎产出买卖方向、置信度、止损止盈；
#         5. 30min 级别额外算一次日线共振；
#         6. use_llm 为真时再让大模型给出建议，并逐字段覆盖规则结果。

#     Args:
#         code: 股票代码。
#         level: 分析级别。
#         model: LLM 模型名（deepseek / gemini），use_llm 为假时不实际调用。
#         use_llm: 是否调用大模型增强。

#     Returns:
#         响应字典；其中 llm 段描述本次是否真的用上了大模型（used / error / skipped）。

#     Note:
#         纯同步函数，路由里用 asyncio.to_thread 调用；LLM 异常不会抛出，
#         会退化成纯规则结果并把错误放进 llm.error。
#     """
#     df_for_ai, result = get_kline_df_for_ai(code, level)
#     # 现价取最后一根 K 线收盘价；空结果兜 0，交给规则引擎降级处理
#     current_price = float(result.klines[-1].close) if result.klines else 0.0

#     divergence = None
#     if not df_for_ai.empty:
#         try:
#             # 背驰只看近期结构，最多喂 200 根，控制计算量
#             div_detector = DivergenceDetector(df_for_ai.tail(200))
#             divergence = div_detector.check_divergence(result.bis)
#         except Exception:
#             # 背驰是增强项，失败不应拖垮整个信号生成
#             log.debug("背驰检测失败 code=%s", code, exc_info=True)

#     # 波浪分类：把线段与中枢折算成趋势标签，规则引擎据此挑策略
#     classifier = WaveClassifier()
#     wave_class = classifier.classify(result.xians, result.zhongshus, current_price)

#     engine = StrategyEngine(
#         signals=result.signals,
#         trend=wave_class["trend"],
#         current_price=current_price,
#         current_level=level,
#         zhongshus=result.zhongshus,
#         divergence=divergence,
#     )
#     signal = engine.generate_signal()
#     signal.stock_code = code

#     # 只有 30min 才做日线共振：更细的级别噪声太大，日线以上本身就是大级别
#     resonance = None
#     if level == "30min":
#         try:
#             # 日线结果优先走缓存；未命中才真算，并回写缓存避免下一只股票重复拉
#             daily_key = chanlun_cache_key(code, "daily", DEFAULT_KLINE_LIMIT)
#             daily_result = chanlun_cache.get(daily_key)
#             if daily_result is None:
#                 daily_result = run_analysis(code, "daily", kline_limit=DEFAULT_KLINE_LIMIT)
#                 chanlun_cache.set(daily_key, daily_result)
#             if daily_result is not None:
#                 daily_cls = WaveClassifier().classify(
#                     daily_result.xians,
#                     daily_result.zhongshus,
#                     float(daily_result.klines[-1].close) if daily_result.klines else 0.0,
#                 )
#                 resonance = classifier.multi_level_resonance(
#                     [
#                         {"trend": wave_class, "level": level},
#                         {"trend": daily_cls, "level": "daily"},
#                     ]
#                 )
#         except Exception:
#             log.debug("多级别共振计算失败 code=%s", code, exc_info=True)

#     llm_result = None
#     llm_error = None
#     if use_llm:
#         try:
#             llm = get_llm_client(model)
#             prompt = build_analysis_prompt(
#                 code=code,
#                 level=level,
#                 klines=[k.__dict__ for k in result.klines],
#                 trend=wave_class["trend"],
#                 divergence=divergence,
#                 signals=[s.__dict__ for s in result.signals],
#                 zhongshus=[z.__dict__ for z in result.zhongshus],
#                 bis=[b.__dict__ for b in result.bis],
#             )
#             # temperature 压到 0.3：策略输出要稳定，不要创造性
#             raw = llm.chat(prompt, system=SYSTEM_PROMPT, temperature=0.3)
#             llm_result = parse_llm_response(raw)
#             if not isinstance(llm_result, dict):
#                 llm_result = None
#                 llm_error = "LLM 解析结果非对象"
#         except Exception as e:
#             llm_error = str(e)
#             log.warning("LLM 策略生成失败 code=%s model=%s: %s", code, model, e)

#     # lr 为 None 表示「没用上 LLM」，下面所有字段整体回落到规则结果
#     lr = llm_result if isinstance(llm_result, dict) else None

#     # LLM 字段逐项覆盖规则结果。注意 `lr.get(x) or signal.y` 是短路语义：
#     # LLM 给出 0 / "" / False 这类合法值时会被当成“没给”而静默回落到规则值
#     # （confidence=0、entry_price=0 等场景会受影响）
#     return {
#         "stock_code": signal.stock_code,
#         "level": signal.level,
#         "direction": (lr.get("direction") if lr else None) or signal.direction,
#         "confidence": lr["confidence"] if lr and "confidence" in lr else signal.confidence,
#         "risk_level": (lr.get("risk_level") if lr else None) or signal.risk_level,
#         "entry_price": (lr.get("entry_price") if lr else None) or signal.entry_price,
#         "stop_loss": (lr.get("stop_loss") if lr else None) or signal.stop_loss,
#         "take_profit": (lr.get("take_profit") if lr else None) or signal.take_profit,
#         "holding_period": (lr.get("holding_period") if lr else None) or signal.holding_period,
#         "description": (lr.get("reasoning") if lr else None) or signal.description,
#         "trend": wave_class["trend"],
#         "divergence": divergence,
#         "resonance": resonance,
#         "llm": {
#             "model": model,
#             "used": lr is not None and llm_error is None,
#             "error": llm_error,
#             "skipped": not use_llm,
#         },
#     }


# @router.get("/api/chanlun/{code}/ai", tags=["缠论"], summary="AI 策略信号（背驰 + 规则 + 可选 LLM）")
# async def ai_signal(
#     request: Request,
#     code: str,
#     level: str = Query("daily"),
#     model: str = Query("deepseek", description="AI 模型：deepseek / gemini"),
#     use_llm: bool = Query(
#         False,
#         description="为 true 时调用大模型增强；默认仅规则引擎，响应更快",
#     ),
# ):
#     """AI 策略信号：规则引擎打底，可选让大模型润色/覆盖。

#     Args:
#         request: 用于取客户端 IP 做限流。
#         code: 股票代码。
#         level: 分析级别，默认 daily。
#         model: deepseek / gemini。
#         use_llm: 默认 False，仅走规则引擎（快、无外部依赖）。

#     Returns:
#         build_ai_signal_response 产出的响应字典。

#     Raises:
#         HTTPException: 404 由底层（K 线不足）原样透传；其它异常包成 500。

#     Note:
#         规则结果与 LLM 结果用不同的缓存实例，避免调一次 LLM 后
#         把纯规则请求也污染成 LLM 结果。
#     """
#     check_chanlun_rate_limits(client_ip(request))

#     cache_key = _ai_signal_cache_key(code, level, model, use_llm)
#     # 两套缓存分开存：规则结果便宜可长缓存，LLM 结果贵且慢
#     cache = ai_signal_llm_cache if use_llm else ai_signal_rule_cache
#     cached = cache.get(cache_key)
#     if cached is not None:
#         return cached

#     try:
#         # 整条链路（取数 + 缠论 + 背驰 + 规则 + LLM）都是同步的，丢线程池
#         payload = await asyncio.to_thread(build_ai_signal_response, code, level, model, use_llm)
#         cache.set(cache_key, payload)
#         return payload
#     except HTTPException:
#         # K 线不足等业务错误原样透传，不要包成 500
#         raise
#     except Exception as e:
#         raise HTTPException(
#             status_code=500,
#             detail=f"AI 策略生成失败: {e!s}",
#         ) from e
