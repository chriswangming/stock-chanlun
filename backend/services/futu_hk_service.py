"""
港股数据服务 - 使用富途 OpenD（futu-api）获取港股行情与 K 线

与 akshare_service.py（A 股）平行存在。
接口签名与返回结构刻意与 akshare_service 对齐，上层路由可以直接换数据源：

    get_kline_hist(...)      -> DataFrame[date, open, close, high, low, volume]
    get_realtime_quote([..]) -> DataFrame[代码,名称,最新价,涨跌幅,成交量,成交额,今开,最高,最低,昨收]
    get_stock_info(code)     -> dict（中文键，与 akshare_service.get_stock_info 同构）
    search_stocks(kw)        -> DataFrame[code, name]
    normalize_stock_code(c)  -> (code, market)

聚合接口（与 akshare_service 同名同结构，前端可直接换源）：
    get_board_constituents_em(board_name)  板块成分股
    get_daily_hot_stocks(limit)            领涨榜
    get_market_overview_bundle()           指数 + 涨跌家数 + 行业板块
    get_stock_boards_em(code)              个股所属行业/亮点
    get_stock_depth_em(code)               买卖档位盘口
    get_stock_news(limit)                  市场资讯
    get_stock_symbol_news_em(code, limit)  个股资讯

前置条件
--------
1. 启动 FutuOpenD 并登录（默认监听 127.0.0.1:11111）
2. 安装 futu-api（只有 sdist，代理环境需 `pip install --proxy "" futu-api`；
   本机可直接从 anaconda 的 site-packages 拷贝 futu 包）
3. 环境变量可覆盖地址：FUTU_OPEND_HOST / FUTU_OPEND_PORT

三个必须绕开的坑（都实测过，改动时别删掉对应防护）
--------------------------------------------------
1. OpenD 没起时 futu SDK 是【无限阻塞】而不是抛错。
   → 建连接前先 socket 探活；每次调用再套一层看门狗线程 + join(timeout)。
2. futu SDK 会起非 daemon 的回调线程，不 close 会让 Python 退出时卡死
   （FastAPI 表现为"Ctrl+C 停不下来"）。
   → 建连接时把 futu 自己的新线程设成 daemon，进程退出由 atexit 兜底关闭。
3. request_history_kline 新版返回【3 元组】(ret, data, page_req_key)，
   且单页上限 1000 根，不翻页只会拿到最早那一段。
   → _unpack3() 兼容 2/3 元组，并按 page_req_key 循环翻页。

还有两个改动时容易踩坏的隐蔽约束
--------------------------------
4. 连接是按线程缓存的（threading.local），而取数是在看门狗线程里执行的。
   **必须在 _call 之外取好 ctx 再闭包传进去**（`lambda c=ctx: c.xxx()`）。
   写成 `_retry(lambda: _get_ctx().xxx())` 的话，worker 线程会拿到自己的
   thread-local，每次调用/每翻一页都新建一条连接，OpenD 很快被打满。
5. 板块代码形如 'HK.LIST1006'，不能走数字归一化（会被转成 'HK.01006' 然后报
   "未知股票"）。查询代码统一走 to_query_code() 而不是 to_futu_code()。
   另外 Plate.OTHER 只能作为 get_owner_plate 的返回值，不能当请求参数。
"""
from __future__ import annotations

import atexit
import logging
import os
import socket
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

# ── 连接与取数参数 ────────────────────────────────────────────────────────────
FUTU_HOST: str = os.environ.get("FUTU_OPEND_HOST", "127.0.0.1").strip() or "127.0.0.1"
FUTU_PORT: int = int(os.environ.get("FUTU_OPEND_PORT", "11111") or 11111)

PROBE_TIMEOUT = 3.0     # socket 探活超时（秒）
CALL_TIMEOUT = 15.0     # 单次 futu 调用看门狗超时（秒）
MAX_COUNT = 1000        # 单页最大K线数（futu 上限）
MAX_PAGE = 60           # 翻页保护上限
RETRY_ATTEMPTS = 2      # 取数重试次数（超时会重建连接再试一次）
# 同时在飞的富途请求数。每条 OpenQuoteContext 是一条连接，OpenD 连接数有上限，
# 选股/批量扫描会并发打过来，这里压一道闸门（不是限流，超出的只是排队等一下）。
MAX_CONCURRENT_CALLS = 8

# 快照单次上限 400（第 401 只起报"请求股票个数超过限制400"）
SNAPSHOT_BATCH = 400


class FutuError(Exception):
    """富途取数失败（含未安装 SDK、OpenD 未启动、超时、接口报错）"""


class FutuUnavailable(FutuError):
    """SDK 未安装 / OpenD 没启动 —— 调用方通常直接降级返回空结果"""


class FutuTimeout(FutuError):
    """调用超时（OpenD 卡住）"""


# ── 惰性导入 futu：没装 SDK 时不要让整个后端起不来 ────────────────────────────
_futu_mod = None
_futu_import_error: Optional[str] = None


def _futu():
    """返回 futu 模块；未安装时抛 FutuUnavailable"""
    global _futu_mod, _futu_import_error
    if _futu_mod is not None:
        return _futu_mod
    if _futu_import_error:
        raise FutuUnavailable(f"futu-api 未就绪: {_futu_import_error}")
    try:
        import futu as _m
    except Exception as e:  # ImportError 及其派生（缺 simplejson 等也会在这里炸）
        _futu_import_error = str(e)
        raise FutuUnavailable(
            f"未安装 futu-api 或依赖缺失（{e}）。请先 pip install futu-api"
        )
    _futu_mod = _m
    return _m


# ── 连接管理：每线程一条 OpenQuoteContext ─────────────────────────────────────
_TLS = threading.local()
_ALL_CTX_LOCK = threading.Lock()
_CREATE_LOCK = threading.Lock()
_ALL_CTX: set = set()
# 并发闸门（见 _call 说明）
_CALL_SEM = threading.BoundedSemaphore(MAX_CONCURRENT_CALLS)


def opend_alive(host: Optional[str] = None, port: Optional[int] = None,
                timeout: float = PROBE_TIMEOUT) -> bool:
    """
    探活 OpenD。

    futu SDK 在 OpenD 没起时是阻塞重试而不是报错，所以先用裸 socket 探一下，
    探不通就直接返回 False，避免整个服务卡死在连接建立上。
    """
    host = host or FUTU_HOST
    port = int(port or FUTU_PORT)
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _create_ctx():
    """
    创建 OpenQuoteContext，并把 futu 自己的新线程设成 daemon。

    futu SDK 起的 CallbackExecutor 是非 daemon 线程，连接不 close 时 Python 退出
    会卡在 threading._shutdown 等它，连 atexit 都来不及执行（表现为"跑完不退出"）。
    daemon 只影响退出时是否等待，不影响正常取数。
    """
    futu = _futu()
    orig_start = threading.Thread.start

    def _start(self, *args, **kwargs):
        try:
            fn = getattr(self, "_target", None)
            if str(getattr(fn, "__module__", "")).startswith("futu"):
                self.daemon = True
        except Exception:
            pass
        return orig_start(self, *args, **kwargs)

    with _CREATE_LOCK:
        threading.Thread.start = _start
        try:
            return futu.OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
        finally:
            threading.Thread.start = orig_start


