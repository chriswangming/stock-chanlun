"""
chan（本缠论内核）的**数据出口**：把缠论计算结果摊平成前端可直接消费的 JSON 结构。

``CChan`` 算完之后，内部是一张互相引用的对象图::

    原始K线 KLU ──包含合并──> 合并K线 KLC ──> 笔 Bi ──> 线段 Seg ──┬─> 中枢 ZS
                                                                   └─> 买卖点 BSP

前端「画K线图」的面板（``components/Chart/KLineChart.vue``）只认扁平数组，
不该去反查这些对象关系；跨模块比对（缠论内核 ↔ 前端）也需要一份
稳定、可逐项 diff 的中间产物。本模块就负责这层「摊平」。

三条硬约定（与前端 ``api/stock.ts`` 的 ``ChanlunResult`` 对齐）:

    1. **时间统一输出** ``'YYYY-MM-DD HH:mm:ss'``。
       必须与 ``/api/stocks/{code}/kline`` 的口径一致 —— 前端会把 /kline 的 K 线
       与 /chanlun 的笔/中枢混着按日期对齐，两边格式不一致就会整体错位。
       （分钟级同一天有多根 bar，所以必须带时分秒，不能只给日期。）
    2. **横坐标统一用本级别 KLU 下标 ``idx``**。
       因此 ``bis[i].start_x`` 可以直接当 ``klines`` 的数组下标用。
    3. **不裁剪**。画图时按 dataZoom 截窗口即可；这里导出的是完整计算结果。

主要入口:

    - :func:`serialize_chanlun_analysis1` —— 传 ``CChan``，返回完整响应字典（路由层用）
    - :func:`get_plot_lv_data` —— 传 ``CChanPlotMeta``，返回单一级别的完整数据
"""
from __future__ import annotations

import math
from datetime import date as _date, datetime as _datetime
from typing import Any, Dict, List, Optional

from ..Chan import CChan
from ..Common.CEnum import BI_DIR, DATA_FIELD, FX_TYPE, KL_TYPE, KLINE_DIR
from ..Common.CTime import CTime

from .PlotMeta import (
    CBS_Point_meta,
    CChanPlotMeta,
    Cklc_meta,
)

# ── 常量表 ──────────────────────────────────────────────────────────────────

#: KL_TYPE → 前端 level 标签（``/api/chanlun/{code}?level=xxx`` 用的那一套）
_KL_TYPE_TO_LEVEL: Dict[KL_TYPE, str] = {
    KL_TYPE.K_1M: "1min",
    KL_TYPE.K_5M: "5min",
    KL_TYPE.K_15M: "15min",
    KL_TYPE.K_30M: "30min",
    KL_TYPE.K_60M: "60min",
    KL_TYPE.K_DAY: "daily",
    KL_TYPE.K_WEEK: "weekly",
    KL_TYPE.K_MON: "monthly",
    KL_TYPE.K_QUARTER: "quarterly",
    KL_TYPE.K_YEAR: "yearly",
}

#: BSP_TYPE 主类型（'1'/'2'/'3'）→ 中文序数
_BSP_MAIN_CN: Dict[str, str] = {"1": "一", "2": "二", "3": "三"}

#: 买卖点「置信度」基线：纯启发式，按类别给经验权重（见 _bsp_confidence 说明）
_BSP_BASE_CONFIDENCE: Dict[str, float] = {"1": 0.75, "2": 0.70, "3": 0.65}
_BSP_DEFAULT_CONFIDENCE = 0.60
#: 线段级买卖点（级别更高、更可靠）加权
_BSP_SEG_BONUS = 0.05


# ── 基础序列化工具 ────────────────────────────────────────────────────────

def _json_num(v, round_digits: Optional[int] = None) -> Optional[float]:
    """
    把数值转成 JSON 友好的 float。

    Args:
        v: 待转换的值（float / numpy 标量 / None / 非法值都可能出现）
        round_digits: 保留小数位；None 表示**不做任何舍入**

    Returns:
        Optional[float]: 转换结果；None / NaN / Inf / 非法值一律返回 None（JSON 里是 null）

    Note:
        默认不四舍五入是有意的：这份数据的用途之一是跨项目比对计算结果，
        提前舍入会把真实的精度差异（0.000001 级别）抹掉，反而查不出问题。
        只想减小体积或看个大概时再显式传 ``round_digits``（常用 2~4）。
    """
    if v is None:
        return None
    try:
        num = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return round(num, round_digits) if round_digits is not None else num


