"""共享技术指标计算（选股 / 背驰 / 前端算法对齐）。

本模块是「后端与前端、选股与个股分析」共用的唯一指标口径来源：

    - list 版（ema_series / calc_macd_lists / calc_skdj_lists / 双金叉判定）
      用于选股这类只需要序列、不需要 DataFrame 的场景，与前端
      stockIndicators 的实现逐行对齐，避免出现「前端有信号、后端选不出来」。
    - DataFrame 版（calculate_macd_df）用于背驰检测等向量化场景。

改动这里任何一个公式都要同步前端，否则双金叉标记会前后端打架。
"""
from __future__ import annotations

import pandas as pd


def ema_series(values: list[float], period: int) -> list[float]:
    """指数移动平均（EMA）递推，返回与输入等长的序列。

    递推式：EMA[i] = v[i] * k + EMA[i-1] * (1 - k)，其中 k = 2 / (period + 1)。

    Args:
        values: 原始序列（通常是收盘价）。
        period: EMA 周期，越大越平滑。

    Returns:
        与 values 等长的 EMA 序列；空输入返回 []。

    Note:
        种子取首个值（EMA[0] = values[0]），与 pandas `ewm(span=period, adjust=False)`
        口径一致；但与其它平台「用前 period 根均值做种子」的写法存在微小差异，
        跨端对账指标数值时需要留意这一点。
    """
    if not values:
        return []
    # 平滑系数：period 越大 k 越小，历史权重越高
    k = 2 / (period + 1)
    # 首个值没有前值可用，直接作为递推种子
    out = [values[0]]
    for i in range(1, len(values)):
        out.append(values[i] * k + out[-1] * (1 - k))
    return out


def calc_macd_lists(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float], list[float]]:
    """MACD 的 DIF / DEA 两条线（list 版）。

    DIF = EMA(fast) - EMA(slow)，DEA = EMA(signal, DIF)，参数与前端
    stockIndicators、选股双金叉判定完全一致。

    Args:
        closes: 收盘价序列（按时间升序）。
        fast / slow / signal: 快线 / 慢线 / 信号线周期，默认 12 / 26 / 9。

    Returns:
        (dif, dea) 两个等长序列。
    """
    ema12 = ema_series(closes, fast)
    ema26 = ema_series(closes, slow)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = ema_series(dif, signal)
    return dif, dea


def calc_skdj_lists(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    n: int = 9,
    smooth_n: int = 3,
    smooth_m: int = 1,
) -> tuple[list[float | None], list[float | None]]:
    """通达信风格慢速随机指标 SKDJ，与前端 stockIndicators / 选股一致。

    计算分三步：RSV → SK（第一段平滑）→ SD（第二段平滑）。

    Args:
        highs / lows / closes: 最高 / 最低 / 收盘价序列，按时间升序且等长。
        n: RSV 回看窗口，默认 9。
        smooth_n / smooth_m: 平滑分母 / 分子，默认 3 / 1（即 alpha = 1/3）。

    Returns:
        (sk, sd) 两条等长序列；前 n-1 根因 RSV 未成形为 None，
        下游判定金叉时必须先跳过 None。
    """
    # 1) RSV：当日收盘价在最近 n 日 [最低, 最高] 区间中的相对位置（0~100）
    rsv: list[float | None] = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        ln = min(lows[i - n + 1 : i + 1])
        hn = max(highs[i - n + 1 : i + 1])
        # 区间高度为 0（一字板 / 停牌）时退化到中位 50，避免除零
        rsv[i] = 50 if hn == ln else (closes[i] - ln) / (hn - ln) * 100

    # 2) 第一段平滑 RSV → SK（通达信 SMA 写法，alpha = smooth_m / smooth_n）
    sk: list[float | None] = [None] * len(closes)
    prev_sk: float | None = None
    for i in range(len(closes)):
        r = rsv[i]
        if r is None:
            continue
        # 首个有效值直接做种子，之后按 SMA 递推
        prev_sk = r if prev_sk is None else (smooth_m * r + (smooth_n - smooth_m) * prev_sk) / smooth_n
        sk[i] = prev_sk

    # 3) 第二段平滑 SK → SD，与上面同一套递推，故 SD 比 SK 更滞后
    sd: list[float | None] = [None] * len(closes)
    prev_sd: float | None = None
    for i in range(len(closes)):
        s = sk[i]
        if s is None:
            continue
        prev_sd = s if prev_sd is None else (smooth_m * s + (smooth_n - smooth_m) * prev_sd) / smooth_n
        sd[i] = prev_sd

    return sk, sd


def macd_gold_cross_indices(dif: list[float], dea: list[float]) -> list[int]:
    """列出所有 MACD 金叉位置：前一刻 DIF <= DEA，本刻 DIF > DEA。

    Args:
        dif / dea: 等长的 MACD 双线序列。

    Returns:
        金叉发生的索引列表（升序），索引即该根 K 线的下标。
    """
    out: list[int] = []
    for i in range(1, len(dif)):
        if dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]:
            out.append(i)
    return out


