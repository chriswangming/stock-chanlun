"""
线段与中枢检测器。

链路位置::

    原始K线 → 包含处理 → 分型 → 笔 → **线段 → 中枢** → 买卖点/支撑阻力

本模块负责链路的第五、第六环：

1. ``detect_segments``：滑动窗口扫描笔序列，连续 3 笔若存在价格重叠即构成
   一条线段，并尝试向后**延伸**（后续同向且重叠的笔继续并入）；
2. ``detect_zhongshus``：滑动窗口扫描线段序列，连续 3 段若存在重叠区间即构成
   一个中枢，并尝试向后延伸。

另提供 ``get_zhongshu_for_price``，用于查询某个价格当前落在哪个中枢内。

Note:
    两个检测函数用的都是「3 个连续单元 + 重叠 + 向后延伸 + 跳跃推进」的
    同一套滑动窗口骨架，阅读时可以对照着看。
"""
from typing import Optional
from datetime import datetime
from .elements import Bi, XianSegment, Zhongshu


class SegmentDetector:
    """
    线段规则:
    - 3笔（或以上）重叠构成线段
    - 线段被更高一级别笔破坏则线段结束

    中枢规则:
    - 3个（或以上）连续同级别线段的重叠区域构成中枢
    """

    def __init__(self, bis: list[Bi]):
        """
        Args:
            bis: 已检测出的笔序列（须按时间升序）。
        """
        self.bis = bis

    def detect_segments(self, min_overlap_bis: int = 3, max_iterations: int = 10000) -> list[XianSegment]:
        """
        检测所有线段：连续 3 笔存在价格重叠即成段，并尽量向后延伸。

        Args:
            min_overlap_bis: 构成一条线段所需的最少重叠笔数（默认 3，缠论标准要求
                至少 3 笔，即「上下上」或「下上下」）。
            max_iterations: 外层循环迭代上限，纯防御性参数，防止极端数据下死循环。

        Returns:
            按时间顺序排列的线段列表。

        Note:
            推进方式：成立时 ``i += len(bi_ids)``，即整段跳到本线段用掉的笔之后，
            保证不同线段之间不会共用同一批笔；不成立时只 ``i += 1`` 逐笔滑动一格。

            延伸规则：只有**与首笔同向**且**与当前区间重叠**的后续笔才会被并入；
            方向不同的笔会终止延伸（本实现不处理「线段被破坏」后的重划分）。
        """
        if len(self.bis) < 3:
            return []

        segments: list[XianSegment] = []
        i = 0
        iterations = 0

        while i <= len(self.bis) - min_overlap_bis:
            # 防止死循环
            iterations += 1
            if iterations > max_iterations:
                break

            # 取连续3笔检查重叠
            group = self.bis[i:i + min_overlap_bis]
            if self._has_overlap(group):
                # 三笔有共同重叠区 → 线段成立，先记录基础区间，再尝试向后延伸
                start = group[0].start
                end = group[-1].end
                direction = group[0].direction
                high = max(b.high for b in group)
                low = min(b.low for b in group)
                start_price = group[0].start_price
                end_price = group[-1].end_price
                bi_ids = [b.id for b in group]

                # 尝试延伸
                extended = True
                extend_iterations = 0
                # 循环上限 1000：外层已有 max_iterations，这里再兜一层防止单段无限延伸
                while extended and extend_iterations < 1000:
                    extended = False
                    extend_iterations += 1
                    # 依据当前已并入的笔数量推进索引，避免重复读取同一根笔
                    next_idx = i + len(bi_ids)
                    if next_idx < len(self.bis):
                        next_b = self.bis[next_idx]
                        # 只并入「与首笔同向」的笔：方向一变就说明走势转折、本段结束
                        if next_b.direction == direction:
                            if self._overlaps(next_b, high, low):
                                # 并入后取并集，把线段覆盖范围扩到新的极值
                                high = max(high, next_b.high)
                                low = min(low, next_b.low)
                                bi_ids.append(next_b.id)
                                end = next_b.end
                                end_price = next_b.end_price
                                extended = True

                segments.append(XianSegment(
                    id=f"xian_{len(segments)+1}",
                    start=start,
                    end=end,
                    direction=direction,
                    high=high,
                    low=low,
                    start_price=start_price,
                    end_price=end_price,
                    bi_ids=bi_ids,
                    level=2))
                # 整段跳转：跳过本线段已消耗的所有笔，避免相邻线段重复用笔
                i += len(bi_ids)
            else:
                # 无重叠 → 窗口只滑动一格，从下一笔继续寻找
                i += 1

        return segments

    def _has_overlap(self, bis_group: list[Bi]) -> bool:
        """
        判断一组笔是否存在**共同**的价格重叠区间。

        Args:
            bis_group: 一组待判定的笔（通常 3 笔）。

        Returns:
            存在共同重叠区时返回 True。

        Note:
            几何含义：所有笔覆盖区间的**交集**为 ``[max(low), min(high)]``。
            用 ``min(high) > max(low)`` 判断：只有当「最保守的上沿」严格高于
            「最保守的下沿」时交集才非空。变量名 high/low 指的是交集的上下沿，
            不要误读成某一根笔的最高 / 最低价。这里用严格 ``>``，相切（=）不算重叠。
        """
        if len(bis_group) < 2:
            return False
        # 交集上沿 = 各笔 high 的最小值
        high = min(b.high for b in bis_group)
        # 交集下沿 = 各笔 low 的最大值
        low = max(b.low for b in bis_group)
        return high > low  # 有重叠区域

    def _overlaps(self, bi: Bi, existing_high: float, existing_low: float) -> bool:
        """
        判断单根笔是否与已有的价格区间 [existing_low, existing_high] 重叠。

        Note:
            用的是**开区间**重叠判定（两个严格不等号）：笔的高点必须高于区间下沿、
            低点必须低于区间上沿。因此「刚好贴住边」（bi.low == existing_high）
            不算重叠，中枢 / 线段延伸会在此处停止。
        """
        return bi.high > existing_low and bi.low < existing_high

    def detect_zhongshus(self, segments: list[XianSegment]) -> list[Zhongshu]:
        """
        检测中枢（滑动窗口算法）：
        遍历线段序列，每取得连续3段计算重叠区间：
        - 有重叠 → 构成中枢，尝试向后延伸（后续线段若与之重叠则并入）
        - 无重叠 → 跳过，继续寻找下一组
        相邻中枢之间必然间隔至少3个线段，不会重复。

        Args:
            segments: 线段序列（须按时间升序）。

        Returns:
            按时间顺序排列的中枢列表。

        Note:
            与直觉相反：中枢延伸时区间是**收窄**（取交集）而不是扩大（取并集）——
            ``range_high = min(range_high, nxt.high)``、``range_low = max(range_low, nxt.low)``。
            这与线段延伸取并集的做法正好相反，因为中枢的定义是「所有参与段共有的
            重叠区」，段越多共有区自然越小。极端情况下可能收窄到
            ``range_high <= range_low``，此时区间已退化为空集。
        """
        if len(segments) < 3:
            return []

        zhongshus: list[Zhongshu] = []
        i = 0

        while i <= len(segments) - 3:
            group = segments[i:i + 3]

            # 计算三段重叠区间
            range_high = min(s.high for s in group)
            range_low = max(s.low for s in group)

            if range_high > range_low:
                # 重叠 → 形成中枢，尝试向后延伸
                cur_start = group[0].start
                cur_end = group[-1].end
                xian_ids = [s.id for s in group]
                extend_idx = i + 3

                while extend_idx < len(segments):
                    nxt = segments[extend_idx]
                    # 新段与当前中枢重叠 → 并入；中枢区间取所有段的交叠（收窄），而非并集
                    if nxt.high > range_low and nxt.low < range_high:
                        # 取交集：上沿只降不升、下沿只升不降，中枢随延伸不断收窄
                        range_high = min(range_high, nxt.high)
                        range_low = max(range_low, nxt.low)
                        cur_end = nxt.end
                        xian_ids.append(nxt.id)
                        extend_idx += 1
                    else:
                        break

                zhongshus.append(Zhongshu(
                    id=f"zs_{len(zhongshus)+1}",
                    start=cur_start,
                    end=cur_end,
                    range_high=float(range_high),
                    range_low=float(range_low),
                    xian_ids=xian_ids,
                    level=group[0].level
                ))
                # 跳跃推进：extend_idx 指向第一个未并入的线段，
                # 保证相邻中枢不会共用线段（无重叠时则只滑动一格）
                i = extend_idx  # 跳到中枢结束后的第一个线段
            else:
                i += 1

        return zhongshus

    def get_zhongshu_for_price(self, zhongshus: list[Zhongshu],
                                 price: float) -> Optional[Zhongshu]:
        """
        找到给定价格所处的中枢。

        Args:
            zhongshus: 中枢序列（须按时间升序）。
            price:     待查询的价格。

        Returns:
            价格落在 [range_low, range_high] 内的中枢；多个命中时返回**最近**的一个
            （倒序遍历）；都不命中返回 None。

        Note:
            判定用的是**闭区间**（``<=``），价格正好压在中枢上下沿也算落在中枢内。
        """
        # 倒序：优先返回时间上最新的中枢（当下所处的位置）
        for zs in reversed(zhongshus):
            if zs.range_low <= price <= zs.range_high:
                return zs
        return None