def _json_enum_name(v) -> str:
    """
    枚举 → 可读名字（``FX_TYPE.TOP`` -> ``"TOP"``）；非枚举退化成 ``str(v)``。
    """
    return v.name if hasattr(v, "name") else str(v)


def _time_to_str(t) -> str:
    """
    把缠论时间统一成 ``'YYYY-MM-DD HH:mm:ss'``。

    Args:
        t: :class:`CTime` / ``datetime`` / ``date`` / 已经是字符串的情况

    Returns:
        str: ``'YYYY-MM-DD HH:mm:ss'`` 格式的时间串

    Note:
        日线及以上由数据源只给到日期，时分为 0，输出形如 ``'2026-10-16 00:00:00'``；
        分钟级带真实时分，形如 ``'2026-10-16 10:35:00'`` —— 这正是前端
        ``normTs`` 期待的口径，K 线轴与缠论结构才能对齐。
    """
    if isinstance(t, CTime):
        return f"{t.year:04d}-{t.month:02d}-{t.day:02d} {t.hour:02d}:{t.minute:02d}:{t.second:02d}"
    if isinstance(t, _datetime):
        return t.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(t, _date):
        return f"{t.strftime('%Y-%m-%d')} 00:00:00"
    return str(t)


def _klu_volume(klu) -> float:
    """
    取单根原始K线的成交量，缺字段时兜 0。

    Note:
        成交量挂在 ``klu.trade_info.metric`` 上（见 ``KLine/TradeInfo.py``），
        由数据源决定有没有；baostock 日线有 volume，个股也可能整段缺失。
    """
    metric = getattr(getattr(klu, "trade_info", None), "metric", None)
    if not metric:
        return 0.0
    v = metric.get(DATA_FIELD.FIELD_VOLUME)
    if v is None:
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _kl_type_to_level(kl_type) -> str:
    """
    :class:`KL_TYPE` → 前端 level 标签（``daily`` / ``30min`` / …）。

    Note:
        秒级在前端没有对应级别，统一落成 ``Ns`` 形式（如 ``5s``），
        仅用于展示，不影响任何计算。
    """
    if kl_type is None:
        return ""
    name = getattr(kl_type, "name", str(kl_type))
    if kl_type in _KL_TYPE_TO_LEVEL:
        return _KL_TYPE_TO_LEVEL[kl_type]
    body = name[2:]                      # "K_30M" -> "30M"
    if body.endswith("M"):
        return f"{body[:-1]}min"
    if body.endswith("S"):
        return f"{body[:-1]}s"
    return name.lower()


# ── 各结构序列化 ──────────────────────────────────────────────────────────

def _serialize_klines(meta: CChanPlotMeta, rd: Optional[int], with_indicators: bool):
    """
    摊平原始K线（KLU）序列，并顺手把每根K线上挂的指标抽成等长数组。

    Args:
        meta: 本级别的画图元数据
        rd: 价格保留小数位
        with_indicators: 是否同时产出副图指标数组

    Returns:
        Tuple[List[Dict], Optional[Dict]]: ``(klines, indicators)``

    Note:
        ``indicators`` 是与 ``klines`` **等长、同序**的（索引 i 对第 i 根K线），
        直接由 chan 的指标算子（``Math/MACD.py`` 等）在喂K线时算好，
        与前端 ``utils/stockIndicators.ts`` 的 EMA 递推式同源，
        两边给同一段收盘价应当得到一致结果。
    """
    klines: List[Dict[str, Any]] = []
    dif_arr: List[Optional[float]] = []
    dea_arr: List[Optional[float]] = []
    bar_arr: List[Optional[float]] = []
    boll_up: List[Optional[float]] = []
    boll_mid: List[Optional[float]] = []
    boll_down: List[Optional[float]] = []
    rsi_arr: List[Optional[float]] = []
    k_arr: List[Optional[float]] = []
    d_arr: List[Optional[float]] = []
    j_arr: List[Optional[float]] = []

    for klu in meta.klu_iter():
        klines.append({
            "idx": int(klu.idx),          # 本级别下标 = 前端 x 轴坐标
            "date": _time_to_str(klu.time),
            "open": _json_num(klu.open, rd),
            "high": _json_num(klu.high, rd),
            "low": _json_num(klu.low, rd),
            "close": _json_num(klu.close, rd),
            "volume": _json_num(_klu_volume(klu), rd),
        })
        if not with_indicators:
            continue
        # 指标是「懒创建」的：只有配置里带了该算子，klu 上才会有对应属性
        m = getattr(klu, "macd", None)
        dif_arr.append(_json_num(getattr(m, "DIF", None), rd))
        dea_arr.append(_json_num(getattr(m, "DEA", None), rd))
        bar_arr.append(_json_num(getattr(m, "macd", None), rd))
        b = getattr(klu, "boll", None)
        boll_up.append(_json_num(getattr(b, "UP", None), rd))
        boll_mid.append(_json_num(getattr(b, "MID", None), rd))
        boll_down.append(_json_num(getattr(b, "DOWN", None), rd))
        rsi_arr.append(_json_num(getattr(klu, "rsi", None), rd))
        kd = getattr(klu, "kdj", None)
        k_arr.append(_json_num(getattr(kd, "k", None), rd))
        d_arr.append(_json_num(getattr(kd, "d", None), rd))
        j_arr.append(_json_num(getattr(kd, "j", None), rd))

    if not with_indicators:
        return klines, None

    return klines, {
        "macd": {
            "dif": _compact_all_none(dif_arr),
            "dea": _compact_all_none(dea_arr),
            # 国内软件口径：柱 = (DIF - DEA) * 2
            "macd": _compact_all_none(bar_arr),
        },
        "boll": {
            "up": _compact_all_none(boll_up),
            "mid": _compact_all_none(boll_mid),
            "down": _compact_all_none(boll_down),
        },
        # 可选指标：未配置时整列为 None（前端据此回退本地计算）
        "rsi": _compact_all_none(rsi_arr),
        "kdj": {
            "k": _compact_all_none(k_arr),
            "d": _compact_all_none(d_arr),
            "j": _compact_all_none(j_arr),
        },
    }


