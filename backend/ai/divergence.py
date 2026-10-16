"""
背驰自动判断 — 基于 newchan 缠论笔对象 CBi 的 MACD 面积 + RSI/KDJ 背离检测。

改写说明（原实现基于 ``chanlun.elements.Bi``）：
    ``Bi`` 是纯数据模型，一根笔只有 ``start`` / ``end`` 两个日期，要取区间数据只能
    拿日期去 DataFrame 里做掩码过滤 —— 日期口径（str / datetime / Timestamp）一旦
    对不齐就整段落空。``CBi`` 自带 KLU 链表和 ``idx``，可以直接、精确地取出这一笔
    覆盖的原始K线，去掉了这个最容易错位的环节。

对外接口保持不变：模块级函数与 ``DivergenceDetector`` 的各方法签名兼容旧实现，
返回值字典的字段也逐个对齐，下游 ``analysis_agent`` / ``strategy_engine`` 无需改动。

关键约定：
    1. 笔的区间以 ``get_begin_klu()`` / ``get_end_klu()`` 的 **KLU idx 闭区间**为准，
       这与 newchan 自己算背驰（``CBi.Cal_MACD_area``）用的是同一套边界；
    2. ``self.df`` 的行位置 = KLU idx - idx_offset。idx_offset 默认 0，即「喂进来的 df
       就是参与缠论计算的那段完整行情」；若只喂了尾部窗口（如 ``df.tail(200)``），
       必须显式传 offset（= 全历史根数 - 窗口根数），否则整体错位一个<｜hy_place▁holder▁no▁813｜>；
    3. MACD / RSI / KDJ 优先取 KLU 上由引擎算好的值 —— 它是全历史递推出来的，
       在截断窗口下比临时用 pandas 重算的一列更准，且与 ``ZS.is_divergence``
       判定背驰用的是同一口径。引擎没配的指标自动退回 df 列。
"""
import pandas as pd
import numpy as np
from typing import Optional

from newchan.Bi.Bi import CBi


# MACD：后一段面积低于前一段该比例以下视为力度显著减弱
MACD_WEAKEN_RATIO = 0.85
MACD_AREA_EPS = 1e-15
PRICE_EPS = 1e-12
MIN_BIS_FOR_DIV = 4
# 每段至少若干根 K 线才参与力度对比，避免单子线噪声
MIN_BARS_PER_SEGMENT = 2
# MACD 已满足背驰时，RSI 或 KDJ(J) 同向背离各小幅抬高概率上限
OSC_CONFIRM_BOOST = 0.07

# CBi 上「引擎侧指标」到 self.df 里「pandas 自算列」的映射，用于取不到时的兜底
_DF_FALLBACK_COLUMN = {
    "macd": "bar",
    "rsi": "rsi",
    "kdj_j": "J",
}


def calculate_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """计算 MACD 指标，返回追加 dif/dea/bar 列的 DataFrame。"""
    from utils.indicators import calculate_macd_df

    return calculate_macd_df(df, fast=fast, slow=slow, signal=signal)


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """计算 RSI（Wilder 平滑，与常见行情软件一致）"""
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def calculate_kdj(
    df: pd.DataFrame,
    n: int = 9,
    m1: int = 3,
    m2: int = 3
) -> pd.DataFrame:
    """计算 KDJ 指标"""
    df = df.copy()
    low_n = df['low'].rolling(n, min_periods=1).min()
    high_n = df['high'].rolling(n, min_periods=1).max()
    rsv = (df['close'] - low_n) / (high_n - low_n + 1e-9) * 100
    df['K'] = rsv.ewm(com=m1 - 1, adjust=False).mean()
    df['D'] = df['K'].ewm(com=m2 - 1, adjust=False).mean()
    df['J'] = 3 * df['K'] - 2 * df['D']
    return df


def money(v: float) -> float:
    """格式化金额显示"""
    return round(v, 2)