def _get_ctx():
    """获取当前线程的 OpenQuoteContext（不存在则建）"""
    ctx = getattr(_TLS, "ctx", None)
    if ctx is not None:
        return ctx
    _futu()  # SDK 没装时在这里就报错，不要等调用时才炸
    if not opend_alive():
        raise FutuUnavailable(
            f"无法连接富途 OpenD：{FUTU_HOST}:{FUTU_PORT}，请先启动 FutuOpenD 并登录"
        )
    ctx = _create_ctx()
    _TLS.ctx = ctx
    with _ALL_CTX_LOCK:
        _ALL_CTX.add(ctx)
    return ctx


def _safe_close(ctx):
    try:
        ctx.close()
    except Exception:
        pass


def _invalidate_ctx():
    """
    作废当前线程的连接（调用超时后那条连接可能已经卡死，不能再用）。

    close() 本身也可能阻塞，所以丢到 daemon 线程里做，不拖住当前请求。
    """
    ctx = getattr(_TLS, "ctx", None)
    _TLS.ctx = None
    if ctx is None:
        return
    with _ALL_CTX_LOCK:
        _ALL_CTX.discard(ctx)
    threading.Thread(target=_safe_close, args=(ctx,), daemon=True).start()


def close_futu():
    """关闭当前线程的连接"""
    ctx = getattr(_TLS, "ctx", None)
    _TLS.ctx = None
    if ctx is None:
        return
    with _ALL_CTX_LOCK:
        _ALL_CTX.discard(ctx)
    _safe_close(ctx)


def close_all_futu():
    """关闭所有线程创建的连接（atexit 兜底）"""
    with _ALL_CTX_LOCK:
        ctxs = list(_ALL_CTX)
        _ALL_CTX.clear()
    for ctx in ctxs:
        # 逐个放进 daemon 线程，某一个卡住不影响其余的关闭
        t = threading.Thread(target=_safe_close, args=(ctx,), daemon=True)
        t.start()
        t.join(2.0)


atexit.register(close_all_futu)


def _call(fn, timeout: float = CALL_TIMEOUT):
    """
    在 daemon 线程里执行 futu 调用 + join(timeout) 看门狗。

    超时后作废当前线程的连接（下次调用会重建），并抛 FutuTimeout。
    这样 OpenD 假死时只会拖慢一次请求，不会把整个线程池耗光。

    并发闸门：OpenD 的连接数有上限，而每条 OpenQuoteContext 是一条连接，
    这里把同时在飞的请求压到 MAX_CONCURRENT_CALLS，避免选股/批量扫描时
    瞬间开几十条连接把 OpenD 打满。
    """
    box: dict = {}

    def _run():
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - 原样抛给调用方
            box["error"] = e

    with _CALL_SEM:
        t = threading.Thread(target=_run, daemon=True, name="futu-call")
        t.start()
        t.join(timeout)

    if t.is_alive():
        _invalidate_ctx()
        raise FutuTimeout(f"富途请求超时（>{timeout}s），OpenD 可能未响应")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _retry(fn, attempts: int = RETRY_ATTEMPTS, timeout: float = CALL_TIMEOUT):
    """
    带一次重试的调用。

    只有【超时】才作废连接重建 —— 真正的超时意味着那条连接可能已经卡死。
    普通业务错误（代码不存在、未订阅等）不动连接，否则会把已经订阅好的连接也一起丢掉。
    """
    last: Exception = FutuError("未知错误")
    for i in range(1, attempts + 1):
        try:
            return _call(fn, timeout)
        except FutuUnavailable:
            raise  # SDK 没装 / OpenD 没起，重试没意义
        except FutuTimeout as e:
            last = e
            if i < attempts:
                _invalidate_ctx()
                time.sleep(0.3 * i)
                log.warning("[富途] 第 %s 次调用超时，重建连接重试: %s", i, e)
        except Exception as e:
            last = e
            if i < attempts:
                time.sleep(0.2 * i)
                log.debug("[富途] 第 %s 次调用失败，重试: %s", i, e)
    raise last


def _unpack3(result):
    """request_history_kline 新版返回 3 元组，老版 2 元组，这里统一成 3 个"""
    if isinstance(result, tuple):
        if len(result) == 3:
            return result
        if len(result) == 2:
            return result[0], result[1], None
    raise FutuError(f"富途返回结构无法解析: {type(result)}")


# ── 代码 / 周期 / 复权 映射 ───────────────────────────────────────────────────
def normalize_stock_code(code: str) -> tuple[str, str]:
    """
    规范化港股代码，返回 (code, market)，与 akshare_service 同签名。

    'HK.00700' / 'hk00700' / '00700' / '700'  ->  ('00700', 'HK')
    """
    c = str(code or "").strip()
    market = "HK"

    if "." in c:                       # HK.00700 / hk.00700
        head, _, tail = c.partition(".")
        if head.strip():
            market = head.strip().upper()
        c = tail.strip()

    c = c.lstrip("hHkK").lstrip(".") if c[:2].lower() == "hk" else c
    digits = "".join(ch for ch in c if ch.isdigit())
    if not digits:
        return c.upper(), market
    return digits.zfill(5), market


def to_futu_code(code: str) -> str:
    """转成富途格式：'00700' -> 'HK.00700'"""
    sym, market = normalize_stock_code(code)
    return f"{market}.{sym}"


def strip_market_prefix(futu_code: str) -> str:
    """'HK.00700' -> '00700'"""
    return str(futu_code).split(".", 1)[-1]


# 已知市场前缀。板块代码形如 'HK.LIST1006'，不能走数字归一化，否则会被误转成 HK.01006
_MARKET_PREFIXES = ("HK", "SH", "SZ", "US", "HK_FUTURE", "SG", "JP", "AU", "CA", "FX")


def to_query_code(code: str) -> str:
    """
    转成可直接喂给富途的查询代码。

    '00700'        -> 'HK.00700'
    'HK.00700'     -> 'HK.00700'
    'HK.LIST1006'  -> 'HK.LIST1006'   （板块代码，原样保留）
    'HK.800000'    -> 'HK.800000'
    """
    s = str(code or "").strip()
    if "." in s:
        head, _, tail = s.partition(".")
        if head.strip().upper() in _MARKET_PREFIXES and tail.strip():
            return f"{head.strip().upper()}.{tail.strip()}"
    return to_futu_code(s)


def is_hk_index(code: str) -> bool:
    """港股指数的代码段以 8 开头（HK.800000 恒生指数）"""
    sym, market = normalize_stock_code(code)
    return market == "HK" and sym.startswith("8")