def _klc_dir_fx(klc: Cklc_meta):
    """
    把合并K线的 ``type`` 拆成前端要的 ``dir`` 与 ``fx``。

    ``Cklc_meta.type`` 有两种取值来源（见 ``Plot/PlotMeta.py``）:

        - :class:`FX_TYPE.TOP` / ``BOTTOM``：这根合并K线本身是分型
        - :class:`KLINE_DIR.UP` / ``DOWN``：普通合并K线，标记的是**合并方向**

    Returns:
        Tuple[str, Optional[str]]: ``(dir, fx)``，``dir`` ∈ ``{"up","down"}``，
        ``fx`` ∈ ``{"top","bottom",None}``。

    Note:
        ``dir`` 决定前端框的颜色（up=红框 / down=绿框）。顶分型按「向上」取红，
        底分型按「向下」取绿；普通合并K线直接用它自己的合并方向。
    """
    typ = klc.type
    if isinstance(typ, FX_TYPE):
        if typ == FX_TYPE.TOP:
            return "up", "top"
        if typ == FX_TYPE.BOTTOM:
            return "down", "bottom"
        return "up", None          # UNKNOWN：不是分型，方向无意义
    # KLINE_DIR
    if typ == KLINE_DIR.DOWN:
        return "down", None
    return "up", None


def _serialize_merged_klines(meta: CChanPlotMeta, rd: Optional[int]) -> List[Dict[str, Any]]:
    """
    摊平包含处理后的合并K线（KLC）序列 —— 前端主图用它画「合并区间」框。

    Note:
        ``included`` 只放**被首根吃掉的**那几根（不含首根），与前端
        ``MergedKline`` 的注释口径一致：首根已由 ``start``/``end`` 区间表示，
        再画一遍会跟外框重叠。想拿完整分组用 ``klu_idx_list``。
    """
    out: List[Dict[str, Any]] = []
    for i, klc in enumerate(meta.klc_list):
        dir_, fx = _klc_dir_fx(klc)
        klus = list(klc.klu_list)
        out.append({
            # 在**合并后序列**里的下标（与 fenxings[i].index 同一套编号）
            "idx": i,
            "start": _time_to_str(klus[0].time) if klus else None,
            "end": _time_to_str(klus[-1].time) if klus else None,
            "start_idx": int(klc.begin_idx),
            "end_idx": int(klc.end_idx),
            "high": _json_num(klc.high, rd),
            "low": _json_num(klc.low, rd),
            "dir": dir_,
            "count": len(klus),
            "fx": fx,
            "included": [
                {
                    "date": _time_to_str(k.time),
                    "high": _json_num(k.high, rd),
                    "low": _json_num(k.low, rd),
                }
                for k in klus[1:]
            ],
            # 完整分组（含首根），供其它项目做「合并不丢K线」校验：
            # sum(len(klu_idx_list)) 应等于 klines 长度
            "klu_idx_list": [int(k.idx) for k in klus],
        })
    return out


