"""
分型检测器 — 缠论链路的第一环。

链路位置::

    原始K线 → **包含处理(合并) → 分型** → 笔 → 线段 → 中枢 → 买卖点/支撑阻力

本模块做两件事：

1. ``_process_inclusion``：按缠论规则消除K线的**包含关系**，把若干根存在包含
   的K线合并成一根，得到「合并后序列」。后续所有结构（分型、笔、线段、中枢）
   都是在**合并后序列**上计算的，而不是原始K线上。
2. ``detect``：在合并后序列上按**严格三K线**规则找出顶分型与底分型。

Note:
    ``Fenxing.index`` 是**合并后序列**的下标，不是原始K线的下标。
    想定位原始K线必须用 ``date`` 字段（见 ``bi_detector._count_klines_between``）。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np

from .elements import KLine, MergedKLine, IncludedRaw


@dataclass
class Fenxing:
    """
    一个顶分型或底分型（三根相邻K线构成的价格转折点）。

    Note:
        ``index`` 是**包含合并后序列**中的下标，**不是**原始K线的位置。
        合并会让序列变短，因此不能用它在原始 DataFrame 里直接取行。
    """
    # 分型中间那根K线的日期（用于与原始K线对齐）
    date: datetime
    # 分型类型："top"=顶分型，"bottom"=底分型
    type: Literal["top", "bottom"]
    # 分型中间K线的最高价
    high: float
    # 分型中间K线的最低价
    low: float
    # 该分型在**合并后序列**中的下标（易踩坑，见类文档 Note）
    index: int


class FenxingDetector:
    """
    缠论分型检测器。

    构造时先做一遍包含处理：

    - **向上合并**：新K线的高、低都不低于前一根（走势向上）时，
      取两者的 ``max``，即保留更高的高、更高的低；
    - **向下合并**：新K线的高、低都不高于前一根（走势向下）时，
      取两者的 ``min``，即保留更低的高、更低的低。

    这样可以在不丢失有效波动的前提下压缩噪音，保留后续笔检测真正需要的
    「摆动形态」。
    """
    def __init__(self, klines: list[KLine]):
        """
        Args:
            klines: 原始K线序列（需按时间升序），元素为 :class:`KLine`。
                构造完成后：

                - ``self.raw_klines`` 仍是**原始**序列；
                - ``self.merged_klines`` 是保留包含关系的合并K线对象（供前端画合并区间）。

        Note:
        TODO 修改注释？？？？？？？？
            三个属性各有用途，别混用：分型 / 笔跑在 ``klines`` 与 ``mer`` 上
            （二者等长、下标一一对应），而定位原始K线要用 ``mer[i].lst`` 里
            元素的 ``idx``。
        """
        self.raw_klines: list[KLine] = list(klines)
        self.merged_klines: list[MergedKLine] = []
        self._process_inclusion()

    def _process_inclusion(self):
        """
        包含关系处理（缠论标准步骤），原地把 ``self.klines`` 换成合并后序列。

        判定「被包含」：两根K线中，一根的最高价不高于另一根的最高价
        且最低价不低于另一根的最低价，即价格区间互相套住 → 只能保留一根。

        合并方向由 ``merge_direction`` 决定：先看**已有合并序列的前两根**
        走势，若它们不构成明确的上升 / 下降关系，再退而看**当前K线相对前一根**。

        Note:
            合并后序列长度 ≤ 原始K线数量，因此所有基于分型的下标都必须
            重新编号，切勿与原始下标混用。
        """
        src = self.raw_klines
        # 空序列直接返回：下面 out_* 都以 src[0] 初始化，空列表会越界
        if not src:
            return

        # 合并序列以第 0 根为基准；之后每根K线要么「追加」为新根，要么「并入」末尾
        first = src[0]
        out_high = [first.high]
        out_low = [first.low]
        out_open = [first.open]
        out_close = [first.close]
        out_date = [first.date]
        out_vol = [float(first.volume or 0)]
        # 每根合并K线覆盖的原始K线分组 —— 这就是老实现丢掉的「包含关系」
        groups: list[list[KLine]] = [[first]]
        # 合并方向：首根先记 up，真正发生包含时才会被 merge_direction 覆盖
        out_dir = ["up"]

        def merge_direction(prev_h, prev_l, before_h, before_l, cur_h, cur_l) -> Literal["up", "down"]:
            """
            确定包含合并的方向（"up" 取 max / "down" 取 min）。

            Args:
                prev_h / prev_l:     合并序列当前最后一根（即「前一根」）的 high/low
                before_h / before_l: 再往前一根的 high/low（序列不足 2 根时退化为 prev）
                cur_h / cur_l:       待处理的当前K线 high/low

            Returns:
                "up" 表示按向上方向合并（取 max），"down" 表示按向下方向合并（取 min）。

            Note:
                判定有**先后两级**，顺序不能颠倒：

                1. 先看「前一根 vs 再前一根」——若二者构成严格上升（高、低都不更低）
                   则判 up，严格下降则判 down。因为缠论规定包含处理的方向必须
                   **延续既有走势**，而走势由已经确定的相邻两根来定义。
                2. 只有当这两根不构成明确关系（例如高点更高但低点更低这种交叉、
                   或者完全相等、或者序列还不足 2 根）时，才退回第二级，
                   用「当前根 vs 前一根」来判断方向。

                若把两级顺序反过来，遇到交叉型K线就会得出与标准不同的合并结果。
            """
            # 第一级：用合并序列里已确定的前两根定方向
            if len(out_high) >= 2:
                if prev_h >= before_h and prev_l >= before_l:
                    return "up"
                if prev_h <= before_h and prev_l <= before_l:
                    return "down"
            # 第二级：前两根无明确方向时，改用当前根相对前一根来定
            return "up" if cur_h >= prev_h and cur_l >= prev_l else "down"

        for i in range(1, len(src)):
            k = src[i]
            cur_h, cur_l = k.high, k.low
            # 始终与「合并序列的末尾一根」比较，而不是原始前一根
            prev_h, prev_l = out_high[-1], out_low[-1]
            # 包含判定：区间互相套住即算包含，两个方向都要判：
            #   prev_l<=cur_l 且 prev_h>=cur_h → 当前根被前一根包住
            #   cur_l<=prev_l 且 cur_h>=prev_h → 前一根被当前根包住
            contained = (
                (prev_l <= cur_l and prev_h >= cur_h)
                or (cur_l <= prev_l and cur_h >= prev_h)
            )
            if not contained:
                # 无包含 → 原样追加为新的一根合并K线（自成一组）
                out_high.append(cur_h)
                out_low.append(cur_l)
                out_open.append(k.open)
                out_close.append(k.close)
                out_date.append(k.date)
                out_vol.append(float(k.volume or 0))
                groups.append([k])
                out_dir.append("up")
                continue

            # 处理K线的包含关系
            # 取「再前一根」用于判定走势方向；不足 2 根时退化为 prev，等价于只走第二级判定
            before_h = out_high[-2] if len(out_high) >= 2 else prev_h
            before_l = out_low[-2] if len(out_low) >= 2 else prev_l
            direction = merge_direction(prev_h, prev_l, before_h, before_l, cur_h, cur_l)
            # prev_contains=True 表示「前一根包住当前根」；False 表示「当前根包住前一根」
            prev_contains = prev_l <= cur_l and prev_h >= cur_h
            if direction == "up":
                # 向上合并取 max：高者更高、低者更高 —— 保留向上摆动的外沿
                out_high[-1] = max(prev_h, cur_h)
                out_low[-1] = max(prev_l, cur_l)
            else:
                # 向下合并取 min：高者更低、低者更低 —— 保留向下摆动的外沿
                out_high[-1] = min(prev_h, cur_h)
                out_low[-1] = min(prev_l, cur_l)
            # 收盘价取最新一根：合并K线的收盘 = 这段区间的最后一根收盘
            out_close[-1] = k.close
            # 成交量累加：合并后的K线代表多根原始K线的成交总量
            out_vol[-1] += float(k.volume or 0)
            # 记录本次合并的方向，供前端按 up/down 画红/绿框
            out_dir[-1] = direction
            # 关键：把这根K线并入当前分组，保留「谁被谁包住了」的信息
            groups[-1].append(k)
            if not prev_contains:
                # 只有「前一根包住当前根」时才把日期推进到当前K线。
                # 反过来（当前根包住前一根）时，前一根的日期才是这段合并K线的
                # 起点，继续沿用原日期，避免分型日期被无意义地往后挪。
                out_date[-1] = k.date

        n = len(out_high)
        for i in range(n):
            include: list[IncludedRaw] = list()
            for j in range(len(groups[i])):
                include.append(IncludedRaw(date=groups[i][j].date, high=groups[i][j].high, low=groups[i][j].low))
            self.merged_klines.append(MergedKLine(start=groups[i][0].date,
                                                  end=groups[i][-1].date,
                                                  start_idx=-1,
                                                  end_idx=-1,
                                                  high=out_high[i],
                                                  low=out_low[i],
                                                  dir=out_dir[i], # type: ignore
                                                  count=len(groups[i]),
                                                  fx=None,
                                                  included=include))

    def detect(self) -> list[Fenxing]:
        """
        在**合并后序列**上检测严格三K线分型。

        判定规则：

        - **顶分型**：中间K线的 high 与 low **同时**大于左右两根；
        - **底分型**：中间K线的 high 与 low **同时**小于左右两根。

        Returns:
            按 ``index`` 升序排列的分型列表（顶、底混合）。

        Note:
            返回的 ``Fenxing.index`` 是**合并后序列**的下标，不是原始K线下标。
            合并会让序列变短，用它去原始 DataFrame 取行会错位。
        """
        kl = self.merged_klines
        n = len(kl)
        if n < 3:
            return []

        highs = np.array([k.high for k in kl], dtype=float)
        lows = np.array([k.low for k in kl], dtype=float)
        dates = [k.start for k in kl]
        fenxings: list[Fenxing] = []

        # 三个错位切片构成「滑动三元组」：(前一根, 中间根, 后一根)，全程向量化，
        # 长度均为 n-2，因此 mask 的第 i 项对应合并序列的第 i+1 根
        prev_h = highs[:-2]
        prev_l = lows[:-2]
        mid_h = highs[1:-1]
        mid_l = lows[1:-1]
        next_h = highs[2:]
        next_l = lows[2:]

        # 顶分型：high 与 low 必须**同时**高于左右两根。
        # 为什么两个都要比？只比 high 会把「低点也在抬高的普通上涨K线」误判成顶；
        # 只比 low 则无法排除长上影的干扰。两者同时成立才说明中间那根完整覆盖了
        # 两侧的波动区间，是真正的转折点。这里用严格 >（不含等号），
        # 相等时不构成分型，避免出现连续等高导致的重复分型。
        top_mask = (
            (mid_h > prev_h)
            & (mid_h > next_h)
            & (mid_l > prev_l)
            & (mid_l > next_l)
        )
        # 底分型：与顶分型对称，中间根的 high 与 low 同时低于左右两根
        bottom_mask = (
            (mid_h < prev_h)
            & (mid_h < next_h)
            & (mid_l < prev_l)
            & (mid_l < next_l)
        )

        # flatnonzero 直接拿到 True 的下标，省掉一轮 Python 循环 + 布尔判断
        for i in np.flatnonzero(top_mask):
            # +1 补回错位切片的偏移：mask 第 i 项 → 合并序列第 i+1 根
            idx = int(i) + 1
            # 回填分型：self.mer 与 self.klines 等长，可直接用同一 idx
            self.merged_klines[idx].fx = "top"
            fenxings.append(
                Fenxing(
                    date=dates[idx],
                    type="top",
                    high=float(mid_h[i]),
                    low=float(mid_l[i]),
                    index=idx,
                )
            )

        for i in np.flatnonzero(bottom_mask):
            # 同样 +1 还原到合并序列下标
            idx = int(i) + 1
            self.merged_klines[idx].fx = "bottom"
            fenxings.append(
                Fenxing(
                    date=dates[idx],
                    type="bottom",
                    high=float(mid_h[i]),
                    low=float(mid_l[i]),
                    index=idx,
                )
            )

        # 顶、底是分开扫描的两轮，需按序列位置重新归并成一个有序序列
        fenxings.sort(key=lambda f: f.index)
        return fenxings