def macd_area(bars: pd.Series) -> float:
    """计算 MACD 柱全绝对值面积（近似积分），忽略 NaN"""
    arr = bars.to_numpy(dtype=float)
    return float(np.nansum(np.abs(arr)))


def macd_area_directional(bars: pd.Series, seg_type: str) -> float:
    """
    与走势同向的 MACD 柱累计面积（忽略反向柱），更符合「上涨笔比红柱、下跌笔比绿柱」的习惯。
    top（向上笔顶背驰）：累计 bar > 0 部分；bottom（向下笔底背驰）：累计 bar < 0 的绝对值。
    """
    arr = bars.to_numpy(dtype=float)
    if seg_type == "top":
        return float(np.nansum(np.maximum(arr, 0.0)))
    if seg_type == "bottom":
        return float(np.nansum(np.abs(np.minimum(arr, 0.0))))
    raise ValueError(f"seg_type must be 'top' or 'bottom', got {seg_type!r}")


def _nan_extreme(series: pd.Series, *, high_extreme: bool) -> float:
    """段内 RSI/KDJ 极值；无有效数据时返回 nan"""
    a = series.to_numpy(dtype=float)
    if a.size == 0:
        # 空切片上 np.nanmax/nanmin 会直接抛 ValueError（reduce 无单位元），
        # 先把这条短路掉，窗口错位之类的场景才能平稳退化而不是崩掉
        return float(np.nan)
    if high_extreme:
        return float(np.nanmax(a))
    return float(np.nanmin(a))


def _bi_klu_list(bi: CBi) -> list:
    """
    取出这一笔真正覆盖的原始K线列表（按时间顺序）。

    Args:
        bi: 缠论笔

    Returns:
        list[CKLine_Unit]: 落在 ``[begin_klu.idx, end_klu.idx]`` 闭区间内的原始K线。

    Note:
        首尾两根合并K线里可能含有落在笔之外的原始K线（包含处理可以自由跨越分型边界），
        所以必须按 KLU 的 idx 裁一刀 —— 与 CBi.Cal_MACD_area 同一套做法。
    """
    begin_klu = bi.get_begin_klu()
    end_klu = bi.get_end_klu()
    klus = []
    for klc in bi.klc_lst:
        for klu in klc.lst:
            if klu.idx < begin_klu.idx or klu.idx > end_klu.idx:
                continue
            klus.append(klu)
    return klus


def _bi_high(bi: CBi) -> float:
    """笔区间最高价（等价于旧 Bi.high；newchan 里由 ``CBi._high()`` 给出）"""
    return float(bi._high())


def _bi_low(bi: CBi) -> float:
    """笔区间最低价（等价于旧 Bi.low；newchan 里由 ``CBi._low()`` 给出）"""
    return float(bi._low())


def _bi_end_idx(bi: CBi) -> int:
    """笔终点 KLU 的下标，用作「谁更靠近当下」的比较键（等价旧 Bi.end 的时间先后）。"""
    idx = bi.get_end_klu().idx
    # idx 未分配时 newchan 会留 -1，这里兜成 0 避免比较出负数
    return idx if isinstance(idx, int) and idx >= 0 else 0