def _serialize_fenxings(meta: CChanPlotMeta, rd: Optional[int]) -> List[Dict[str, Any]]:
    """
    从合并K线序列里抽出分型（顶 / 底）。

    Note:
        ``index`` 是**合并后序列**的下标，不是原始K线下标 —— 这一点极易踩坑，
        想定位原始K线请用 ``date`` 字段。
    """
    out: List[Dict[str, Any]] = []
    for i, klc in enumerate(meta.klc_list):
        if not isinstance(klc.type, FX_TYPE):
            continue
        if klc.type not in (FX_TYPE.TOP, FX_TYPE.BOTTOM):
            continue
        klus = list(klc.klu_list)
        out.append({
            "date": _time_to_str(klus[0].time) if klus else None,
            "high": _json_num(klc.high, rd),
            "low": _json_num(klc.low, rd),
            "type": klc.type.name.lower(),
            "index": i,
        })
    return out


def _serialize_bis(meta: CChanPlotMeta, rd: Optional[int], idx2date: Dict[int, str]):
    """
    摊平笔序列。

    Note:
        ``start_x`` / ``end_x`` 是**本级别 KLU 下标**，可直接索引 ``klines``；
        ``start`` / ``end`` 是对应的日期串（前端 ``Bi`` 接口要求的字段）。
    """
    out: List[Dict[str, Any]] = []
    for bi_meta in meta.bi_list:
        bx, ex = int(bi_meta.begin_x), int(bi_meta.end_x)
        by, ey = _json_num(bi_meta.begin_y, rd), _json_num(bi_meta.end_y, rd)
        out.append({
            "id": f"bi_{bi_meta.idx}",
            "idx": int(bi_meta.idx),
            "start": idx2date.get(bx),
            "end": idx2date.get(ex),
            "start_x": bx,
            "end_x": ex,
            "direction": _json_enum_name(bi_meta.dir).lower(),
            "high": max(by, ey) if by is not None and ey is not None else None,
            "low": min(by, ey) if by is not None and ey is not None else None,
            "start_price": by,
            "end_price": ey,
            # False = 尾部「虚笔」：正在走、尚未确认，前端画成虚线
            "is_sure": bool(bi_meta.is_sure),
        })
    return out


def _serialize_xians(meta: CChanPlotMeta, rd: Optional[int], idx2date: Dict[int, str]):
    """
    摊平线段（笔的上一级）与线段的线段（segseg）序列。

    Note:
        线段端点取自首/末笔的端点 KLU；``is_sure=False`` 的段多为尾部未定型
        或首段切分出来的残留段，前端按虚线处理。
    """
    def _one(seg_meta, prefix):
        """把一条线段 meta 转成前端要的 JSON：含起止日期与 idx、方向、高低价、确定标记与趋势线。
        """
        bx, ex = int(seg_meta.begin_x), int(seg_meta.end_x)
        by, ey = _json_num(seg_meta.begin_y, rd), _json_num(seg_meta.end_y, rd)
        tl = {}
        for name, line in (seg_meta.tl or {}).items():
            try:
                x0, y0, x1, y1 = seg_meta.format_tl(line)
                tl[name] = {
                    "begin_x": _json_num(x0, rd),
                    "begin_y": _json_num(y0, rd),
                    "end_x": _json_num(x1, rd),
                    "end_y": _json_num(y1, rd),
                }
            except Exception:
                continue
        return {
            "id": f"{prefix}_{seg_meta.idx}",
            "idx": int(seg_meta.idx),
            "start": idx2date.get(bx),
            "end": idx2date.get(ex),
            "start_x": bx,
            "end_x": ex,
            "direction": _json_enum_name(seg_meta.dir).lower(),
            "high": max(by, ey) if by is not None and ey is not None else None,
            "low": min(by, ey) if by is not None and ey is not None else None,
            "start_price": by,
            "end_price": ey,
            "is_sure": bool(seg_meta.is_sure),
            "trend_line": tl,
        }

    return [ _one(s, "xian") for s in meta.seg_list ], [ _one(s, "segseg") for s in meta.segseg_list ]