# 项目内部周期串 -> futu KLType 属性名
# 覆盖 core.chanlun_analysis.level_to_period 的产物（1min->"5", 15min->"15", daily...）
# 以及 "5min"/"weekly" 这类通用周期串写法
_PERIOD_TO_KL = {
    "1min": "K_1M", "1": "K_1M",
    "3min": "K_3M", "3": "K_3M",
    "5min": "K_5M", "5": "K_5M",
    "10min": "K_10M", "10": "K_10M",
    "15min": "K_15M", "15": "K_15M",
    "30min": "K_30M", "30": "K_30M",
    "60min": "K_60M", "60": "K_60M",
    "daily": "K_DAY", "day": "K_DAY", "d": "K_DAY",
    "weekly": "K_WEEK", "week": "K_WEEK", "w": "K_WEEK",
    "monthly": "K_MON", "month": "K_MON", "m": "K_MON",
}

_ADJUST_TO_AU = {
    "qfq": "QFQ",
    "hfq": "HFQ",
    "": "NONE",
    "none": "NONE",
    None: "NONE",
}


def _ktype_of(period: str):
    futu = _futu()
    name = _PERIOD_TO_KL.get(str(period).strip().lower())
    if name is None:
        raise FutuError(f"富途不支持的K线周期: {period}")
    return getattr(futu.KLType, name)


def _autype_of(adjust: Optional[str]):
    futu = _futu()
    key = _ADJUST_TO_AU.get(str(adjust).strip().lower() if adjust else None, "QFQ")
    return getattr(futu.AuType, key)


def _norm_date(date_str, default: str) -> str:
    """把 20250101 / 2025-01-01 / 2025/01/01 统一成 'YYYY-MM-DD'"""
    if date_str is None or str(date_str).strip() in ("", "None"):
        return default
    s = str(date_str).strip().replace("/", "-")
    if s.isdigit() and len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s[:10]


# 每个交易日约 330 分钟（09:30-12:00 + 13:00-16:00）
_HK_TRADING_MINUTES_PER_DAY = 330
_MINUTES_PER_BAR = {
    "K_1M": 1, "K_3M": 3, "K_5M": 5, "K_10M": 10,
    "K_15M": 15, "K_30M": 30, "K_60M": 60,
}


def _default_start(kl_name: str, limit: int) -> str:
    """
    按周期和需要的根数反推起始日期，避免无脑从 1990 年拉导致多页翻页。

    系数已经把周末 + 港股节假日算进去（一年约 245 个交易日）。
    """
    if kl_name in _MINUTES_PER_BAR:
        days = limit * _MINUTES_PER_BAR[kl_name] / _HK_TRADING_MINUTES_PER_DAY * 1.25 + 10
    elif kl_name == "K_DAY":
        days = limit * 1.5 + 40          # 365/245 ≈ 1.49
    elif kl_name == "K_WEEK":
        days = limit * 7.1 + 40
    else:                                 # K_MON
        days = limit * 30.5 + 60
    start = datetime.now() - timedelta(days=max(int(days) + 1, 30))
    return max(start.strftime("%Y-%m-%d"), "1990-01-01")