class DivergenceDetector:
    """
    背驰检测:
    1. MACD 同向柱面积对比（向上笔累计红柱、向下笔累计绿柱；过小则回退为绝对值面积）
    2. RSI/KDJ 顶底背离（在 MACD 背驰前提下抬升概率）

    Note:
        构造时传入的 df 必须与生成 ``bis`` 的那次缠论计算同源：
        若只截取了尾部窗口，请同时给出 ``idx_offset``，
        否则 KLU idx → df 行位置的换算会错位。
    """

    def __init__(self, df: pd.DataFrame, idx_offset: int = 0):
        """
        Args:
            df: K 线表，至少含 date/open/high/low/close 列。
            idx_offset: self.df 第 0 行对应的 KLU idx。
                完整行情传 0（默认）；``df.tail(200)`` 这类尾部窗口传
                ``全历史根数 - 200``。

        Note:
            idx_offset 是为了兼容旧的调用方式而新增的可选参数，不传时行为与原实现一致。
        """
        self.df = df.copy()
        self.df = calculate_macd(self.df)
        self.df['rsi'] = calculate_rsi(self.df)
        self.df = calculate_kdj(self.df)
        self.df.reset_index(drop=True, inplace=True)
        self.idx_offset = int(idx_offset)

    def check_divergence(self, bis: list[CBi]) -> Optional[dict]:
        """
        检测背驰: 比较最近两个同向段的力度
        返回: type、probability、macd_ratio、price_drop|price_rise、description，
        以及 rsi_confirm / kdj_confirm（是否与 MACD 背驰同向的振荡器背离）。
        顶、底同时满足时取概率更高者；概率相同则取第二段结束时间更晚者。

        Args:
            bis: newchan 的笔列表（``chan[lv].bi_list`` 迭代出来的 CBi）。

        Note:
            这里同时收录确认笔与虚笔，与原实现一致 —— 虚笔虽然可能被后续走势改写，
            但它代表"当下正在形成的这段"，恰恰是背驰最该盯的地方。
        """
        if len(bis) < MIN_BIS_FOR_DIV:
            return None

        up_segments = [b for b in bis if b.is_up()]
        down_segments = [b for b in bis if b.is_down()]

        candidates: list[tuple[dict, int]] = []

        if len(up_segments) >= 2:
            seg_a, seg_b = up_segments[-2], up_segments[-1]
            result = self._check_segment_divergence(seg_a, seg_b, "top")
            if result:
                candidates.append((result, _bi_end_idx(seg_b)))

        if len(down_segments) >= 2:
            seg_a, seg_b = down_segments[-2], down_segments[-1]
            result = self._check_segment_divergence(seg_a, seg_b, "bottom")
            if result:
                candidates.append((result, _bi_end_idx(seg_b)))

        if not candidates:
            return None

        # 概率优先；同概率则第二段结束位置更靠后（更贴近当下走势）
        best = max(
            candidates,
            key=lambda item: (item[0]["probability"], item[1]),
        )
        return best[0]

    def _rsi_confirms(self, seg_type: str, s1_df: pd.DataFrame, s2_df: pd.DataFrame) -> bool:
        """顶: 价新高但段内 RSI 高点不及前一段；底: 价新低但 RSI 低点抬高"""
        if "rsi" not in s1_df.columns:
            return False
        if seg_type == "top":
            r1 = _nan_extreme(s1_df["rsi"], high_extreme=True)
            r2 = _nan_extreme(s2_df["rsi"], high_extreme=True)
            if np.isnan(r1) or np.isnan(r2):
                return False
            return r2 < r1
        r1 = _nan_extreme(s1_df["rsi"], high_extreme=False)
        r2 = _nan_extreme(s2_df["rsi"], high_extreme=False)
        if np.isnan(r1) or np.isnan(r2):
            return False
        return r2 > r1

    def _kdj_j_confirms(self, seg_type: str, s1_df: pd.DataFrame, s2_df: pd.DataFrame) -> bool:
        """以 J 线为快速振荡器，逻辑同 RSI"""
        if "J" not in s1_df.columns:
            return False
        if seg_type == "top":
            j1 = _nan_extreme(s1_df["J"], high_extreme=True)
            j2 = _nan_extreme(s2_df["J"], high_extreme=True)
            if np.isnan(j1) or np.isnan(j2):
                return False
            return j2 < j1
        j1 = _nan_extreme(s1_df["J"], high_extreme=False)
        j2 = _nan_extreme(s2_df["J"], high_extreme=False)
        if np.isnan(j1) or np.isnan(j2):
            return False
        return j2 > j1

    def _klu_metric_series(self, bi: CBi, kind: str) -> Optional[pd.Series]:
        """
        从笔自身的 KLU 上取由 newchan 引擎算好的指标序列。

        Args:
            bi: 缠论笔
            kind: ``"macd"`` / ``"rsi"`` / ``"kdj_j"``

        Returns:
            Optional[pd.Series]: 与笔覆盖的原始K线等长同序的序列；
            返回 ``None`` 表示引擎侧压根没算这个指标（比如 CChanConfig 没开 RSI/KDJ）。

        Note:
            属性是"懒创建"的（见 ``CKLine_Unit.set_metric``）：只有配置了对应算子，
            KLU 上才会有 ``macd`` / ``rsi`` / ``kdj``，所以这里必须逐个 hasattr 判断，
            不能假设一定存在。整段全 None 也按"没算"处理。
        """
        klus = _bi_klu_list(bi)
        if not klus:
            return None

        vals = []
        for klu in klus:
            if kind == "macd":
                item = getattr(klu, "macd", None)
                vals.append(None if item is None else item.macd)
            elif kind == "rsi":
                vals.append(getattr(klu, "rsi", None))
            elif kind == "kdj_j":
                item = getattr(klu, "kdj", None)
                vals.append(None if item is None else item.j)
            else:
                raise ValueError(f"unsupported metric kind: {kind!r}")

        if all(v is None for v in vals):
            return None
        return pd.Series(vals, dtype=float)

    def _segment_series(self, bi: CBi, seg_df: pd.DataFrame, kind: str) -> pd.Series:
        """
        取某笔区间上某个指标的序列：优先引擎侧值，取不到再退 pandas 自算的那一列。

        Args:
            bi: 缠论笔
            seg_df: 该笔对应的 df 切片（兜底数据源）
            kind: ``"macd"`` / ``"rsi"`` / ``"kdj_j"``

        Returns:
            pd.Series: 指标序列；两者都取不到时返回空 Series（长度为 0）。

        Note:
            优先取引擎值不只是为了省一次计算：MACD/RSI 都是有状态的递推指标，
            在截断窗口上临时重算会因为缺少预热而失真，
            而 KJLU 上的值是顺着全历史推下来的。
        """
        s = self._klu_metric_series(bi, kind)
        if s is not None:
            return s
        col = _DF_FALLBACK_COLUMN[kind]
        if len(seg_df) == 0 or col not in seg_df.columns:
            return pd.Series(dtype=float)
        return seg_df[col]

    def _augment_segment_df(self, bi: CBi, seg_df: pd.DataFrame) -> pd.DataFrame:
        """
        把引擎侧算好的 rsi / J 覆盖到切片里的同名列，
        好让 ``_rsi_confirms`` / ``_kdj_j_confirms`` 能直接沿用原来的 DataFrame 入参。

        Args:
            bi: 缠论笔
            seg_df: 该笔对应的 df 切片

        Returns:
            pd.DataFrame: 覆盖后的切片；引擎没算对应指标时原样返回（退回 pandas 自算值）。

        Note:
            长度对不上时不覆盖——那种情况说明 idx_offset 配错了或窗口没对齐，
            硬塞会造成错位，宁可用原来的列。
        """
        out = seg_df
        for kind, col in (("rsi", "rsi"), ("kdj_j", "J")):
            s = self._klu_metric_series(bi, kind)
            if s is None or len(s) != len(out):
                continue
            out = out.copy()
            out[col] = s.to_numpy()
        return out

    def _klu_range(self, bi: CBi) -> tuple[int, int]:
        """这一笔的首尾 KLU idx（闭区间），用作 ``_get_segment_df`` 的入参。"""
        return bi.get_begin_klu().idx, bi.get_end_klu().idx

    def _check_segment_divergence(
        self, seg1: CBi, seg2: CBi, seg_type: str
    ) -> Optional[dict]:
        """比较两段的力度差异"""
        s1_df = self._get_segment_df(*self._klu_range(seg1))
        s2_df = self._get_segment_df(*self._klu_range(seg2))

        bars1 = self._segment_series(seg1, s1_df, "macd")
        bars2 = self._segment_series(seg2, s2_df, "macd")

        if (
            len(bars1) < MIN_BARS_PER_SEGMENT
            or len(bars2) < MIN_BARS_PER_SEGMENT
        ):
            return None

        d1 = macd_area_directional(bars1, seg_type)
        d2 = macd_area_directional(bars2, seg_type)
        if d1 > MACD_AREA_EPS and d2 > MACD_AREA_EPS:
            macd1, macd2 = d1, d2
            macd_force_kind = "directional"
        else:
            macd1 = macd_area(bars1)
            macd2 = macd_area(bars2)
            macd_force_kind = "abs"
        if macd1 <= MACD_AREA_EPS:
            return None

        threshold = macd1 * MACD_WEAKEN_RATIO

        if seg_type == "bottom":
            price1 = _bi_low(seg1)
            price2 = _bi_low(seg2)
            if price1 <= PRICE_EPS:
                return None
            if not (price2 < price1 and macd2 < threshold):
                return None
            change_key = "price_drop"
            desc_prefix = "价格新低"
        else:
            price1 = _bi_high(seg1)
            price2 = _bi_high(seg2)
            if price1 <= PRICE_EPS:
                return None
            if not (price2 > price1 and macd2 < threshold):
                return None
            change_key = "price_rise"
            desc_prefix = "价格新高"

        ratio = macd2 / macd1
        prob = min(1.0, (1 - ratio) + 0.5)

        # 用引擎侧算好的振荡器值参与确认（取不到则退回切片里的 pandas 列）
        rsi_ok = self._rsi_confirms(
            seg_type, self._augment_segment_df(seg1, s1_df),
            self._augment_segment_df(seg2, s2_df),
        )
        kdj_ok = self._kdj_j_confirms(
            seg_type, self._augment_segment_df(seg1, s1_df),
            self._augment_segment_df(seg2, s2_df),
        )
        if rsi_ok:
            prob = min(1.0, prob + OSC_CONFIRM_BOOST)
        if kdj_ok:
            prob = min(1.0, prob + OSC_CONFIRM_BOOST)

        tags: list[str] = []
        if rsi_ok:
            tags.append("RSI背离")
        if kdj_ok:
            tags.append("KDJ背离")
        extra_txt = f"，{'/'.join(tags)}共振" if tags else ""

        delta = abs(price2 - price1)
        pct_move = round(delta / price1, 3)

        return {
            "type": seg_type,
            "probability": round(prob, 2),
            change_key: pct_move,
            "macd_ratio": round(ratio, 2),
            "macd_force": macd_force_kind,
            "rsi_confirm": rsi_ok,
            "kdj_confirm": kdj_ok,
            "description": (
                f"{desc_prefix}{money(delta):.2f}但力度减弱至{ratio:.0%}{extra_txt}"
            ),
        }

    def _get_segment_df(self, start, end) -> pd.DataFrame:
        """
        截取某笔区间的 K 线切片。

        Args:
            start: 区间起点。现在是 KLU idx（int）；传日期对象时走旧的掩码分支。
            end: 区间终点，含在内。

        Returns:
            pd.DataFrame: 切片；区间完全落在 self.df 之外时返回空表。

        Note:
            兼容两种入参是因为接口要保持不变：整数下标走 iloc（精确，
            不受索引是否被 reset 影响），日期走旧掩码，供历史调用方式继续可用。
        """
        if isinstance(start, (int, np.integer)) and isinstance(end, (int, np.integer)):
            lo = max(int(start) - self.idx_offset, 0)
            hi = min(int(end) - self.idx_offset, len(self.df) - 1)
            if hi < lo:
                return self.df.iloc[0:0]
            return self.df.iloc[lo:hi + 1]

        # 旧写法：按日期过滤
        if "date" not in self.df.columns:
            return self.df.iloc[0:0]
        mask = (self.df['date'] >= start) & (self.df['date'] <= end)
        return self.df[mask]
