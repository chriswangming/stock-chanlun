"""
选股服务 — 按基础指标、缠论买卖点、MACD+SKDJ 双金叉筛选股票。

缠论部分基于 newchan，但没有直接 ``new CChan(code, ..., DATA_SRC.BAO_STOCK)``，
而是拆成「取数」与「计算」两步：K 线仍走 services.akshare_service.get_kline_hist
（它内部用 _bs_lock 串行化 baostock 单一会话，可在线程池里安全并发调用），
拿到 DataFrame 后再用 newchan 的 CKLine_List 增量推进算出笔 / 线段 / 中枢 / 买卖点
（纯 CPU，不受取数锁影响，因此ThreadPoolExecutor 的并行度仍然有效）。

不直接构造 CChan 的原因有两个：
    1. CBaoStock.do_init / do_close 用的是**类属性** is_connect + 全局 bs.login/logout，
       在线程池里并发会互相踢下线；
    2. CBaoStock.do_close 里的 bs.logout() 会把 akshare_service 已经建好的会话一起注销，
       （它用模块级 _bs_logged_in 记账），反过来让后续 get_kline_hist 取到空数据。
"""
import logging
import time
import pandas as pd
from concurrent.futures import ThreadPoolExecutor

from newchan.BuySellPoint.BS_Point import CBS_Point
from newchan.ChanConfig import CChanConfig
from newchan.Common.CEnum import DATA_FIELD, KL_TYPE
from newchan.Common.CTime import CTime
from newchan.KLine.KLine_List import CKLine_List
from newchan.KLine.KLine_Unit import CKLine_Unit
from services.akshare_service import (
    get_daily_hot_stocks,
    get_kline_hist,
    get_realtime_quote,
    get_stock_boards_em,
    get_stock_info,
)
from ai.wave_classifier import WaveClassifier
from config import SCREENING_WORKERS
from utils.indicators import calc_macd_lists, calc_skdj_lists, find_latest_dual_cross_bar

log = logging.getLogger(__name__)

# 选股场景的窗口：判断买卖点只需近期结构，200 根足够且能大幅降低数据源压力。
# 原名 core.chanlun_analysis.SCREENING_KLINE_LIMIT，随 core 包弃用搬到这里。
SCREENING_KLINE_LIMIT = 200
# 「能画出一笔」的经验下限，不足时跳过而不是返回空壳结果
MIN_KLINE_NUM = 20

# 前端级别名 → akshare period 参数。
# 注意 "1min" 折到 "5"：akshare_service 的 _MINUTE_PERIODS 只有 5/15/30/60，
# 不支持 1 分钟线，这里沿用旧 core.level_to_period 的行为而非报错。
_LEVEL_TO_PERIOD = {
    "1min": "5",
    "5min": "5",
    "15min": "15",
    "30min": "30",
    "60min": "60",
    "daily": "daily",
    "weekly": "weekly",
    "monthly": "monthly",
}

# 前端级别名 → newchan 的 KL_TYPE，用于构造 CKLine_List 时标明本序列的级别语义。
# 注意 "1min" 同样落到 K_5M：它实际取到的就是 5 分钟K线（见 _LEVEL_TO_PERIOD 的注），
# 这里按「数据的真实周期」标注而不是按请求的级别名，否则分钟线的时间序列会被标错。
_LEVEL_TO_KLTYPE = {
    "1min": KL_TYPE.K_5M,
    "5min": KL_TYPE.K_5M,
    "15min": KL_TYPE.K_15M,
    "30min": KL_TYPE.K_30M,
    "60min": KL_TYPE.K_60M,
    "daily": KL_TYPE.K_DAY,
    "weekly": KL_TYPE.K_WEEK,
    "monthly": KL_TYPE.K_MON,
}

# CBS_Point 不带置信度（features 里只有结构量），给所有买卖点一个中性基准。
# 与 ai.strategy_engine.DEFAULT_BSP_CONFIDENCE 同口径。
DEFAULT_BSP_CONFIDENCE = 0.6
_CN_NUM = {"1": "一", "2": "二", "3": "三"}


def _level_to_period(level: str) -> str:
    """前端级别名 → akshare period；未知级别兜底 daily，宁可算日线也不传非法参数。"""
    return _LEVEL_TO_PERIOD.get(level, "daily")


def _level_to_kltype(level: str) -> KL_TYPE:
    """前端级别名 → newchan KL_TYPE；未知级别兜底日线，规则同 _level_to_period。"""
    return _LEVEL_TO_KLTYPE.get(level, KL_TYPE.K_DAY)


