"""
买卖点判定模块
"""
from typing import Optional, Literal
from datetime import datetime
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
        self.bis = bis
        self.segments = segments
        self.zhongshus = zhongshus
        self.level = level

    def detect_all(self) -> list[BuySellPoint]:
        """检测所有买卖点"""
        signals = []
        first_buys = self._detect_1st_buy()
        signals.extend(first_buys)
        signals.extend(self._detect_2nd_buy(first_buys))
        signals.extend(self._detect_3rd_buy())
        first_sells = self._detect_1st_sell()
        signals.extend(first_sells)
        signals.extend(self._detect_2nd_sell(first_sells))
        signals.extend(self._detect_3rd_sell())
        return sorted(signals, key=lambda s: s.datetime)

    # ── 下跌买卖点 ──────────────────────────────────────────────

    def _detect_1st_buy(self) -> list[BuySellPoint]:
        """
        一买: 下跌趋势的背驰点
        条件: 连续2个以上向下段，后一段力度 < 前一段力度（背驰）
        """
        signals = []
        down_segments = [s for s in self.segments if s.direction == "down"]
        if len(down_segments) < 2:
            return signals

        for i in range(1, len(down_segments)):
            prev = down_segments[i - 1]
            curr = down_segments[i]

            # 价格创新低但力度（可用高度/时间比近似）减弱
            if curr.low < prev.low:
                prev_power = (prev.high - prev.low) / max(1, (prev.end - prev.start).total_seconds())
                curr_power = (curr.high - curr.low) / max(1, (curr.end - curr.start).total_seconds())

                if curr_power < prev_power * 0.8:  # 力度减弱20%以上
                    signals.append(BuySellPoint(
                        type="一买",
                        level=self.level,
                        price=float(curr.low),
                        datetime=curr.end,
                        confidence=round(min(1.0, abs(1 - curr_power / prev_power) + 0.5), 2),
                        stop_loss=float(curr.low * 0.97),
                        description=f"背驰一买: 当前段力度{abs(curr_power):.2f} < 前段力度{abs(prev_power):.2f}"
                    ))
        return signals

    def _detect_2nd_buy(self, first_buys: list[BuySellPoint]) -> list[BuySellPoint]:
        """
        二买: 一买后的回调低点（不破一买点）
        注意：回调低点须出现在一买之后（下笔终点 > 一买点时间），
        而不是起始于一买之后——当下笔起点早于一买但终点落在一买后时
        同样满足条件。
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
                # 回调低点不破一买点（含容差 ±3%）
                if b.low >= fb.price * 0.97:
                    signals.append(BuySellPoint(
                        type="二买",
                        level=self.level,
                        price=float(b.low),
                        datetime=b.end,
                        confidence=0.70,
                        stop_loss=float(min(b.low * 0.97, fb.price * 0.95)),
                        description=f"回调二买: 回踩{b.low:.2f}不破一买{fb.price:.2f}"
                    ))
                    break
        return signals

    def _detect_3rd_buy(self) -> list[BuySellPoint]:
        """
        三买: 向上笔突破某中枢后，回踩低点不跌入该中枢上沿。
        用最近被向上笔确认突破的中枢，而非简单取最后一个。
        """
        signals = []
        if not self.zhongshus or not self.bis:
            return signals

        for zs in reversed(self.zhongshus):
            # 找向上笔是否突破本中枢
            break_up = None
            for b in self.bis:
                if b.direction == "up" and b.end > zs.end and b.high > zs.range_high:
                    break_up = b
                    break

            if not break_up:
                continue

            # 找突破后紧接着的下探笔
            retest_candidates = [
                b for b in self.bis
                if b.direction == "down" and b.start > break_up.end
            ]
            if not retest_candidates:
                continue

            retest = retest_candidates[0]
            # 回踩低点不跌入中枢上沿 = 三买成立
            if retest.low > zs.range_high:
                signals.append(BuySellPoint(
                    type="三买",
                    level=self.level,
                    price=float(retest.low),
                    datetime=retest.end,
                    confidence=0.80,
                    stop_loss=float(zs.range_low),
                    take_profit=float(break_up.high * 1.05),
                    description=f"三买: 回踩{retest.low:.2f}不破中枢{zs.range_high:.2f}"
                ))
            break  # 只取最近的一个三买

        return signals

    # ── 上涨买卖点 ──────────────────────────────────────────────

    def _detect_1st_sell(self) -> list[BuySellPoint]:
        """一卖: 上涨趋势背驰点"""
        signals = []
        up_segments = [s for s in self.segments if s.direction == "up"]
        if len(up_segments) < 2:
            return signals

        for i in range(1, len(up_segments)):
            prev = up_segments[i - 1]
            curr = up_segments[i]

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
                        stop_loss=float(curr.high * 1.03),
                        description=f"背驰一卖: 当前段力度{abs(curr_power):.2f} < 前段力度{abs(prev_power):.2f}"
                    ))
        return signals

    def _detect_2nd_sell(self, first_sells: list[BuySellPoint]) -> list[BuySellPoint]:
        """
        二卖: 一卖后反弹高点（不破一卖前高）
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
                # 反弹高点不超过一卖点（含 ±2% 容差）
                if b.high <= fs.price * 1.03:
                    signals.append(BuySellPoint(
                        type="二卖",
                        level=self.level,
                        price=float(b.high),
                        datetime=b.end,
                        confidence=0.65,
                        description=f"二卖: 反弹{b.high:.2f}不破一卖{fs.price:.2f}"
                    ))
                    break
        return signals

    def _detect_3rd_sell(self) -> list[BuySellPoint]:
        """
        三卖: 向下笔跌破某中枢后，反弹高点不突破该中枢下沿。
        用最近被向下笔确认突破的中枢。
        """
        signals = []
        if not self.zhongshus or not self.bis:
            return signals

        for zs in reversed(self.zhongshus):
            # 找向下笔是否跌破本中枢
            break_down = None
            for b in self.bis:
                if b.direction == "down" and b.end > zs.end and b.low < zs.range_low:
                    break_down = b
                    break

            if not break_down:
                continue

            # 找跌破后紧接着的上探笔
            retest_candidates = [
                b for b in self.bis
                if b.direction == "up" and b.start > break_down.end
            ]
            if not retest_candidates:
                continue

            retest = retest_candidates[0]
            if retest.high < zs.range_low:
                signals.append(BuySellPoint(
                    type="三卖",
                    level=self.level,
                    price=float(retest.high),
                    datetime=retest.end,
                    confidence=0.75,
                    description=f"三卖: 反弹{retest.high:.2f}不破中枢{zs.range_low:.2f}"
                ))
            break

        return signals

    def detect_trend(self) -> str:
        """判断当前走势类型。

        中枢数量不再作为判据 —— chan.py 口径下中枢数量天然偏多，
        改用最近若干线段的高低点是否逐级抬高/降低来判定趋势。
        """
        if not self.segments:
            return "未知"
        recent = self.segments[-3:]
        if len(recent) >= 2:
            highs = [s.high for s in recent]
            lows = [s.low for s in recent]
            if all(highs[i] > highs[i - 1] and lows[i] > lows[i - 1]
                   for i in range(1, len(recent))):
                return "上涨"
            if all(highs[i] < highs[i - 1] and lows[i] < lows[i - 1]
                   for i in range(1, len(recent))):
                return "下跌"

        downs = sum(1 for s in recent if s.direction == "down")
        ups = sum(1 for s in recent if s.direction == "up")
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
        """
        levels: list[SupportResistanceLevel] = []

        # ── 1. 中枢 ──────────────────────────────
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
            return f"{p:.2f}"

        for lvl in levels:
            pk = price_key(lvl.price)
            if pk not in seen_prices:
                seen_prices[pk] = len(deduped)
                deduped.append(lvl)
            else:
                existing = deduped[seen_prices[pk]]
                if lvl.strength > existing.strength:
                    deduped[seen_prices[pk]] = lvl

        # 按强度降序返回
        return sorted(deduped, key=lambda x: -x.strength)