def _to_float(x, default: float = 0.0) -> float:
    """futu 偶尔给 NaN / None / 空串，统一兜底成数字"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if v != v or v in (float("inf"), float("-inf")):
        return default
    return v


# ── 简易 TTL 缓存（与 akshare_service 同构，独立实例避免键冲突） ──────────────
_cache: OrderedDict[str, tuple] = OrderedDict()
_cache_lock = threading.RLock()
_CACHE_MAX_ENTRIES = 512


def _cache_get(key: str):
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        data, timestamp, ttl = entry
        if datetime.now().timestamp() - timestamp < ttl:
            _cache.move_to_end(key)
            return data
        _cache.pop(key, None)
        return None


def _cache_set(key: str, data, ttl: int = 60):
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
        elif len(_cache) >= _CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)
        _cache[key] = (data, datetime.now().timestamp(), ttl)


def futu_cache_stats() -> dict:
    """缓存状态（与 akshare_cache_stats 同用途，便于排查）"""
    with _cache_lock:
        return {"entries": len(_cache), "max_entries": _CACHE_MAX_ENTRIES}


# ── K 线 ─────────────────────────────────────────────────────────────────────
def _request_kline_page(ctx, futu_code: str, ktype, autype, start: str, end: str, page_key):
    """单页K线请求。ctx 由调用方传入（必须在看门狗线程之外取，见 _get_ctx 说明）"""
    return _unpack3(
        ctx.request_history_kline(
            futu_code,
            start=start,
            end=end,
            ktype=ktype,
            autype=autype,
            max_count=MAX_COUNT,
            page_req_key=page_key,
        )
    )


def _fetch_kline_df(futu_code: str, ktype, autype, start: str, end: str) -> pd.DataFrame:
    """按 page_req_key 循环翻页取全量K线（futu 单页上限 1000，不翻页只拿最早那段）"""
    futu = _futu()
    frames: list[pd.DataFrame] = []
    page_key = None
    for _ in range(MAX_PAGE):
        # 注意：必须在看门狗线程【之外】取好 ctx 再传进去，否则 worker 线程
        # 会拿到自己的 thread-local，每翻一页都新建一条连接。
        ctx = _get_ctx()
        ret, data, page_key = _retry(
            lambda c=ctx, pk=page_key: _request_kline_page(
                c, futu_code, ktype, autype, start, end, pk)
        )
        if ret != futu.RET_OK:
            raise FutuError(f"富途获取 {futu_code} K线失败: {data}")
        if data is None or len(data) == 0:
            break
        frames.append(data)
        if page_key is None:
            break

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["time_key"]).sort_values("time_key").reset_index(drop=True)

    # futu 按时间戳切边界，这里再按日期裁一次，保证与 start/end 严格一致
    day = df["time_key"].astype(str).str[:10]
    df = df[(day >= start) & (day <= end)].reset_index(drop=True)
    return df


def get_kline_hist(
    code: str,
    period: str = "daily",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    adjust: str = "qfq",
    limit: int = 500,
) -> pd.DataFrame:
    """
    获取港股历史K线（富途 OpenD）。

    period: 1min/5min/15min/30min/60min/daily/weekly/monthly（也认 "5"/"15"/"daily" 等）
    adjust: qfq=前复权 hfq=后复权 其他=不复权
    limit:  最多返回根数（与缠论/图表窗口对齐，纳入缓存键）

    返回列：date(Timestamp) / open / close / high / low / volume
    """
    limit = max(20, min(int(limit or 500), 2000))
    futu_code = to_futu_code(code)
    cache_key = f"kline:{futu_code}:{period}:{adjust}:{limit}:{start_date}:{end_date}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        ktype = _ktype_of(period)
        autype = _autype_of(adjust)
        kl_name = _PERIOD_TO_KL.get(str(period).strip().lower(), "K_DAY")

        end = _norm_date(end_date, datetime.now().strftime("%Y-%m-%d"))
        auto_start = start_date is None
        start = _norm_date(start_date, _default_start(kl_name, limit))

        df = _fetch_kline_df(futu_code, ktype, autype, start, end)

        # 自动窗口没取够（新股/长期停牌等），放宽到 1990 年再取一次
        if auto_start and 0 < len(df) < limit and start > "1990-01-01":
            df = _fetch_kline_df(futu_code, ktype, autype, "1990-01-01", end)

        if df.empty:
            return pd.DataFrame()

        out = pd.DataFrame({
            "date": pd.to_datetime(df["time_key"]),
            "open": df["open"].map(_to_float),
            "close": df["close"].map(_to_float),
            "high": df["high"].map(_to_float),
            "low": df["low"].map(_to_float),
            "volume": df["volume"].map(_to_float),
        })
        if len(out) > limit:
            out = out.tail(limit).reset_index(drop=True)

        ttl = 30 if kl_name in _MINUTES_PER_BAR else 300
        _cache_set(cache_key, out, ttl=ttl)
        return out
    except FutuError as e:
        log.warning("[富途] K线获取失败 %s %s: %s", futu_code, period, e)
        return pd.DataFrame()
    except Exception as e:
        log.warning("[富途] K线获取异常 %s %s: %s", futu_code, period, e)
        return pd.DataFrame()


# ── 快照 / 实时行情 ───────────────────────────────────────────────────────────
def get_market_snapshot(codes: list[str]) -> pd.DataFrame:
    """
    批量取快照（自动按 futu 的 400 上限分批）。

    返回富途原始列：code / name / last_price / open_price / high_price / low_price /
    prev_close_price / volume / turnover / turnover_rate / amplitude / suspension /
    sec_status / total_market_val / pe_ttm_ratio / pb_ratio / lot_size / listing_date ...
    注意：batchinfo 那条链路里的 suspension 恒为 'N/A'，快照里的才是真 bool。

    codes 既可以是股票代码（'00700' / 'HK.00700'），也可以是板块代码（'HK.LIST1006'）。
    """
    if not codes:
        return pd.DataFrame()
    futu = _futu()
    futu_codes = [to_query_code(c) for c in codes]

    frames: list[pd.DataFrame] = []
    for i in range(0, len(futu_codes), SNAPSHOT_BATCH):
        batch = futu_codes[i:i + SNAPSHOT_BATCH]
        ctx = _get_ctx()
        ret, data = _retry(lambda c=ctx, b=batch: c.get_market_snapshot(b))
        if ret != futu.RET_OK:
            raise FutuError(f"富途获取快照失败: {data}")
        if data is not None and len(data):
            frames.append(data)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def get_realtime_quote(codes: list[str]) -> pd.DataFrame:
    """
    获取港股实时行情，列名与 akshare_service.get_realtime_quote 保持一致，
    这样 routers/stocks.py 的 _realtime_quote_impl 不用改就能读。
    """
    if not codes:
        return pd.DataFrame()

    cache_key = f"hk_realtime:{','.join(sorted(str(c) for c in codes))}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        snap = get_market_snapshot(codes)
        if snap.empty:
            return pd.DataFrame()

        records = []
        for _, r in snap.iterrows():
            last = _to_float(r.get("last_price"))
            prev = _to_float(r.get("prev_close_price"))
            change_pct = ((last - prev) / prev * 100) if prev > 0 else 0.0
            records.append({
                "代码": strip_market_prefix(r.get("code", "")),
                "名称": str(r.get("name", "") or ""),
                "最新价": last,
                "涨跌幅": change_pct,
                "成交量": _to_float(r.get("volume")),
                "成交额": _to_float(r.get("turnover")),
                "今开": _to_float(r.get("open_price")),
                "最高": _to_float(r.get("high_price")),
                "最低": _to_float(r.get("low_price")),
                "昨收": prev,
            })

        df = pd.DataFrame(records)
        if not df.empty:
            _cache_set(cache_key, df, ttl=15)   # 盘中行情，短缓存
        return df
    except FutuError as e:
        log.warning("[富途] 实时行情获取失败: %s", e)
        return pd.DataFrame()
    except Exception as e:
        log.warning("[富途] 实时行情获取异常: %s", e)
        return pd.DataFrame()


def get_stock_info(code: str) -> dict:
    """
    获取单只港股基本信息，键名与 akshare_service.get_stock_info 对齐，
    另附港股特有字段（总市值/换手率/每手股数/上市日期/是否停牌）。
    """
    futu_code = to_futu_code(code)
    cache_key = f"hk_info:{futu_code}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        snap = get_market_snapshot([futu_code])
        if snap.empty:
            return {}
        r = snap.iloc[0]

        last = _to_float(r.get("last_price"))
        prev = _to_float(r.get("prev_close_price"))
        change_pct = ((last - prev) / prev * 100) if prev > 0 else 0.0
        suspended = bool(r.get("suspension")) or _to_float(r.get("volume")) <= 0

        info = {
            "代码": strip_market_prefix(r.get("code", "")),
            "名称": str(r.get("name", "") or ""),
            "现价": last,
            "涨跌幅": change_pct,
            "涨跌额": last - prev,
            "成交量": _to_float(r.get("volume")),
            "成交额": _to_float(r.get("turnover")),
            "振幅": _to_float(r.get("amplitude")),
            "最高": _to_float(r.get("high_price")),
            "最低": _to_float(r.get("low_price")),
            "今开": _to_float(r.get("open_price")),
            "昨收": prev,
            "市盈率": _to_float(r.get("pe_ttm_ratio")),
            "市净率": _to_float(r.get("pb_ratio")),
            # 港股特有
            "总市值": _to_float(r.get("total_market_val")),
            "换手率": _to_float(r.get("turnover_rate")),
            "每手股数": _to_float(r.get("lot_size")),
            "上市日期": str(r.get("listing_date", "") or ""),
            "停牌": suspended,
        }
        _cache_set(cache_key, info, ttl=60)
        return info
    except FutuError as e:
        log.warning("[富途] 港股信息获取失败 %s: %s", futu_code, e)
        return {}
    except Exception as e:
        log.warning("[富途] 港股信息获取异常 %s: %s", futu_code, e)
        return {}


# ── 股票列表 / 搜索 ───────────────────────────────────────────────────────────
def get_hk_stock_list(sec_type: str = "STOCK") -> pd.DataFrame:
    """
    取港股标的清单（get_stock_basicinfo）。

    sec_type: STOCK 正股 / IDX 指数 / ETF / WARRANT 窝轮 / BOND 债券
    注意：这里返回的 suspension 恒为字符串 'N/A'，没有参考价值；
          要判停牌请用 get_stock_info / 快照。
    """
    futu = _futu()
    cache_key = f"hk_list:{sec_type}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    st = getattr(futu.SecurityType, str(sec_type).upper(), None)
    if st is None:
        log.warning("[富途] 未知证券类型: %s", sec_type)
        return pd.DataFrame()

    ctx = _get_ctx()
    ret, data = _retry(lambda c=ctx: c.get_stock_basicinfo(futu.Market.HK, st))
    if ret != futu.RET_OK:
        log.warning("[富途] 港股清单获取失败: %s", data)
        return pd.DataFrame()
    if data is None or len(data) == 0:
        return pd.DataFrame()

    _cache_set(cache_key, data, ttl=3600 * 6)   # 标的清单一天内基本不变
    return data


# ── 快照行：把富途快照列统一成与 akshare_service 一致的中文字段口径 ─────────────
def _snapshot_rows(futu_codes: list[str]) -> list[dict]:
    """
    批量取快照并转成统一行（内部自动按 400 分批）。

    每行：code/name/price/change_pct/volume/amount/turnover_pct/pe_ttm/pb
          以及 open/high/low/prev_close/suspension
    """
    if not futu_codes:
        return []
    snap = get_market_snapshot(futu_codes)
    if snap.empty:
        return []

    rows = []
    for _, r in snap.iterrows():
        last = _to_float(r.get("last_price"))
        prev = _to_float(r.get("prev_close_price"))
        rows.append({
            "code": strip_market_prefix(r.get("code", "")),
            "name": str(r.get("name", "") or ""),
            "price": last,
            "change_pct": ((last - prev) / prev * 100) if prev > 0 else 0.0,
            "volume": _to_float(r.get("volume")),
            "amount": _to_float(r.get("turnover")),
            "turnover_pct": _to_float(r.get("turnover_rate")),
            "pe_ttm": _to_float(r.get("pe_ttm_ratio")),
            "pb": _to_float(r.get("pb_ratio")),
            "open": _to_float(r.get("open_price")),
            "high": _to_float(r.get("high_price")),
            "low": _to_float(r.get("low_price")),
            "prev_close": prev,
            "suspension": bool(r.get("suspension")),
        })
    return rows


def _index_row(futu_code: str, display_name: str) -> dict:
    """指数行：与 akshare_service._normalize_index_row 同结构"""
    rows = _snapshot_rows([futu_code])
    if not rows:
        return {"code": strip_market_prefix(futu_code), "name": display_name,
                "price": 0.0, "change_pct": 0.0}
    r = rows[0]
    return {
        "code": r["code"],
        "name": r["name"] or display_name,
        "price": r["price"],
        "change_pct": round(r["change_pct"], 4),
    }


# ── 板块 ─────────────────────────────────────────────────────────────────────
# 富途港股板块分类：INDUSTRY 行业(113个) / CONCEPT 概念(121个) / OTHER 其它 / REGION 地域
_PLATE_CLASS_MAP = {
    "INDUSTRY": "industry",
    "CONCEPT": "concept",
    "OTHER": "other",
    "REGION": "region",
}


def get_plate_list(plate_class: str = "INDUSTRY") -> pd.DataFrame:
    """
    取港股板块清单（get_plate_list）。

    plate_class: INDUSTRY 行业 / CONCEPT 概念 / OTHER 其它 / REGION 地域 / ALL 全部
    返回 DataFrame[code, plate_name, plate_id]，缓存 6 小时。
    """
    futu = _futu()
    key = str(plate_class).strip().upper()
    cache_key = f"hk_plate_list:{key}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # OTHER 只能作为 get_owner_plate 的返回值出现，不能当请求参数（富途会直接报错）
    if key not in ("INDUSTRY", "CONCEPT", "REGION", "ALL"):
        log.warning("[富途] 板块分类不可请求: %s（OTHER 仅用于返回）", plate_class)
        return pd.DataFrame()

    pc = getattr(futu.Plate, key, None)
    if pc is None:
        log.warning("[富途] 未知板块分类: %s", plate_class)
        return pd.DataFrame()

    ctx = _get_ctx()
    ret, data = _retry(lambda c=ctx: c.get_plate_list(futu.Market.HK, pc))
    if ret != futu.RET_OK:
        log.warning("[富途] 板块清单获取失败: %s", data)
        return pd.DataFrame()
    if data is None or len(data) == 0:
        return pd.DataFrame()

    _cache_set(cache_key, data, ttl=3600 * 6)
    return data


def _load_plate_name_map() -> dict:
    """
    板块名 -> (板块代码, board_type)。行业优先，概念兜底。

    与 akshare 版的 _EM_INDUSTRY_NAME_MAP / _EM_CONCEPT_NAME_MAP 同用途，
    但富途直接给清单，不用额外维护名称映射表。
    """
    cache_key = "hk_plate_name_map"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    mapping: dict[str, tuple[str, str]] = {}
    for cls in ("INDUSTRY", "CONCEPT"):
        try:
            df = get_plate_list(cls)
        except FutuError:
            continue
        if df.empty:
            continue
        board_type = _PLATE_CLASS_MAP.get(cls, cls.lower())
        for _, r in df.iterrows():
            name = str(r.get("plate_name", "") or "").strip()
            code = str(r.get("code", "") or "").strip()
            if name and code and name not in mapping:
                mapping[name] = (code, board_type)

    if mapping:
        _cache_set(cache_key, mapping, ttl=3600 * 6)
    return mapping


def _resolve_plate(board_name: str):
    """板块名 -> (板块代码, board_type, 实际板块名)；先精确后模糊"""
    name = str(board_name or "").strip()
    if not name:
        return None
    mapping = _load_plate_name_map()
    hit = mapping.get(name)
    if hit:
        return hit[0], hit[1], name
    low = name.lower()
    for k, (code, btype) in mapping.items():
        if low in k.lower() or k.lower() in low:
            log.debug("[板块] 模糊匹配: %r → %r", name, k)
            return code, btype, k
    return None


def get_all_industry_boards() -> list[dict]:
    """
    全部行业板块涨跌幅，按涨幅降序：[{name, change_pct}, ...]。
    与 akshare_service.get_all_industry_boards 同结构。

    实现：行业板块清单 + 板块快照（板块也有 last_price/prev_close_price，可直接算涨跌幅）。
    """
    cache_key = "hk_industry_boards:v1"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        df = get_plate_list("INDUSTRY")
        if df.empty:
            return []
        boards = _board_change_rows(df["code"].tolist())
        boards.sort(key=lambda x: x["change_pct"], reverse=True)
        _cache_set(cache_key, boards, ttl=120)
        return boards
    except FutuError as e:
        log.warning("[富途] 行业板块获取失败: %s", e)
        return []
    except Exception as e:
        log.warning("[富途] 行业板块获取异常: %s", e)
        return []


def _board_change_rows(plate_codes: list[str]) -> list[dict]:
    """板块代码 -> [{name, change_pct}]（板块同样有 last_price/prev_close_price）"""
    if not plate_codes:
        return []
    snap = get_market_snapshot(plate_codes)
    if snap.empty:
        return []
    out = []
    for _, r in snap.iterrows():
        last = _to_float(r.get("last_price"))
        prev = _to_float(r.get("prev_close_price"))
        out.append({
            "name": str(r.get("name", "") or ""),
            "change_pct": round(((last - prev) / prev * 100) if prev > 0 else 0.0, 4),
        })
    return out


def get_board_constituents_em(board_name: str) -> dict:
    """
    板块成分股：与 akshare_service.get_board_constituents_em 同结构。

    返回 {"sector_name", "board_type", "stocks":[{code,name,price,change_pct,
          volume,amount,turnover_pct,pe_ttm,pb,rank}], "total"}
    成分股按涨跌幅降序，缓存 90 秒。
    """
    name = str(board_name or "").strip()
    if not name:
        return {"sector_name": "", "board_type": None, "stocks": [], "total": 0}

    cache_key = f"hk_board_cons:{name}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        resolved = _resolve_plate(name)
        if resolved is None:
            out = {"sector_name": name, "board_type": None, "stocks": [], "total": 0}
            _cache_set(cache_key, out, ttl=300)
            return out
        plate_code, board_type, real_name = resolved

        futu = _futu()
        ctx = _get_ctx()
        ret, data = _retry(lambda c=ctx: c.get_plate_stock(plate_code))
        if ret != futu.RET_OK:
            raise FutuError(f"富途获取板块成分股失败: {data}")
        if data is None or len(data) == 0:
            out = {"sector_name": real_name, "board_type": board_type, "stocks": [], "total": 0}
            _cache_set(cache_key, out, ttl=90)
            return out

        codes = [str(c) for c in data["code"].tolist()]
        # 板块内可能有几百只，快照按 400 分批
        stocks: list[dict] = []
        for i in range(0, len(codes), SNAPSHOT_BATCH):
            for row in _snapshot_rows(codes[i:i + SNAPSHOT_BATCH]):
                if not row["name"]:
                    continue
                stocks.append({
                    "code": row["code"],
                    "name": row["name"],
                    "price": round(row["price"], 4),
                    "change_pct": round(row["change_pct"], 4),
                    "volume": round(row["volume"], 2),
                    "amount": round(row["amount"], 2),
                    "turnover_pct": round(row["turnover_pct"], 4),
                    "pe_ttm": round(row["pe_ttm"], 4),
                    "pb": round(row["pb"], 4),
                })

        stocks = [s for s in stocks if s["price"] > 0]     # 滤掉长期停牌/无报价
        stocks.sort(key=lambda x: x["change_pct"], reverse=True)
        for i, s in enumerate(stocks, start=1):
            s["rank"] = i

        out = {
            "sector_name": real_name,
            "board_type": board_type,
            "stocks": stocks,
            "total": len(stocks),
        }
        _cache_set(cache_key, out, ttl=90)
        return out
    except FutuError as e:
        log.warning("[富途] 板块成分股获取失败 %s: %s", name, e)
        return {"sector_name": name, "board_type": None, "stocks": [], "total": 0}
    except Exception as e:
        log.warning("[富途] 板块成分股获取异常 %s: %s", name, e)
        return {"sector_name": name, "board_type": None, "stocks": [], "total": 0}


# ── 热门股（领涨榜） ──────────────────────────────────────────────────────────
# 港股仙股极多，领涨榜容易被成交额几万港元的票刷屏。
# 默认 0 = 与 A 股版口径一致不过滤；嫌脏就调到 5_000_000（500万港元）以上。
HOT_MIN_TURNOVER = 0.0


def get_daily_hot_stocks(limit: int = 20) -> list:
    """
    当日人气榜（港股领涨榜），与 akshare_service.get_daily_hot_stocks 同结构。

    返回 [{rank, code, name, change_pct, price, volume, amount}]，按涨幅降序。
    """
    limit = max(1, min(int(limit or 20), 200))
    cache_key = f"hk_hot:{limit}:{HOT_MIN_TURNOVER}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        futu = _futu()
        ctx = _get_ctx()
        # 取多一点再过滤，避免过滤后不足 limit 条
        fetch_n = min(200, max(limit * 3, 50)) if HOT_MIN_TURNOVER > 0 else min(200, limit)
        ret, payload = _retry(
            lambda c=ctx: c.get_top_movers_rank(
                futu.Market.HK, sort_dir=futu.RankSortDir.DESCENDING, count=fetch_n
            )
        )
        if ret != futu.RET_OK:
            raise FutuError(f"富途获取领涨榜失败: {payload}")

        # 返回结构是 (ret, (总数, DataFrame))
        df = payload[1] if isinstance(payload, (tuple, list)) and len(payload) >= 2 else payload
        if df is None or len(df) == 0:
            return []

        stocks = []
        for _, r in df.iterrows():
            amount = _to_float(r.get("turnover"))
            if HOT_MIN_TURNOVER > 0 and amount < HOT_MIN_TURNOVER:
                continue
            stocks.append({
                "code": strip_market_prefix(r.get("security", "")),
                "name": str(r.get("name", "") or ""),
                "change_pct": round(_to_float(r.get("change_ratio")), 2),
                "price": round(_to_float(r.get("cur_price")), 4),
                "volume": _to_float(r.get("volume")),
                "amount": amount,
            })
            if len(stocks) >= limit:
                break

        stocks.sort(key=lambda x: x["change_pct"], reverse=True)
        for idx, s in enumerate(stocks, start=1):
            s["rank"] = idx

        _cache_set(cache_key, stocks, ttl=120)
        return stocks
    except FutuError as e:
        log.warning("[富途] 热门股获取失败: %s", e)
        return []
    except Exception as e:
        log.warning("[富途] 热门股获取异常: %s", e)
        return []


# ── 市场总览 ──────────────────────────────────────────────────────────────────
# 港股主要指数（富途代码）：800000 恒生 / 800100 国企 / 800700 恒生科技 / 800300 港股通互联网科技
_HK_INDEX_SPECS = [
    ("hsi", "HK.800000", "恒生指数"),
    ("hscei", "HK.800100", "国企指数"),
    ("hstech", "HK.800700", "恒生科技指数"),
    ("hsiii", "HK.800300", "港股通互联网科技"),
]


def get_hk_market_breadth() -> dict:
    """
    港股涨跌家数（get_rise_fall_distribution）。

    返回 {"advancers", "decliners", "unchanged"}，与 A 股版 market_breadth 同键。
    区间规则：left>=0 且 right>0 计入上涨；left<0 计入下跌；0~0 计入平盘。
    """
    cache_key = "hk_market_breadth:v1"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        futu = _futu()
        ctx = _get_ctx()
        ret, data = _retry(lambda c=ctx: c.get_rise_fall_distribution(market=futu.Market.HK))
        if ret != futu.RET_OK:
            raise FutuError(f"富途获取涨跌分布失败: {data}")

        ranges = (data or {}).get("range_list") or []
        up = down = flat = 0
        for rg in ranges:
            cnt = int(_to_float(rg.get("stock_count")))
            left = _to_float(rg.get("left_border"))
            right = _to_float(rg.get("right_border"))
            if left >= 0 and right > 0:
                up += cnt
            elif left < 0:
                down += cnt
            else:
                flat += cnt

        out = {"advancers": up, "decliners": down, "unchanged": flat}
        if up + down + flat > 0:
            _cache_set(cache_key, out, ttl=300)
        return out
    except FutuError as e:
        log.warning("[富途] 涨跌家数获取失败: %s", e)
        return {"advancers": 0, "decliners": 0, "unchanged": 0}
    except Exception as e:
        log.warning("[富途] 涨跌家数获取异常: %s", e)
        return {"advancers": 0, "decliners": 0, "unchanged": 0}


def get_market_overview_bundle() -> dict:
    """
    聚合：港股主要指数 + 涨跌家数 + 全部行业板块（供 /api/market/overview）。
    与 akshare_service.get_market_overview_bundle 同结构。
    """
    cache_key = "hk_market_overview:bundle:v1"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    indices: dict = {}
    for key, futu_code, label in _HK_INDEX_SPECS:
        try:
            indices[key] = _index_row(futu_code, label)
        except Exception as e:
            log.debug("[富途] 指数 %s 获取失败: %s", futu_code, e)
            indices[key] = {"code": strip_market_prefix(futu_code), "name": label,
                            "price": 0.0, "change_pct": 0.0}

    breadth = get_hk_market_breadth()
    all_boards = get_all_industry_boards()
    out = {
        "indices": indices,
        "market_breadth": breadth,
        "sectors": all_boards,
        "sectors_top": all_boards[:5],
        "sectors_bottom": list(reversed(all_boards[-5:])) if all_boards else [],
        "stale": False,
    }
    _cache_set(cache_key, out, ttl=60)
    return out


# ── 个股所属板块 ──────────────────────────────────────────────────────────────
def get_stock_boards_em(code: str) -> dict:
    """
    个股所属行业与亮点（get_owner_plate + 快照），与 akshare_service 同结构。

    返回 {"industry": str, "highlights": [{label, value}]}（highlights 最多 8 条）
    """
    futu_code = to_futu_code(code)
    cache_key = f"hk_boards:{futu_code}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        futu = _futu()
        ctx = _get_ctx()
        ret, data = _retry(lambda c=ctx: c.get_owner_plate([futu_code]))
        if ret != futu.RET_OK:
            raise FutuError(f"富途获取所属板块失败: {data}")

        industry = ""
        highlights: list[dict] = []
        seen: set[str] = set()

        def _add(label: str, val):
            if val is None:
                return
            s = str(val).strip()
            if not s or label in seen:
                return
            seen.add(label)
            highlights.append({"label": label, "value": s})

        if data is not None and len(data):
            plates = data.to_dict("records")
            # 行业优先，其次概念
            for want in ("INDUSTRY", "CONCEPT"):
                names = [str(p.get("plate_name", "") or "").strip()
                         for p in plates
                         if str(p.get("plate_type", "")).upper() == want]
                names = [n for n in names if n]
                if names:
                    if not industry:
                        industry = names[0]
                    _add("行业" if want == "INDUSTRY" else "概念板块", "、".join(names[:6]))
                    break
            if not industry and plates:
                industry = str(plates[0].get("plate_name", "") or "").strip()

        # 补充基本面亮点
        info = get_stock_info(code)
        if info:
            if info.get("总市值"):
                _add("总市值", f"{info['总市值'] / 1e8:.2f}亿港元")
            if info.get("市盈率"):
                _add("市盈率(TTM)", f"{info['市盈率']:.2f}")
            if info.get("市净率"):
                _add("市净率", f"{info['市净率']:.2f}")
            if info.get("每手股数"):
                _add("每手股数", f"{int(info['每手股数'])}")
            if info.get("上市日期"):
                _add("上市时间", info["上市日期"])

        # 行业排第一，其余按标签排序
        highlights.sort(key=lambda x: (0 if "行业" in x["label"] else 1, x["label"]))
        out = {"industry": industry, "highlights": highlights[:8]}
        _cache_set(cache_key, out, ttl=600)
        return out
    except FutuError as e:
        log.warning("[富途] 个股板块获取失败 %s: %s", futu_code, e)
        return {"industry": "", "highlights": []}
    except Exception as e:
        log.warning("[富途] 个股板块获取异常 %s: %s", futu_code, e)
        return {"industry": "", "highlights": []}


# ── 盘口（买卖档位） ──────────────────────────────────────────────────────────
def _ensure_order_book_subscribed(futu_code: str) -> None:
    """
    get_order_book 前必须先订阅，否则报"请先订阅OrderBook数据"。

    订阅是按连接生效的，所以记在当前 ctx 对象上；连接重建后（_invalidate_ctx）
    会拿到新的 ctx 对象，自然需要重新订阅。
    """
    ctx = _get_ctx()
    subscribed = getattr(ctx, "_ob_subscribed", None)
    if subscribed is None:
        subscribed = set()
        setattr(ctx, "_ob_subscribed", subscribed)
    if futu_code in subscribed:
        return

    futu = _futu()
    ret, msg = _retry(
        lambda: ctx.subscribe(futu_code, [futu.SubType.ORDER_BOOK], subscribe_push=False)
    )
    if ret != futu.RET_OK:
        raise FutuError(f"富途订阅摆盘失败 {futu_code}: {msg}")
    subscribed.add(futu_code)
    # 订阅是异步生效的，立刻 get_order_book 会报"请先订阅OrderBook数据"，实测需要约 1.5s
    time.sleep(1.5)


def get_stock_depth_em(code: str, num: int = 5) -> dict:
    """
    买卖档位盘口（get_order_book），与 akshare_service.get_stock_depth_em 同结构。

    返回 {"asks": [{price, volume}], "bids": [{price, volume}]}
    asks 顺序为卖五→卖一（价格由高到低），bids 为买一→买五（价格由高到低）。
    """
    num = max(1, min(int(num or 5), 10))
    futu_code = to_futu_code(code)
    cache_key = f"hk_depth:{futu_code}:{num}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        futu = _futu()
        ob = None
        # 订阅生效有延迟，最多补订阅一次
        for attempt in range(2):
            _ensure_order_book_subscribed(futu_code)
            ctx = _get_ctx()
            ret, ob = _retry(lambda c=ctx: c.get_order_book(futu_code, num=num))
            if ret == futu.RET_OK:
                break
            if attempt == 0 and "订阅" in str(ob):
                ctx = getattr(_TLS, "ctx", None)
                if ctx is not None:
                    getattr(ctx, "_ob_subscribed", set()).discard(futu_code)
                continue
            raise FutuError(f"富途获取盘口失败 {futu_code}: {ob}")

        if ob is None:
            return {"asks": [], "bids": []}

        # Ask 是卖一→卖N（价格升序），逆序后即卖N→卖一，与 A 股版一致
        asks = [{"price": _to_float(p), "volume": _to_float(v)}
                for p, v, *_ in reversed(list(ob.get("Ask") or []))]
        # Bid 已经是买一→买N（价格降序）
        bids = [{"price": _to_float(p), "volume": _to_float(v)}
                for p, v, *_ in list(ob.get("Bid") or [])]

        out = {"asks": asks, "bids": bids}
        if asks or bids:
            _cache_set(cache_key, out, ttl=10)
        return out
    except FutuError as e:
        log.warning("[富途] 盘口获取失败 %s: %s", futu_code, e)
        return {"asks": [], "bids": []}
    except Exception as e:
        log.warning("[富途] 盘口获取异常 %s: %s", futu_code, e)
        return {"asks": [], "bids": []}


# ── 新闻 ─────────────────────────────────────────────────────────────────────
# 富途没有"全市场快讯"接口，只有按关键字搜索资讯（get_search_news）
_DEFAULT_NEWS_KEYWORDS = ("港股", "恒生指数", "股市")


def _parse_news_time(raw) -> str:
    """
    富途的 publish_time 只有 'MM/DD'（今年），跨年时要退到去年。
    统一输出 'YYYY-MM-DD'；已经是完整时间的原样截断。
    """
    s = str(raw or "").strip()
    if not s:
        return ""
    if "/" in s and "-" not in s:
        mm, _, dd = s.partition("/")
        try:
            today = datetime.now()
            dt = datetime(today.year, int(mm), int(dd))
            if dt.date() > today.date():
                dt = datetime(today.year - 1, int(mm), int(dd))
            return dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return s
    return s[:19] if len(s) >= 19 else s


def _search_news_rows(keyword: str, max_count: int) -> list[dict]:
    futu = _futu()
    ctx = _get_ctx()
    ret, data = _retry(
        lambda c=ctx: c.get_search_news(keyword, max_count=max_count)
    )
    if ret != futu.RET_OK:
        raise FutuError(f"富途搜索资讯失败「{keyword}」: {data}")
    if data is None or len(data) == 0:
        return []
    return data.to_dict("records")


def get_stock_news(limit: int = 10) -> list:
    """
    港股市场资讯，与 akshare_service.get_stock_news 同结构。

    返回 [{title, time, url, source, digest}]（富途无摘要，digest 恒为空串）。
    富途没有全市场快讯流，这里是按默认关键字（港股/恒生指数/股市）搜索后按标题去重。
    """
    limit = max(1, min(int(limit or 10), 50))
    cache_key = f"hk_stock_news:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        items: list[dict] = []
        seen: set[str] = set()
        for kw in _DEFAULT_NEWS_KEYWORDS:
            if len(items) >= limit:
                break
            try:
                rows = _search_news_rows(kw, limit)
            except FutuError as e:
                log.debug("[富途] 资讯搜索「%s」失败: %s", kw, e)
                continue
            for r in rows:
                title = str(r.get("title") or "").strip()
                if not title or title in seen:
                    continue
                seen.add(title)
                items.append({
                    "title": title[:300],
                    "time": _parse_news_time(r.get("publish_time")),
                    "url": str(r.get("url") or ""),
                    "source": str(r.get("source") or "")[:48],
                    "digest": "",
                })
                if len(items) >= limit:
                    break

        _cache_set(cache_key, items, ttl=300)
        return items
    except Exception as e:
        log.warning("[富途] 市场资讯获取异常: %s", e)
        return []


def get_stock_symbol_news_em(code: str, limit: int = 8) -> list:
    """
    个股相关资讯，与 akshare_service.get_stock_symbol_news_em 同结构。

    返回 [{title, time, source, url}]。富途用股票名做关键字搜索；
    名称拿不到时退化为代码。
    """
    limit = max(1, min(int(limit or 8), 20))
    futu_code = to_futu_code(code)
    cache_key = f"hk_sym_news:{futu_code}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        info = get_stock_info(code)
        keyword = str(info.get("名称") or "").strip()
        if not keyword:
            # 代码不存在（或行情不可用）。不要用代码数字去搜 —— 富途是模糊搜索，
            # '99999' 会搜出一堆美股/澳股的内幕交易公告，属于噪音。
            return []

        rows = _search_news_rows(keyword, limit)
        items = []
        for r in rows:
            title = str(r.get("title") or "").strip()
            if not title:
                continue
            url = str(r.get("url") or "").strip()
            items.append({
                "title": title[:300],
                "time": _parse_news_time(r.get("publish_time")),
                "source": str(r.get("source") or "")[:48],
                "url": url if url.startswith("http") else "",
            })

        _cache_set(cache_key, items, ttl=600)
        return items
    except FutuError as e:
        log.warning("[富途] 个股资讯获取失败 %s: %s", futu_code, e)
        return []
    except Exception as e:
        log.warning("[富途] 个股资讯获取异常 %s: %s", futu_code, e)
        return []


def search_stocks(keyword: str, limit: int = 20) -> pd.DataFrame:
    """
    按代码前缀或名称关键字搜索港股，返回 DataFrame[code, name]（与 A 股版同构）。

    首次调用会拉一次全量港股清单（约 3400 只）并缓存 6 小时。
    """
    kw = str(keyword or "").strip()
    if not kw:
        return pd.DataFrame()

    cache_key = f"hk_search:{kw}:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    try:
        basic = get_hk_stock_list("STOCK")
        if basic.empty:
            return pd.DataFrame()

        kw_lower = kw.lower()
        kw_digits = "".join(ch for ch in kw if ch.isdigit())
        code_col = "code" if "code" in basic.columns else basic.columns[0]
        name_col = "name" if "name" in basic.columns else None

        hits = []
        for _, r in basic.iterrows():
            raw_code = str(r.get(code_col, ""))
            sym = strip_market_prefix(raw_code)
            name = str(r.get(name_col, "") or "") if name_col else ""
            # 代码匹配：去掉前导 0 后前缀匹配，'700' 能命中 '00700'
            code_hit = bool(kw_digits) and sym.lstrip("0").startswith(kw_digits.lstrip("0"))
            name_hit = kw_lower in name.lower()
            if code_hit or name_hit:
                hits.append({"code": sym, "name": name})
            if len(hits) >= limit:
                break

        df = pd.DataFrame(hits, columns=["code", "name"])
        _cache_set(cache_key, df, ttl=3600)
        return df
    except FutuError as e:
        log.warning("[富途] 港股搜索失败 %s: %s", kw, e)
        return pd.DataFrame()
    except Exception as e:
        log.warning("[富途] 港股搜索异常 %s: %s", kw, e)
        return pd.DataFrame()


# ── 健康检查 ──────────────────────────────────────────────────────────────────
def health() -> dict:
    """
    数据源健康状态，供 /api/system 之类的接口展示。

    {"available": bool, "host": str, "port": int, "sdk": bool, "opend": bool, "error": str|None}
    """
    info = {
        "available": False,
        "host": FUTU_HOST,
        "port": FUTU_PORT,
        "sdk": False,
        "opend": False,
        "error": None,
    }
    try:
        _futu()
        info["sdk"] = True
    except Exception as e:
        info["error"] = str(e)
        return info

    info["opend"] = opend_alive()
    if not info["opend"]:
        info["error"] = f"OpenD 未在 {FUTU_HOST}:{FUTU_PORT} 监听"
        return info

    info["available"] = True
    return info


__all__ = [
    "FutuError",
    "FutuUnavailable",
    "FutuTimeout",
    "normalize_stock_code",
    "to_futu_code",
    "strip_market_prefix",
    "is_hk_index",
    "get_kline_hist",
    "get_realtime_quote",
    "get_stock_info",
    "get_market_snapshot",
    "get_hk_stock_list",
    "search_stocks",
    # 与 akshare_service 对齐的 7 个聚合接口
    "get_board_constituents_em",
    "get_daily_hot_stocks",
    "get_market_overview_bundle",
    "get_stock_boards_em",
    "get_stock_depth_em",
    "get_stock_news",
    "get_stock_symbol_news_em",
    # 板块 / 市场宽度（上面接口的组成部件，也单独对外）
    "get_plate_list",
    "get_all_industry_boards",
    "get_hk_market_breadth",
    "opend_alive",
    "health",
    "futu_cache_stats",
    "close_futu",
    "close_all_futu",
]