def skdj_gold_cross_indices(sk: list[float | None], sd: list[float | None]) -> list[int]:
    """列出所有 SKDJ 金叉位置：前一刻 SK <= SD，本刻 SK > SD。

    Args:
        sk / sd: 等长的 SKDJ 双线序列，前 n-1 根可能为 None。

    Returns:
        金叉索引列表（升序）；任一端为 None 的位置直接跳过。
    """
    out: list[int] = []
    for i in range(1, len(sk)):
        a, b = sk[i - 1], sk[i]
        ap, bp = sd[i - 1], sd[i]
        if a is None or b is None or ap is None or bp is None:
            continue
        if a <= ap and b > bp:
            out.append(i)
    return out


def _valid_dual_cross_at(
    hi: int,
    dif: list[float],
    dea: list[float],
    sk: list[float | None],
    sd: list[float | None],
) -> bool:
    """校验 hi 这根 K 线能否作为「双金叉标记日」。

    条件（与前端 computeDualMacdSkdjMarkerIndices 对齐）：

        1. 当日 DIF > DEA（MACD 处于金叉上方）；
        2. 当日 SK > SD（SKDJ 处于金叉上方）；
        3. 当日不能恰好是 MACD 死叉日；
        4. 当日不能恰好是 SKDJ 死叉日。

    Args:
        hi: 待校验的 K 线索引。
        dif / dea: MACD 双线。
        sk / sd: SKDJ 双线，可能为 None。

    Returns:
        True 表示可以打双金叉标记。

    Note:
        条件 3 / 4 中的死叉方向判定（dif[hi] < dea[hi]、s < d）与条件 1 / 2
        已保证的 dif[hi] > dea[hi]、s > d 互斥，故这两条排除分支当前不会命中；
        保留它们是出于与前端实现的对称性，改动前请确认前端口径。
    """
    if not (dif[hi] > dea[hi]):
        return False
    s, d = sk[hi], sd[hi]
    if s is None or d is None or s <= d:
        return False
    if hi > 0 and dif[hi - 1] >= dea[hi - 1] and dif[hi] < dea[hi]:
        return False
    if hi > 0:
        sp, dp = sk[hi - 1], sd[hi - 1]
        if sp is not None and dp is not None and sp >= dp and s < d:
            return False
    return True


def find_latest_dual_cross_bar(
    dif: list[float],
    dea: list[float],
    sk: list[float | None],
    sd: list[float | None],
    window: int = 3,
) -> int | None:
    """
    最近一根有效 MACD+SKDJ 双金叉 K 线索引（bar 最大）。
    与前端 computeDualMacdSkdjMarkerIndices / 选股逻辑对齐。

    Args:
        dif / dea: MACD 双线。
        sk / sd: SKDJ 双线。
        window: 两个指标金叉日之间的容差（单位：根）。允许 MACD 与 SKDJ
            的金叉相差最多 window 根仍算共振，因为两者平滑参数不同，
            金叉几乎不会落在同一天。

    Returns:
        最近的双金叉 K 线索引，找不到返回 None。

    Note:
        样本少于 30 根时直接放弃：EMA / SKDJ 尚未收敛，此时的金叉不可信。
    """
    # 样本不足 30 根：EMA 与 SKDJ 都还没热身完，金叉是噪声，一律不认
    if len(dif) < 30:
        return None
    macd_g = macd_gold_cross_indices(dif, dea)
    skdj_g = skdj_gold_cross_indices(sk, sd)
    if not macd_g or not skdj_g:
        return None

    # 双指针滑窗：macd_g 与 skdj_g 都是升序，故 j 只需单向前进，整体 O(n)
    best_hi = -1
    j = 0
    for m in macd_g:
        # j 滑过所有「早于容差窗」的 SKDJ 金叉，停在第一个可能与 m 共振的位置
        while j < len(skdj_g) and skdj_g[j] < m - window:
            j += 1
        k = j
        # 遍历落在 [m-window, m+window] 内的 SKDJ 金叉
        while k < len(skdj_g) and skdj_g[k] <= m + window:
            # 标记日取两者中较晚的那根：共振直到那天才算成立
            hi = max(m, skdj_g[k])
            if hi > best_hi and _valid_dual_cross_at(hi, dif, dea, sk, sd):
                best_hi = hi
            k += 1
    return best_hi if best_hi >= 0 else None


def calculate_macd_df(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """向量化 MACD（DataFrame 版），供背驰检测等场景使用。

    Args:
        df: 至少含 close 列的 K 线表。
        fast / slow / signal: 周期参数，默认 12 / 26 / 9。

    Returns:
        df 的副本，新增 dif / dea / bar 三列（bar 为 (dif - dea) * 2，
        即国内常用 MACD 柱口径）；不修改入参。

    Note:
        这里用 pandas 的 ewm(adjust=False)，与上面的 ema_series 同一口径，
        但两者服务不同场景：本函数要的是列式结果，list 版要的是与前端逐值对齐。
    """
    out = df.copy()
    ema_fast = out["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = out["close"].ewm(span=slow, adjust=False).mean()
    out["dif"] = ema_fast - ema_slow
    out["dea"] = out["dif"].ewm(span=signal, adjust=False).mean()
    out["bar"] = (out["dif"] - out["dea"]) * 2
    return out