def _to_ctime(ts) -> CTime:
    """
    pandas 时间戳 → newchan 的 CTime。

    Note:
        关键在 hour/minute 的取值：CTime 的 auto 语义是「时分都为 0 时视为天级别，
        落到当天 23:59」，正好让日线时间戳大于当天任意分钟K线。分钟线的 date
        带真实时分，原样传进去即可，不要统一填 0 —— 否则会被误判成天级别。
    """
    return CTime(ts.year, ts.month, ts.day, ts.hour, ts.minute)


def _build_kl_list(df: pd.DataFrame, kl_type: KL_TYPE) -> CKLine_List:
    """
    把 K 线 DataFrame 喂给 newchan，算出该级别的笔 / 线段 / 中枢 / 买卖点。

    Args:
        df: 至少含 date/open/close/high/low 列、按时间升序的 K 线表。
        kl_type: 本序列级别，决定 CTime 与笔解释器如何理解时间。

    Returns:
        CKLine_List: 已结算完成的容器（bi_list / seg_list / zs_list / bs_point_lst 均可用）。

    Note:
        刻意绕过 CChan 的联网取数，改用 add_single_klu 手工推进：网络 I/O 在此之前
        已由 _analyze_stock 做完（且被 akshare_service._bs_lock 保护），剩下的纯
        CPU 计算才能在线程池里真正并行。
        autofix=True 是因为数据源偶有畸形K线（open < low 之类），不修正会抛
        ErrCode.KL_DATA_INVALID。
    """
    for col in ("date", "open", "close", "high", "low"):
        if col not in df.columns:
            raise ValueError(f"K线表缺少 {col} 列，实际列={list(df.columns)}")

    kl_list = CKLine_List(kl_type=kl_type, conf=CChanConfig({}))
    dates = df["date"].tolist()
    opens = df["open"].astype(float).tolist()
    closes = df["close"].astype(float).tolist()
    highs = df["high"].astype(float).tolist()
    lows = df["low"].astype(float).tolist()

    for i in range(len(dates)):
        klu = CKLine_Unit(
            {
                DATA_FIELD.FIELD_TIME: _to_ctime(dates[i]),
                DATA_FIELD.FIELD_OPEN: opens[i],
                DATA_FIELD.FIELD_CLOSE: closes[i],
                DATA_FIELD.FIELD_HIGH: highs[i],
                DATA_FIELD.FIELD_LOW: lows[i],
            },
            autofix=True,
        )
        # idx / kl_type 原本由 CChan.load 统一回填，手工推进必须自己补上，
        # 否则买卖点比较先后时会拿到哨兵值 -1
        klu.set_idx(i)
        klu.kl_type = kl_type
        kl_list.add_single_klu(klu)

    # 全部K线喂完后统一结算：线段 / 中枢 / 买卖点是批量算的（只有笔是增量更新）
    kl_list.cal_seg_and_zs()
    return kl_list


def _latest_bsp(kl_list: CKLine_List) -> CBS_Point | None:
    """
    取该级别最新一个买卖点。

    Note:
        bsp_iter_v2 是按笔 idx「从新到旧」归并遍历的生成器，取第一个即可，
        没必要把全部买卖点排一遍序（选股场景只要最近的那个）。
    """
    try:
        return next(iter(kl_list.bs_point_lst.bsp_iter_v2()), None)
    except Exception:
        return None


def _bsp_labels(bsp: CBS_Point) -> list[str]:
    """
    CBS_Point 的类型列表 → 中文标签，如 ``["一买"]`` / ``["二买", "三买"]``。

    Note:
        CBS_Point.type 是列表而非单值 —— 同一根笔可同时满足多类定义，
        所以返回列表，由调用方决定取哪个。
    """
    sfx = "买" if bsp.is_buy else "卖"
    labels: list[str] = []
    for t in bsp.type:
        main = str(t.main_type())
        label = f"{_CN_NUM.get(main, main)}{sfx}"
        if label not in labels:
            labels.append(label)
    return labels


def _bsp_date_str(bsp: CBS_Point) -> str:
    """买卖点日期 → 'YYYY-MM-DD'。

    Note:
        CBS_Point 没有 datetime 属性，时间挂在它所在的那根 KLU 上。
        CTime.__str__ 输出的是 'YYYY/MM/DD'（斜杠），这里统一转成 ISO 短横线格式，
        与旧 BuySellPoint.datetime 的输出口径保持一致。
    """
    return bsp.klu.time.toDateStr(splt="-")


def _bsp_confidence(bsp: CBS_Point) -> float:
    """买卖点置信度：features 里有 'confidence' 就用，否则回落默认基准。"""
    try:
        v = bsp.features["confidence"]
    except (KeyError, TypeError, AttributeError):
        v = None
    if v is None:
        return DEFAULT_BSP_CONFIDENCE
    return max(0.0, min(1.0, float(v)))