def _serialize_zhongshus(meta: CChanPlotMeta, rd: Optional[int], idx2date: Dict[int, str]) -> List[Dict[str, Any]]:
    """
    摊平中枢序列。

    Note:
        ``start`` / ``end`` 是中枢首尾原始K线的日期；``center`` 为中位价
        (``range_high`` + ``range_low``) / 2，买卖点判定常拿它当参照。
    """
    out: List[Dict[str, Any]] = []
    for i, zs in enumerate(meta.zs_lst):
        out.append({
            "id": f"zs_{i}",
            "start": idx2date.get(int(zs.begin)),
            "end": idx2date.get(int(zs.end)),
            "start_idx": int(zs.begin),
            "end_idx": int(zs.end),
            "range_high": _json_num(zs.high, rd),
            "range_low": _json_num(zs.low, rd),
            "center": _json_num((zs.high + zs.low) / 2, rd),
            "is_sure": bool(zs.is_sure),
            # 一笔中枢：整个中枢其实只由一根笔构成，可信度低，前端可淡化
            "is_one_bi_zs": bool(zs.is_onebi_zs),
            # 被合并进本中枢的子中枢数量；0 = 这是个「原生」中枢
            "sub_zs_cnt": len(zs.sub_zs_lst),
        })
    return out


def _compact_all_none(arr: List[Optional[float]]) -> Optional[List[Optional[float]]]:
    """
    整列全是 None 时折成 None，避免往响应里塞一大片无意义的 null。

    Note:
        RSI / KDJ 属于**可选**指标 —— 只有 ChanConfig 里配了对应算子，
        klu 上才会有值（见 ``KLine_Unit.set_metric``）。没配时这里返回 None，
        前端看到是空就知道该退回自己的本地计算。
    """
    if not arr:
        return None
    return None if all(v is None for v in arr) else arr


def _bsp_type_cn(bsp_type: str, is_buy: bool) -> str:
    """
    把 chan 的买卖点类型串翻成前端展示用的中文标签。

    Args:
        bsp_type: ``CBS_Point.type2str()`` 的产物，形如 ``"1"`` / ``"1p"`` / ``"3a,2"``
        is_buy: True 是买点

    Returns:
        str: ``一买`` / ``二买`` / ``三买`` / ``一卖`` / ``二卖`` / ``三卖``

    Note:
        一根笔可能同时命中多类（type 会是逗号串）。这里取**第一个**，
        即最早登记、最"根"的那类；完整类型串另外放在 ``bsp_type`` 字段里。
        ``BSP_TYPE`` 的 ``value`` 首字符即主类型（``'1p'[0] == '1'``）。
    """
    head = (bsp_type or "").split(",")[0].strip()[:1]
    return f"{_BSP_MAIN_CN.get(head, head or '?')}{'买' if is_buy else '卖'}"


def _bsp_confidence(bsp_meta: CBS_Point_meta) -> float:
    """
    估算买卖点置信度（**纯启发式，不是校准过的概率**）。

    ``CBS_Point`` 本身不带置信度，只有一堆特征；而前端
    ``Signal.confidence`` 是必填字段。这里的做法是：

        基线（按主类型：1类 0.75 / 2类 0.70 / 3类 0.65）
        + 线段级买卖点再加 0.05（级别更高、更可靠）

    若哪天要接真实的模型打分，改这一个函数即可。
    """
    head = (bsp_meta.type or "").split(",")[0].strip()[:1]
    base = _BSP_BASE_CONFIDENCE.get(head, _BSP_DEFAULT_CONFIDENCE)
    if bsp_meta.is_seg:
        base += _BSP_SEG_BONUS
    return round(min(base, 1.0), 2)


def _serialize_signals(
    meta: CChanPlotMeta,
    rd: Optional[int],
    idx2date: Dict[int, str],
    level: str,
) -> List[Dict[str, Any]]:
    """
    摊平买卖点序列（笔级 + 线段级，按时间升序）。

    Note:
        ``CBS_Point_meta.desc()`` 给的是 ``b1`` / ``※b1`` 这种内部简写，
        这里另外拼一份人可读的 ``description``。
    """
    raw = list(meta.bs_point_lst) + list(meta.seg_bsp_lst)
    out: List[Dict[str, Any]] = []
    for bsp in raw:
        x = int(bsp.x)
        price = _json_num(bsp.y, rd)
        type_cn = _bsp_type_cn(bsp.type, bsp.is_buy)
        conf = _bsp_confidence(bsp)
        out.append({
            "type": type_cn,                       # 一买 / 一卖 / 二买 / ...
            "bsp_type": bsp.type,                  # 原始类型串，如 "1p" / "3a,2"
            "level": level,
            "price": price,
            "datetime": idx2date.get(x),
            "idx": x,
            "confidence": conf,
            "is_buy": bool(bsp.is_buy),
            "is_seg": bool(bsp.is_seg),
            # 买卖点本身不提供止损止盈，留空由策略层（ai/）决定
            "stop_loss": None,
            "take_profit": None,
            "description": f"{'线段' if bsp.is_seg else '笔'}级别{type_cn}（{bsp.type}）",
        })
    # bsp_iter / seg_bsp 各自内部有序，但两者混合后必须按时间重排
    out.sort(key=lambda s: (s["datetime"] or "", s["idx"]))
    return out


