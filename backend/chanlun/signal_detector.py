"""
买卖点判定模块 — 缠论链路的最下游。

链路位置::

    原始K线 → 包含处理 → 分型 → 笔 → 线段 → 中枢 → **买卖点 / 支撑阻力**

本模块消费上游已算好的结构（笔 / 线段 / 中枢），输出：

- 六类买卖点：一买、二买、三买（下跌侧）与一卖、二卖、三卖（上涨侧）；
- 当前走势类型（``detect_trend``）；
- 支撑位 / 阻力位（``detect_support_resistance``）。

Note:
    背驰判定用的是「(high-low) / 时间秒数」的**简化力度近似**，并非标准的
    MACD 面积背驰法。详见 ``_detect_1st_buy`` 的 Note。
"""
# from typing import Optional, Literal
# from datetime import datetime
from .elements import Bi, XianSegment, Zhongshu, BuySellPoint, SupportResistanceLevel


class SignalDetector:
    """
    三类买卖点判定:

    一买(1st Buy): 下跌趋势背驰点 → 最后一个下跌段终点，价格新低但力度不创新低
    二买(2nd Buy): 一买后回调低点（不破一买前低）
    三买(3rd Buy): 突破中枢后回踩，不跌入中枢

    一卖/二卖/三卖: 对称逻辑（上涨趋势）
    """

    def __init__(self, bis: list[Bi],
                 segments: list[XianSegment],
                 zhongshus: list[Zhongshu],
                 level: str = "daily"):
        """
        Args:
            bis:       笔序列（一买/一卖用线段，二买/二卖与三买/三卖用笔）。
            segments:  线段序列（含 direction / high / low / start / end）。
            zhongshus: 中枢序列（三买 / 三卖的判定基准）。
            level:     级别标签，如 "daily" / "30min"，原样写入信号。
        """
        self.bis = bis
        self.segments = segments
        self.zhongshus = zhongshus
        self.level = level

    def detect_all(self) -> list[BuySellPoint]:
        """
        一次性检测所有买卖点并按时间升序返回。

        Returns:
            一买 / 二买 / 三买 / 一卖 / 二卖 / 三卖 的混合列表，按 ``datetime`` 升序。

        Note:
            二买依赖一买的结果（要在「一买之后的回调低点」里找），
            二卖同理依赖一卖，因此这两步必须先算出 first_buys / first_sells 再传入。
        """
        signals = []
        # 先算一买：二买需要以一买的位置为参照
        first_buys = self._detect_1st_buy()
        signals.extend(first_buys)
        signals.extend(self._detect_2nd_buy(first_buys))
        signals.extend(self._detect_3rd_buy())
        # 同理：二卖需要以一卖的位置为参照
        first_sells = self._detect_1st_sell()
        signals.extend(first_sells)
        signals.extend(self._detect_2nd_sell(first_sells))
        signals.extend(self._detect_3rd_sell())
        # 六类信号分开扫描，最后统一按时间归并
        return sorted(signals, key=lambda s: s.datetime)

    # ── 下跌买卖点 ──────────────────────────────────────────────

    def _detect_1st_buy(self) -> list[BuySellPoint]:
        """
        一买（第一类买点）: 下跌趋势的背驰点。

        判定条件（两条同时成立）：
        1. 后一个下跌线段的低点 ``curr.low`` 低于前一个下跌线段的低点 ``prev.low``
           —— 即价格仍在创新低；
        2. 后一段的力度 ``curr_power`` 小于前一段力度 ``prev_power`` 的 **80%**
           —— 即下跌动能显著衰竭，这就是「背驰」。

        Returns:
            一买信号列表（序列中每个满足背驰的下跌段至多产生一个信号）。

        Note:
            **力度是简化近似**，公式为 ``(high - low) / 时间秒数``，含义是
            「单位时间内的价格波动幅度」。它与标准缠论背驰判定有两点重要差异：

            - 标准做法用**同向段 MACD 柱面积**比较，且要区分段内是否包含
              更大级别的中枢；这里只用振幅 / 时长，对「窄幅长时间阴跌」和
              「急跌」的区分能力较弱；
            - 该指标受**时间单位**影响很大：日线与分钟线的秒数差几个数量级，
              其绝对值的可比性只存在于同一级别内部，跨级别不可直接比较。

            另外 ``prev_power`` 可能为 0（段内 high == low 的极端数据），
            此时 ``curr_power / prev_power`` 会抛 ZeroDivisionError。
        """
        signals = []
        # 只看下跌段，且按时间顺序两两比较相邻的两个下跌段
        down_segments = [s for s in self.segments if s.direction == "down"]
        if len(down_segments) < 2:
            return signals

        for i in range(1, len(down_segments)):
            prev = down_segments[i - 1]
            curr = down_segments[i]

            # 价格创新低但力度（可用高度/时间比近似）减弱
            if curr.low < prev.low:
                # max(1, ...) 是为了避免时间跨度为 0（起止同一时刻）时除零
                prev_power = (prev.high - prev.low) / max(1, (prev.end - prev.start).total_seconds())
                curr_power = (curr.high - curr.low) / max(1, (curr.end - curr.start).total_seconds())

                if curr_power < prev_power * 0.8:  # 力度减弱20%以上
                    signals.append(BuySellPoint(
                        type="一买",
                        level=self.level,
                        price=float(curr.low),
                        datetime=curr.end,
                        # 背驰越明显（力度比越小）confidence 越高，
                        # 基线 0.5，上限 1.0。此处 curr_power/prev_power ∈ (0, 0.8)，
                        # 故取值区间约为 (0.70, 1.00]
                        confidence=round(min(1.0, abs(1 - curr_power / prev_power) + 0.5), 2),
                        # 止损设在一买低点下方 3%
                        stop_loss=float(curr.low * 0.97),
                        description=f"背驰一买: 当前段力度{abs(curr_power):.2f} < 前段力度{abs(prev_power):.2f}"
                    ))
        return signals

    def _detect_2nd_buy(self, first_buys: list[BuySellPoint]) -> list[BuySellPoint]:
        """
        二买（第二类买点）: 一买之后的回调低点（不破一买点）。

        判定条件：
        1. 遍历所有**向下笔**，其终点时间 ``b.end`` 晚于一买时间（即回调发生在一买之后）；
        2. 该回调低点 ``b.low`` 不低于一买价 ``fb.price`` 的 **97%**（含 3% 容差）；
        3. 取**第一个**满足条件的回调（``break``），即离一买最近的那次回踩。

        Args:
            first_buys: ``_detect_1st_buy`` 的产出，作为回调的参照点。

        Returns:
            二买信号列表（每个一买至多产生一个二买）。

        Note:
            条件 1 用的是**结束时间**在下笔之后，而不是「下笔起始于一买之后」——
            当下笔起点早于一买、但终点落在一买之后时同样算数。

            容差是**单向放宽**的：允许回调低点比一买低点最多再低 3%，
            即 ``b.low >= fb.price * 0.97``。这与二卖的 ``b.high <= fs.price * 1.03``
            在数值上对称（都是 3%），但注意一买/一卖的 confidence 计算里 ``abs()``
            并不改变结果（因为 ``curr_power/prev_power < 0.8`` 恒为正），
            属冗余写法而非错误。
        """
        signals = []
        if not first_buys or not self.bis:
            return signals

        for fb in first_buys:
            # 在一买之后结束的下探笔
            after_first = [b for b in self.bis
                          if b.direction == "down"
                          and b.end > fb.datetime]
            for b in after_first:
                # 回调低点不破一买点（含 3% 向下容差）
                if b.low >= fb.price * 0.97:
                    signals.append(BuySellPoint(
                        type="二买",
                        level=self.level,
                        price=float(b.low),
                        datetime=b.end,
                        # 二买置信度固定为 0.70（低于一买），因为它是次一级确认
                        confidence=0.70,
                        # 止损取「回调低点 -3%」与「一买价 -5%」中更靠下的一个，
                        # 即给二买留出一买所没有的额外缓冲
                        stop_loss=float(min(b.low * 0.97, fb.price * 0.95)),
                        description=f"回调二买: 回踩{b.low:.2f}不破一买{fb.price:.2f}"
                    ))
                    break
        return signals

    def _detect_3rd_buy(self) -> list[BuySellPoint]:
        """
        三买（第三类买点）: 向上笔突破某中枢后，回踩低点不跌回该中枢。

        判定条件（三步）：
        1. 存在一个向上笔 ``break_up``，其终点晚于中枢结束时间（``b.end > zs.end``，
           即突破发生在该中枢形成之后）且高点突破中枢上沿（``b.high > zs.range_high``）；
        2. 突破之后紧接着出现一个向下笔（``b.start > break_up.end`` 中的第一个）作为「回踩」；
        3. 回踩低点 ``retest.low`` **严格高于**中枢上沿 ``zs.range_high`` —— 即没有跌回中枢内。

        Returns:
            三买信号列表（至多一个：命中或 continue 后，循环末尾都会 break）。

        Note:
            这里遍历的是**倒序**的中枢（``reversed``），即优先用「最近被向上笔确认
            突破」的那个中枢，而不是简单取最后一个中枢——最后一个中枢可能压根
            还没被突破。

            置信度固定 0.80；止损设在中枢下沿（跌回中枢即证伪），
            止盈设在突破高点上方 5%。
        """
        signals = []
        if not self.zhongshus or not self.bis:
            return signals

        # 倒序：从最近的中枢开始找，命中即止
        for zs in reversed(self.zhongshus):
            # 找向上笔是否突破本中枢
            break_up = None
            for b in self.bis:
                if b.direction == "up" and b.end > zs.end and b.high > zs.range_high:
                    break_up = b
                    break

            # 本中枢从未被向上笔突破 → 换更早的中枢继续找
            if not break_up:
                continue

            # 找突破后紧接着的下探笔（按笔序列顺序取第一个起点晚于突破终点的下笔）
            retest_candidates = [
                b for b in self.bis
                if b.direction == "down" and b.start > break_up.end
            ]
            # 突破后还没出现回调 → 三买尚未成立，换更早的中枢继续找
            if not retest_candidates:
                continue

            retest = retest_candidates[0]
            # 回踩低点不跌入中枢上沿 = 三买成立（严格 >，贴着上沿不算）
            if retest.low > zs.range_high:
                signals.append(BuySellPoint(
                    type="三买",
                    level=self.level,
                    price=float(retest.low),
                    datetime=retest.end,
                    confidence=0.80,
                    # 止损放在中枢下沿：一旦跌回中枢内部即判定突破失败
                    stop_loss=float(zs.range_low),
                    # 止盈放在突破高点上方 5%
                    take_profit=float(break_up.high * 1.05),
                    description=f"三买: 回踩{retest.low:.2f}不破中枢{zs.range_high:.2f}"
                ))
            # 只取最近的一个三买（无论是否命中，都不再回溯更早的中枢）
            break

        return signals

    # ── 上涨买卖点 ──────────────────────────────────────────────

    def _detect_1st_sell(self) -> list[BuySellPoint]:
        """
        一卖（第一类卖点）: 上涨趋势的背驰点，与一买完全对称。

        判定条件：
        1. 后一个上涨线段的高点创出新高（``curr.high > prev.high``）；
        2. 后一段力度不足前一段的 **80%**（``curr_power < prev_power * 0.8``）。

        Returns:
            一卖信号列表。

        Note:
            力度同样用 ``(high - low) / 时间秒数`` 近似，局限与 ``_detect_1st_buy``
            一致；``prev_power`` 为 0 时会除零。止损放在卖点上方 3%（对空头而言）。
        """
        signals = []
        up_segments = [s for s in self.segments if s.direction == "up"]
        if len(up_segments) < 2:
            return signals

        for i in range(1, len(up_segments)):
            prev = up_segments[i - 1]
            curr = up_segments[i]

            # 价格创新高但力度减弱 → 上涨背驰
            if curr.high > prev.high:
                prev_power = (prev.high - prev.low) / max(1, (prev.end - prev.start).total_seconds())
                curr_power = (curr.high - curr.low) / max(1, (curr.end - curr.start).total_seconds())

                if curr_power < prev_power * 0.8:
                    signals.append(BuySellPoint(
                        type="一卖",
                        level=self.level,
                        price=float(curr.high),
                        datetime=curr.end,
                        confidence=round(min(1.0, abs(1 - curr_power / prev_power) + 0.5), 2),
                        # 对空头而言的止损：卖点上方 3%
                        stop_loss=float(curr.high * 1.03),
                        description=f"背驰一卖: 当前段力度{abs(curr_power):.2f} < 前段力度{abs(prev_power):.2f}"
                    ))
        return signals

    def _detect_2nd_sell(self, first_sells: list[BuySellPoint]) -> list[BuySellPoint]:
        """
        二卖（第二类卖点）: 一卖之后的反弹高点（不破一卖前高），与二买对称。

        判定条件：
        1. 遍历所有**向上笔**，其终点时间晚于一卖时间；
        2. 反弹高点 ``b.high`` 不超过一卖价 ``fs.price`` 的 **103%**（含 3% 容差）；
        3. 取第一个满足条件的反弹（离一卖最近的那次）。

        Args:
            first_sells: ``_detect_1st_sell`` 的产出，作为反弹的参照点。

        Returns:
            二卖信号列表（每个一卖至多产生一个二卖）。

        Note:
            容差 1.03（+3%）与二买的 0.97（-3%）在数值上对称。
            置信度固定 0.65，是六类买卖点里最低的——二卖属于弱确认，
            因此这里不设止盈/止损。
        """
        signals = []
        if not first_sells or not self.bis:
            return signals

        for fs in first_sells:
            # 在一卖之后结束的上探笔
            after_first = [b for b in self.bis
                          if b.direction == "up"
                          and b.end > fs.datetime]
            for b in after_first:
                # 反弹高点不超过一卖点（向上放宽 3% 容差；注释原写 ±2%，实为 3%）
                if b.high <= fs.price * 1.03:
                    signals.append(BuySellPoint(
                        type="二卖",
                        level=self.level,
                        price=float(b.high),
                        datetime=b.end,
                        # 二卖置信度最低（0.65）：属于反弹弱确认
                        confidence=0.65,
                        description=f"二卖: 反弹{b.high:.2f}不破一卖{fs.price:.2f}"
                    ))
                    break
        return signals

    def _detect_3rd_sell(self) -> list[BuySellPoint]:
        """
        三卖（第三类卖点）: 向下笔跌破某中枢后，反弹高点回不到该中枢。

        判定条件（三步，与三买镜像）：
        1. 存在一个向下笔 ``break_down``，其终点晚于中枢结束时间（``b.end > zs.end``）
           且低点跌破中枢下沿（``b.low < zs.range_low``）；
        2. 跌破之后紧接着出现一个向上笔（``b.start > break_down.end`` 中的第一个）作为「反抽」；
        3. 反抽高点 ``retest.high`` **严格低于**中枢下沿 ``zs.range_low`` —— 即没能回到中枢内。

        Returns:
            三卖信号列表（至多一个：命中或 continue 后，循环末尾都会 break）。

        Note:
            同样倒序遍历中枢，优先使用「最近被向下笔确认跌破」的那个，
            而非简单取最后一个。置信度固定 0.75，略低于三买（0.80）。
        """
        signals = []
        if not self.zhongshus or not self.bis:
            return signals

        # 倒序：从最近的中枢开始找，命中即止
        for zs in reversed(self.zhongshus):
            # 找向下笔是否跌破本中枢
            break_down = None
            for b in self.bis:
                if b.direction == "down" and b.end > zs.end and b.low < zs.range_low:
                    break_down = b
                    break

            # 本中枢从未被向下笔跌破 → 换更早的中枢继续找
            if not break_down:
                continue

            # 找跌破后紧接着的上探笔（取第一个起点晚于跌破终点的上笔）
            retest_candidates = [
                b for b in self.bis
                if b.direction == "up" and b.start > break_down.end
            ]
            # 跌破后还没出现反抽 → 三卖尚未成立，换更早的中枢继续找
            if not retest_candidates:
                continue

            retest = retest_candidates[0]
            # 反抽高点回不到中枢下沿 = 三卖成立（严格 <，贴着下沿不算）
            if retest.high < zs.range_low:
                signals.append(BuySellPoint(
                    type="三卖",
                    level=self.level,
                    price=float(retest.high),
                    datetime=retest.end,
                    # 三卖置信度 0.75，略低于三买
                    confidence=0.75,
                    description=f"三卖: 反弹{retest.high:.2f}不破中枢{zs.range_low:.2f}"
                ))
            # 只取最近的一个三卖，不再回溯更早的中枢
            break

        return signals

    def detect_trend(self) -> str:
        """
        判断当前走势类型：上涨 / 下跌 / 盘整 / 未知。

        Returns:
            走势类型字符串。

        Note:
            判定顺序是**有意为之**且不可调换：

            1. 线段为空 → "未知"（没有任何结构可判）；
            2. ``len(zhongshus) > 1`` → 直接判 "盘整"（最优先的结构性证据）。
               因为缠论里两个以上中枢本身就意味着「多空反复争夺、区间反复重叠」，
               是比单看最近几段方向更强的信号，所以放在方向统计之前；
            3. 否则比较最近 3 条线段中 down / up 的数量，多者为走势方向；
            4. 数量相等 → "盘整"。

            注意第 3 步只看最近 3 条线段，窗口很窄，短周期数据下容易来回切换。
        """
        if not self.segments:
            return "未知"
        # 只统计最近 3 条线段的方向，窗口较窄
        recent = self.segments[-3:]
        downs = sum(1 for s in recent if s.direction == "down")
        ups = sum(1 for s in recent if s.direction == "up")

        # 出现 2 个及以上中枢 → 结构性盘整证据最强，优先于方向统计
        if len(self.zhongshus) > 1:
            return "盘整"
        if downs > ups:
            return "下跌"
        if ups > downs:
            return "上涨"
        return "盘整"

    def detect_support_resistance(self, klines: list, signals: list[BuySellPoint]) -> list[SupportResistanceLevel]:
        """
        计算支撑位和阻力位

        支撑位来源:
        - 中枢下沿 (resistance=支撑)
        - 笔低点 / 线段低点 (support)
        - 历史K线低点附近 (support)
        - 买卖点价位 (signal)

        阻力位来源:
        - 中枢上沿 (resistance)
        - 笔高点 / 线段高点 (resistance)
        - 历史K线高点附近 (resistance)

        Args:
            klines:  K线字典列表，每项含 date / high / low（引擎传入的是原始K线）。
            signals: 已检出的买卖点，其价位本身也是重要的支撑 / 阻力。

        Returns:
            去重后的支撑阻力位列表，按 ``strength`` 降序。

        Note:
            各来源的 strength 权重（可视为「重要程度」）：

            ==========  ======================  ========
            来源        说明                     strength
            ==========  ======================  ========
            zhongshu    中枢上下沿               0.85
            kline_high  线段高点 / 近期K线高点   0.75 / 0.50
            kline_low   线段低点 / 近期K线低点   0.75 / 0.50
            bi_high     向下笔高点              0.60
            bi_low      向上笔低点               0.60
            signal      买卖点价位              取该信号 confidence
            ==========  ======================  ========

            注：线段高低点复用了 ``kline_high`` / ``kline_low`` 两个 source 取值，
            因此无法通过 source 区分它到底来自线段还是近期K线。

            去重：按 ``f"{p:.2f}"`` **两位小数精确匹配**判等（分组标题注释写的是
            ±0.5%，与实际实现不符），同价位只保留 strength 更高的那条。
        """
        levels: list[SupportResistanceLevel] = []

        # ── 1. 中枢 ──────────────────────────────
        # 中枢是多空反复争夺的区域，其上下沿是最重要的支撑 / 阻力
        for zs in self.zhongshus:
            levels.append(SupportResistanceLevel(
                type="support",
                price=zs.range_low,
                source="zhongshu",
                related_id=zs.id,
                datetime=zs.start,
                strength=0.85
            ))
            levels.append(SupportResistanceLevel(
                type="resistance",
                price=zs.range_high,
                source="zhongshu",
                related_id=zs.id,
                datetime=zs.start,
                strength=0.85
            ))

        # ── 2. 笔 ───────────────────────────────
        # 只取最近10笔，太多反而干扰
        recent_bis = self.bis[-10:]
        for b in recent_bis:
            if b.direction == "down":
                levels.append(SupportResistanceLevel(
                    type="support",
                    price=b.low,
                    source="bi_low",
                    related_id=b.id,
                    datetime=b.end,
                    strength=0.6
                ))
            else:
                levels.append(SupportResistanceLevel(
                    type="resistance",
                    price=b.high,
                    source="bi_high",
                    related_id=b.id,
                    datetime=b.end,
                    strength=0.6
                ))

        # ── 3. 线段 ─────────────────────────────
        # 线段级别高于笔，取最近 6 段；source 复用 kline_high / kline_low
        recent_xians = self.segments[-6:]
        for x in recent_xians:
            if x.direction == "down":
                levels.append(SupportResistanceLevel(
                    type="support",
                    price=x.low,
                    source="kline_low",
                    related_id=x.id,
                    datetime=x.end,
                    strength=0.75
                ))
            else:
                levels.append(SupportResistanceLevel(
                    type="resistance",
                    price=x.high,
                    source="kline_high",
                    related_id=x.id,
                    datetime=x.end,
                    strength=0.75
                ))

        # ── 4. 买卖点 ────────────────────────────
        # 买卖点价位直接用其 confidence 当作 strength
        for sig in signals:
            if "买" in sig.type:
                levels.append(SupportResistanceLevel(
                    type="support",
                    price=sig.price,
                    source="signal",
                    related_id="",
                    datetime=sig.datetime,
                    strength=sig.confidence
                ))
            else:
                levels.append(SupportResistanceLevel(
                    type="resistance",
                    price=sig.price,
                    source="signal",
                    related_id="",
                    datetime=sig.datetime,
                    strength=sig.confidence
                ))

        # ── 5. 近期K线高低价 ─────────────────────
        if klines:
            recent_kl = klines[-20:]
            # 过滤掉取值为 0 / None 的异常项，避免把 0 当成极值
            lows = [k for k in recent_kl if k.get("low")]
            highs = [k for k in recent_kl if k.get("high")]
            if lows:
                min_kl = min(lows, key=lambda x: x.get("low", float("inf")))
                levels.append(SupportResistanceLevel(
                    type="support",
                    price=float(min_kl.get("low")),
                    source="kline_low",
                    related_id="",
                    datetime=min_kl.get("date") if isinstance(min_kl, dict) else min_kl.date,
                    strength=0.5
                ))
            if highs:
                max_kl = max(highs, key=lambda x: x.get("high", float("-inf")))
                levels.append(SupportResistanceLevel(
                    type="resistance",
                    price=float(max_kl.get("high")),
                    source="kline_high",
                    related_id="",
                    datetime=max_kl.get("date") if isinstance(max_kl, dict) else max_kl.date,
                    strength=0.5
                ))

        # ── 去重：相同价格 ±0.5% 内的只保留最强的 ─────
        deduped: list[SupportResistanceLevel] = []
        seen_prices: dict[str, int] = {}  # key -> index in deduped

        def price_key(p: float) -> str:
            """
            把价格量化成去重用的字符串 key。

            Note:
                用两位小数格式化，等价于「按 0.01 元精度分桶」，
                也就是**精确匹配**而非区间容错。上方分组注释写的 ±0.5%
                与本实现不符，若要真正的容差去重需要改这里。
            """
            return f"{p:.2f}"

        for lvl in levels:
            pk = price_key(lvl.price)
            if pk not in seen_prices:
                # 首次出现：记录 (key → deduped 下标) 并追加
                seen_prices[pk] = len(deduped)
                deduped.append(lvl)
            else:
                # 同价位重复：原地替换成 strength 更高的那条
                existing = deduped[seen_prices[pk]]
                if lvl.strength > existing.strength:
                    deduped[seen_prices[pk]] = lvl

        # 按强度降序返回
        return sorted(deduped, key=lambda x: -x.strength)