def _dual_cross_info(klines_df: pd.DataFrame):
    """计算 MACD+SKDJ 双金叉共振，返回 (has_dual_cross, dual_cross_date_str)。"""
    if len(klines_df) < 30:
        return False, None

    closes = klines_df["close"].tolist()
    highs = klines_df["high"].tolist()
    lows = klines_df["low"].tolist()
    dif, dea = calc_macd_lists(closes)
    sk, sd = calc_skdj_lists(highs, lows, closes)
    best_hi = find_latest_dual_cross_bar(dif, dea, sk, sd)
    if best_hi is None:
        return False, None

    d = klines_df.iloc[best_hi]["date"]
    date_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
    return True, date_str


# ─── 单只股票分析 ─────────────────────────────────────────────────────────────

def _analyze_stock(code: str, level: str) -> dict | None:
    """
    对单只股票执行选股分析。
    返回字典或 None（分析失败时跳过）。
    """
    try:
        # 取数沿用原来的 akshare（内部已用 _bs_lock 串行化 baostock 会话，可并发调用）；
        # 不用 CChan(DATA_SRC.BAO_STOCK)，见本模块文件头注释
        df = get_kline_hist(
            code,
            period=_level_to_period(level),
            start_date=None,
            adjust="qfq",
            limit=SCREENING_KLINE_LIMIT,
        )
        if df.empty or len(df) < MIN_KLINE_NUM:
            return None

        kl_list = _build_kl_list(df, _level_to_kltype(level))

        latest_signal: CBS_Point | None = _latest_bsp(kl_list)

        # 走势分类交给现成的 WaveClassifier：线段列表配线段中枢（同级配对）
        try:
            segs = list(kl_list.seg_list)
            seg_zss = list(kl_list.segzs_list)
            trend = WaveClassifier().classify(
                segs, seg_zss, float(df["close"].iloc[-1])
            )["trend"]
        except Exception:
            log.debug("走势分类失败 code=%s level=%s", code, level, exc_info=True)
            trend = "未知"

        has_dual_cross, dual_cross_date = _dual_cross_info(df)

        return {
            "code": code,
            "trend": trend,
            "latest_signal": latest_signal,
            "has_dual_cross": has_dual_cross,
            "dual_cross_date": dual_cross_date,
        }
    except Exception:
        log.debug("单只股票选股分析失败 code=%s level=%s", code, level, exc_info=True)
        return None


# ─── 实时行情 & 基本面 ────────────────────────────────────────────────────────

def _normalize_code(code: str) -> str:
    """统一转换为纯6位数字码，便于跨数据源匹配"""
    code = str(code).strip()
    if code.startswith("sh") or code.startswith("sz") or code.startswith("bj"):
        code = code[2:]
    return code.zfill(6)


def _parse_pe_pb(info: dict) -> tuple[float | None, float | None]:
    pe = info.get("市盈率")
    pb = info.get("市净率")
    try:
        pe_f = float(pe) if pe not in (None, "", 0) else None
    except (TypeError, ValueError):
        pe_f = None
    try:
        pb_f = float(pb) if pb not in (None, "", 0) else None
    except (TypeError, ValueError):
        pb_f = None
    return pe_f, pb_f


def _quotes_from_hot(hot_list: list[dict]) -> dict[str, dict]:
    """从热门池种子行情，避免重复拉全量实时报价。"""
    out: dict[str, dict] = {}
    for item in hot_list:
        code = _normalize_code(str(item.get("code") or ""))
        if not code:
            continue
        out[code] = {
            "price": float(item.get("price") or 0),
            "change_pct": float(item.get("change_pct") or 0),
            "volume": float(item.get("volume") or 0),
            "amount": float(item.get("amount") or 0),
            "name": item.get("name") or code,
            "industry": None,
            "pe": None,
            "pb": None,
        }
    return out