def _serialize_support_resistance(
    meta: CChanPlotMeta,
    rd: Optional[int],
    idx2date: Dict[int, str],
    signals: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    汇总支撑位 / 阻力位。

    来源与权重（沿用旧引擎 ``chanlun/signal_detector.detect_support_resistance`` 的口径）:

        ==================  ==============================  ========
        来源                说明                            strength
        ==================  ==============================  ========
        ``zhongshu``        中枢下沿=支撑 / 上沿=阻力        0.85
        ``bi_low``          向上笔的低点 = 支撑              0.60
        ``bi_high``         向下笔的高点 = 阻力              0.60
        ``kline_low``       线段低点 / 近 20 根最低 = 支撑   0.75 / 0.50
        ``kline_high``      线段高点 / 近 20 根最高 = 阻力   0.75 / 0.50
        ``signal``          买卖点价位                      取该信号 confidence
        ==================  ==============================  ========

    Returns:
        List[Dict]: 去重后按 ``strength`` 降序排列的支撑阻力位列表

    Note:
        去重按 ``f"{price:.2f}"`` **两位小数精确匹配**判等（而非区间容差），
        同价位只保留 strength 更高的那条。
    """
    levels: List[Dict[str, Any]] = []

    # ── 1. 中枢：多空反复争夺的区域，上下沿是最重要的支撑 / 阻力 ──
    for i, zs in enumerate(meta.zs_lst):
        dt = idx2date.get(int(zs.begin))
        levels.append({
            "type": "support", "price": _json_num(zs.low, rd),
            "source": "zhongshu", "relatedId": f"zs_{i}", "datetime": dt, "strength": 0.85,
        })
        levels.append({
            "type": "resistance", "price": _json_num(zs.high, rd),
            "source": "zhongshu", "relatedId": f"zs_{i}", "datetime": dt, "strength": 0.85,
        })

    # ── 2. 笔端点：向下笔的高点是阻力，向上笔的低点是支撑 ──
    # 向下笔收在低位 -> 该低点构成支撑；向上笔收在高位 -> 该高点构成阻力
    for bi_meta in list(meta.bi_list)[-10:]:
        is_down = bi_meta.dir == BI_DIR.DOWN
        levels.append({
            "type": "support" if is_down else "resistance",
            "price": _json_num(bi_meta.end_y, rd),
            "source": "bi_low" if is_down else "bi_high",
            "relatedId": f"bi_{bi_meta.idx}",
            "datetime": idx2date.get(int(bi_meta.end_x)),
            "strength": 0.6,
        })

    # ── 3. 线段端点（级别高于笔，权重更大）──
    for seg_meta in list(meta.seg_list)[-6:]:
        is_down = seg_meta.dir == BI_DIR.DOWN
        levels.append({
            "type": "support" if is_down else "resistance",
            "price": _json_num(seg_meta.end_y, rd),
            "source": "kline_low" if is_down else "kline_high",
            "relatedId": f"xian_{seg_meta.idx}",
            "datetime": idx2date.get(int(seg_meta.end_x)),
            "strength": 0.75,
        })

    # ── 4. 买卖点价位本身 ──
    for sig in signals:
        levels.append({
            "type": "support" if sig.get("is_buy") else "resistance",
            "price": sig.get("price"),
            "source": "signal",
            "relatedId": "",
            "datetime": sig.get("datetime"),
            "strength": sig.get("confidence") or 0.0,
        })

    # ── 5. 近 20 根K线的极值 ──
    klus = list(meta.klu_iter())[-20:]
    lows = [k for k in klus if k.low]
    highs = [k for k in klus if k.high]
    if lows:
        min_kl = min(lows, key=lambda x: x.low)
        levels.append({
            "type": "support", "price": _json_num(min_kl.low, rd),
            "source": "kline_low", "relatedId": "",
            "datetime": idx2date.get(int(min_kl.idx)), "strength": 0.5,
        })
    if highs:
        max_kl = max(highs, key=lambda x: x.high)
        levels.append({
            "type": "resistance", "price": _json_num(max_kl.high, rd),
            "source": "kline_high", "relatedId": "",
            "datetime": idx2date.get(int(max_kl.idx)), "strength": 0.5,
        })

    # ── 去重：同价位只留最强的那条 ──
    deduped: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for lvl in levels:
        if lvl.get("price") is None:
            continue
        key = f"{float(lvl['price']):.2f}"
        if key not in seen:
            seen[key] = len(deduped)
            deduped.append(lvl)
        elif (lvl.get("strength") or 0) > (deduped[seen[key]].get("strength") or 0):
            deduped[seen[key]] = lvl

    deduped.sort(key=lambda l: -(l.get("strength") or 0))
    return deduped


def _detect_trend(meta: CChanPlotMeta) -> str:
    """
    判断当前走势：上涨 / 下跌 / 盘整 / 未知。

    Note:
        判定顺序（与旧引擎 ``SignalDetector.detect_trend`` 一致，**不可调换**）:

            1. 没有任何线段 → ``未知``；
            2. 中枢多于 1 个 → ``盘整``（两个以上中枢本身就是最强的结构性证据，
               优先于方向统计）；
            3. 否则看最近 3 条线段里 up / down 哪边多；
            4. 一样多 → ``盘整``。

        只看最近 3 段，窗口很窄，短周期数据下容易来回切换 —— 这是已知特性。
    """
    segs = list(meta.seg_list)
    if not segs:
        return "未知"
    if len(meta.zs_lst) > 1:
        return "盘整"
    recent = segs[-3:]
    downs = sum(1 for s in recent if s.dir == BI_DIR.DOWN)
    ups = sum(1 for s in recent if s.dir == BI_DIR.UP)
    if downs > ups:
        return "下跌"
    if ups > downs:
        return "上涨"
    return "盘整"


def _make_summary(trend: str, signals: List[Dict[str, Any]], zhongshus: List[Dict[str, Any]]) -> str:
    """
    生成一段简短的自然语言总结，供前端直接展示。

    Args:
        trend: 当前走势
        signals: 已序列化的买卖点
        zhongshus: 已序列化的中枢

    Returns:
        str: 形如 ``当前走势: 上涨 | 中枢区间: [10.20, 11.30] | 最近信号: 一买 @ 10.25``

    Note:
        只描述**最后一个**中枢与**最后一个**信号（当下状态），历史不进总结。
    """
    parts = [f"当前走势: {trend}"]
    if zhongshus:
        last = zhongshus[-1]
        lo, hi = last.get("range_low"), last.get("range_high")
        if lo is not None and hi is not None:
            parts.append(f"中枢区间: [{float(lo):.2f}, {float(hi):.2f}]")
    if signals:
        latest = signals[-1]
        price = latest.get("price")
        price_txt = f"{float(price):.2f}" if price is not None else "—"
        parts.append(f"最近信号: {latest.get('type')} @ {price_txt}")
    else:
        parts.append("暂无明确信号")
    return " | ".join(parts)


# ── 对外入口 ──────────────────────────────────────────────────────────────

def GetPlotMeta(chan: CChan) -> List[CChanPlotMeta]:
    """
    为每个级别各造一份画图元数据。

    Args:
        chan: 已算完的 :class:`CChan`

    Returns:
        List[CChanPlotMeta]: 顺序与 ``chan.lv_list`` 一致（从大级别到小级别）
    """
    return [CChanPlotMeta(chan[kl_type]) for kl_type in chan.lv_list]


def get_plot_lv_data(
    meta: CChanPlotMeta,
    round_digits: Optional[int] = None,
    *,
    stock_code: Optional[str] = None,
    level: Optional[str] = None,
    with_indicators: bool = True,
) -> Dict[str, Any]:
    """
    把**单一级别**的全部缠论画图数据摊平成可直接 ``json.dump`` 的字典。

    覆盖前端画K线图面板需要的每一项（对应 ``api/stock.ts`` 的各个接口）:

        ====================  ==========================================
        字段                  含义
        ====================  ==========================================
        ``klines``            原始K线（未做包含处理）
        ``merged_klines``     包含处理后的合并K线（主图画区间框）
        ``fenxings``          顶 / 底分型
        ``bis``               笔
        ``xians``             线段（笔的上一级）
        ``zhongshus``         中枢
        ``signals``           买卖点（笔级 + 线段级合并，按时间升序）
        ``supportResistance`` 支撑 / 阻力位
        ``indicators``        副图指标：MACD / BOLL / RSI / KDJ
        ====================  ==========================================

    Args:
        meta: 该级别的画图元数据（``CChanPlotMeta``）
        round_digits: 价格保留小数位，None 表示不处理
        stock_code: 标的代码；None 时尽力从 ``meta.data`` 上取，取不到就空串
        level: 级别标签（如 ``daily``）；None 时由 ``meta.data.kl_type`` 推导
        with_indicators: 是否附带副图指标数组（关掉可显著减小响应体积）

    Returns:
        Dict[str, Any]: 完整响应字典，结构与前端 ``ChanlunResult`` 对齐

    Note:
        - 与画图不同，这里**不裁剪 x_range**，导出的是完整计算结果。
        - ``merged_klines[*].klu_idx_list`` 保留了包含关系明细（这根合并K线
          吃掉了哪些原始K线，含首根）。所有 list 长度之和 == 原始K线根数，
          这是校验「包含处理有没有吞掉原始K线」最简单的不变量。
        - 坐标口径统一用 KLU 在本级别的下标 ``idx``（不是日期字符串），
          所以 ``bis[i].start_x`` 可以直接当 ``klines`` 的数组下标用。
    """
    # KLU 下标 → 日期串。笔/线段/中枢只记录端点 KLU 的下标，
    # 而前端接口要求日期，所以这里建一次映射统一转换。
    idx2date: Dict[int, str] = {
        int(klu.idx): _time_to_str(klu.time) for klu in meta.klu_iter()
    }

    klines, indicators = _serialize_klines(meta, round_digits, with_indicators)
    merged_klines = _serialize_merged_klines(meta, round_digits)
    fenxings = _serialize_fenxings(meta, round_digits)
    bis = _serialize_bis(meta, round_digits, idx2date)
    xians, segsegs = _serialize_xians(meta, round_digits, idx2date)
    zhongshus = _serialize_zhongshus(meta, round_digits, idx2date)

    lv = level or _kl_type_to_level(getattr(meta.data, "kl_type", None))
    signals = _serialize_signals(meta, round_digits, idx2date, lv)
    support_resistance = _serialize_support_resistance(
        meta, round_digits, idx2date, signals
    )
    trend = _detect_trend(meta)
    summary = _make_summary(trend, signals, zhongshus)

    return {
        # ── 元信息 ──
        "stock_code": stock_code if stock_code is not None else "",
        "level": lv,
        # 实际K线周期：1min 级别实际取 5 分钟数据，这里照样标注出来
        "data_period": "5min" if lv == "1min" else lv,
        "trend": trend,
        "summary": summary,
        # ── 画图数据 ──
        "klines": klines,
        "total": len(klines),
        "merged_klines": merged_klines,
        "fenxings": fenxings,
        "bis": bis,
        "xians": xians,
        "zhongshus": zhongshus,
        "signals": signals,
        "supportResistance": support_resistance,
        # ── 统计 ──
        "count": {
            "kline": len(klines),
            "kline_combine": len(merged_klines),
            "fenxings": len(fenxings),
            "bi": len(bis),
            "xians": len(xians),
            "segsegs": len(segsegs),
            "zhongshus": len(zhongshus),
            "signals": len(signals),
            "supportResistance": len(support_resistance),
        },
        # ── 副图指标（与 klines 等长同序）──
        "indicators": indicators,
        # 线段的线段（segseg），前端暂无对应面板，先一并带出备查
        "segsegs": segsegs,
    }


def serialize_chanlun_analysis(
    chan: CChan,
    round_digits: Optional[int] = None,
    *,
    stock_code: Optional[str] = None,
    level: Optional[str] = None,
    with_indicators: bool = True,
) -> dict:
    """
    把 ``CChan`` 的**最高级别**结果序列化成前端响应字典（路由层入口）。

    Args:
        chan: 已算完的 :class:`CChan`
        round_digits: 价格保留小数位，None 表示不处理
        stock_code: 覆盖返回的 ``stock_code``；None 时用 ``chan.code``
        level: 覆盖返回的 ``level``；None 时由最高级别的 ``KL_TYPE`` 推导
        with_indicators: 是否附带副图指标数组

    Returns:
        dict: 完整响应字典，字段与前端 ``ChanlunResult`` 一致

    Note:
        只取 ``chan.lv_list[0]``（最高级别）。需要多级别时用
        :func:`GetPlotMeta` 逐个级别调 :func:`get_plot_lv_data`。
    """
    plot_metas = GetPlotMeta(chan)
    code = stock_code if stock_code is not None else getattr(chan, "code", "")
    return get_plot_lv_data(
        meta=plot_metas[0],
        round_digits=round_digits,
        stock_code=code,
        level=level,
        with_indicators=with_indicators,
    )