def _quote_map_from_seed(candidates: list[str], seed: dict[str, dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for code in candidates:
        nc = _normalize_code(code)
        base = seed.get(nc, {})
        result[code] = {
            "price": float(base.get("price") or 0),
            "change_pct": float(base.get("change_pct") or 0),
            "volume": float(base.get("volume") or 0),
            "amount": float(base.get("amount") or 0),
            "name": base.get("name") or code,
            "industry": None,
            "pe": None,
            "pb": None,
        }
    return result


def _get_quote_and_info(
    codes: list[str],
    *,
    need_industry_name: bool = False,
    need_pe_pb: bool = False,
    quote_seed: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """
    批量获取实时行情 + 基本面信息。

    need_industry_name: 拉取行业（东方财富板块接口）
    need_pe_pb: 拉取市盈率/市净率（腾讯行情 info 字段）
    quote_seed: 热门池等已有行情时跳过全量实时报价拉取
    """
    result = {}

    if quote_seed is not None:
        for code in codes:
            nc = _normalize_code(code)
            base = quote_seed.get(nc, {})
            result[code] = {
                "price": float(base.get("price") or 0),
                "change_pct": float(base.get("change_pct") or 0),
                "volume": float(base.get("volume") or 0),
                "amount": float(base.get("amount") or 0),
                "name": base.get("name") or code,
                "industry": None,
                "pe": None,
                "pb": None,
            }
    else:
        quotes_df = get_realtime_quote(codes)
        quotes_records = quotes_df.to_dict("records") if not quotes_df.empty else []

        qmap = {}
        for q in quotes_records:
            code_key = _normalize_code(q.get("代码") or q.get("code") or "")
            qmap[code_key] = q

        for code in codes:
            norm_code = _normalize_code(code)
            q = qmap.get(norm_code, {})
            price = float(q.get("最新价") or q.get("price") or 0)
            prev_close = float(q.get("昨收") or q.get("prev_close") or 0)
            change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0

            result[code] = {
                "price": price,
                "change_pct": round(change_pct, 2),
                "volume": float(q.get("成交量") or q.get("volume") or 0),
                "amount": float(q.get("成交额") or q.get("amount") or 0),
                "name": q.get("名称") or q.get("name") or code,
                "industry": None,
                "pe": None,
                "pb": None,
            }

    if (need_industry_name or need_pe_pb) and codes:
        def fetch_one(code: str) -> tuple[str, str | None, float | None, float | None]:
            industry = None
            pe = pb = None
            try:
                if need_industry_name:
                    boards = get_stock_boards_em(code)
                    industry = boards.get("industry")
                if need_pe_pb:
                    info = get_stock_info(code)
                    pe, pb = _parse_pe_pb(info)
            except Exception:
                log.debug("股票基本面获取失败 code=%s", code, exc_info=True)
            return code, industry, pe, pb

        workers = min(30, len(codes))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for code, ind, pe, pb in pool.map(fetch_one, codes):
                if code in result:
                    if need_industry_name:
                        result[code]["industry"] = ind
                    if need_pe_pb:
                        result[code]["pe"] = pe
                        result[code]["pb"] = pb

    return result


# ─── 主选股函数（生成器版）────────────────────────────────────────────────

def screen_stocks_stream(
    change_pct_min: float | None = None,
    change_pct_max: float | None = None,
    volume_min: float | None = None,
    volume_max: float | None = None,
    industry: str | None = None,
    pe_max: float | None = None,
    pb_max: float | None = None,
    signal_types: list[str] | None = None,
    require_dual_cross: bool = False,
    level: str = "daily",
    pool_size: int = 100,
    max_results: int = 50,
):
    """
    选股主入口（生成器版）。yield 每条符合条件的结果，前端可边算边展示。

    yield dict:
        type: "progress" → { done, total }  进度
        type: "result"   → 选股结果 dict
        type: "done"     → None（全部完成）
    """
    t0 = time.time()

    hot_list = get_daily_hot_stocks(pool_size)
    t1 = time.time()
    log.info("选股候选池获取 count=%s elapsed=%.1fs", len(hot_list), t1 - t0)
    if not hot_list:
        yield {"type": "done", "total": 0}
        return

    candidates = [item["code"] for item in hot_list]
    total = len(candidates)

    need_industry_name = industry is not None
    need_pe_pb = pe_max is not None or pb_max is not None
    seed = _quotes_from_hot(hot_list)
    quote_map = _quote_map_from_seed(candidates, seed)

    # 先用热门池行情做涨跌幅/成交量预过滤，缩小行业/PE 拉取范围
    prefiltered_basic = []
    for code in candidates:
        q = quote_map.get(code, {})
        pct = q.get("change_pct", 0)
        vol = q.get("volume", 0)
        if change_pct_min is not None and pct < change_pct_min:
            continue
        if change_pct_max is not None and pct > change_pct_max:
            continue
        if volume_min is not None and vol < volume_min:
            continue
        if volume_max is not None and vol > volume_max:
            continue
        prefiltered_basic.append(code)

    if (need_industry_name or need_pe_pb) and prefiltered_basic:
        extra = _get_quote_and_info(
            prefiltered_basic,
            need_industry_name=need_industry_name,
            need_pe_pb=need_pe_pb,
            quote_seed=seed,
        )
        for code in prefiltered_basic:
            quote_map[code] = {**quote_map.get(code, {}), **extra.get(code, {})}
    t2 = time.time()
    log.info("选股行情和基本面完成 elapsed=%.1fs", t2 - t1)

    prefiltered = []
    for code in prefiltered_basic:
        q = quote_map.get(code, {})
        if industry is not None and industry not in (q.get("industry") or ""):
            continue
        if pe_max is not None and q.get("pe") is not None and q["pe"] > pe_max:
            continue
        if pb_max is not None and q.get("pb") is not None and q["pb"] > pb_max:
            continue
        prefiltered.append(code)

    if not prefiltered:
        yield {"type": "done", "total": 0}
        return

    yield {"type": "progress", "done": 0, "total": len(prefiltered)}
    t3 = time.time()
    log.info("选股预过滤完成 remaining=%s elapsed=%.1fs", len(prefiltered), t3 - t2)

    workers = min(SCREENING_WORKERS, max(1, len(prefiltered)))
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = {pool.submit(_analyze_stock, c, level): c for c in prefiltered}
    done_count = 0
    emitted_count = 0
    from concurrent.futures import as_completed

    try:
        for future in as_completed(futures):
            code = futures[future]
            done_count += 1
            if done_count % 50 == 0 or done_count == len(prefiltered):
                yield {"type": "progress", "done": done_count, "total": len(prefiltered)}

            try:
                analysis = future.result()
            except Exception:
                log.exception("选股任务执行失败 code=%s level=%s", code, level)
                analysis = None

            if analysis is None:
                continue

            if emitted_count >= max_results:
                for pending in futures:
                    if not pending.done():
                        pending.cancel()
                break

            if signal_types:
                if analysis["latest_signal"] is None:
                    continue
                # CBS_Point.type 是多值列表（一根笔可同时是多类买卖点），
                # 命中任意一个就算通过
                if not set(_bsp_labels(analysis["latest_signal"])) & set(signal_types):
                    continue

            has_dual_cross = analysis["has_dual_cross"]
            dual_cross_date = analysis["dual_cross_date"]
            if require_dual_cross and not has_dual_cross:
                continue

            q = quote_map.get(code, {})
            sig = analysis["latest_signal"]
            sig_date_str = None
            sig_conf = None
            if sig is not None:
                sig_date_str = _bsp_date_str(sig)
                sig_conf = round(_bsp_confidence(sig), 2)

            emitted_count += 1
            yield {
                "type": "result",
                "data": {
                    "code": code,
                    "name": q.get("name", code),
                    "price": q.get("price", 0),
                    "change_pct": q.get("change_pct", 0),
                    "volume": q.get("volume", 0),
                    "amount": q.get("amount", 0),
                    "industry": q.get("industry"),
                    "pe": q.get("pe"),
                    "pb": q.get("pb"),
                    "latest_signal": (_bsp_labels(sig)[0] if sig else None),
                    "latest_signal_date": sig_date_str,
                    "latest_signal_conf": sig_conf,
                    "has_dual_cross": has_dual_cross,
                    "dual_cross_date": dual_cross_date,
                    "trend": analysis["trend"],
                },
            }
            if emitted_count >= max_results:
                for pending in futures:
                    if not pending.done():
                        pending.cancel()
                break
    finally:
        for future in futures:
            future.cancel()
        pool.shutdown(wait=False, cancel_futures=True)

    t4 = time.time()
    log.info("选股完成 done=%s elapsed=%.1fs", done_count, t4 - t3)
    yield {"type": "done", "total": done_count}


def screen_stocks(
    change_pct_min: float | None = None,
    change_pct_max: float | None = None,
    volume_min: float | None = None,
    volume_max: float | None = None,
    industry: str | None = None,
    pe_max: float | None = None,
    pb_max: float | None = None,
    signal_types: list[str] | None = None,
    require_dual_cross: bool = False,
    level: str = "daily",
    pool_size: int = 100,
    max_results: int = 50,
) -> list[dict]:
    """选股主入口（兼容版）。内部调用生成器，等全部算完返回列表。"""
    output = []
    for item in screen_stocks_stream(
        change_pct_min=change_pct_min,
        change_pct_max=change_pct_max,
        volume_min=volume_min,
        volume_max=volume_max,
        industry=industry,
        pe_max=pe_max,
        pb_max=pb_max,
        signal_types=signal_types,
        require_dual_cross=require_dual_cross,
        level=level,
        pool_size=pool_size,
        max_results=max_results,
    ):
        if item["type"] == "result":
            output.append(item["data"])
            if len(output) >= max_results:
                break
    return output
