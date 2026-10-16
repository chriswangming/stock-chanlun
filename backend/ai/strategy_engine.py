"""
策略推荐引擎
"""
from typing import Optional

from ai.elements import AiSignal
from newchan.BuySellPoint.BS_Point import CBS_Point
from newchan.ZS.ZS import CZS


# newchan 的 CBS_Point 不带置信度（它的 features 里只有 bsp_bi_amp 这类结构量），
# 这里给所有买卖点一个中性基准，后续由 divergence / 中枢 / 趋势逐项加减。
# 若调用方自己在 CBS_Point.features 里塞了 'confidence'，则优先用调用方的。
DEFAULT_BSP_CONFIDENCE = 0.6

# CBS_Point.type 是 BSP_TYPE 列表，主类型取 value 首字符（'1'/'2'/'3'）
_CN_NUM = {"1": "一", "2": "二", "3": "三"}


def _bsp_labels(bsp: CBS_Point) -> list[str]:
    """
    把 CBS_Point 的类型列表翻译成中文标签，如 ``["一买"]`` / ``["二买", "三买"]``。

    Args:
        bsp: 买卖点

    Returns:
        list[str]: 去重后的中文标签，顺序保持 type 列表的顺序。

    Note:
        不是所有 CBS_Point 都只有一种类型 —— 同一根笔可以同时满足多类定义
        （例如1买的那笔同时也是2买），所以返回的是列表而不是单个字符串。
    """
    buy_sfx = "买" if bsp.is_buy else "卖"
    labels: list[str] = []
    for t in bsp.type:
        main = str(t.main_type())
        label = f"{_CN_NUM.get(main, main)}{buy_sfx}"
        if label not in labels:
            labels.append(label)
    return labels


def _bsp_has_main_type(bsp: CBS_Point, main: str) -> bool:
    """这笔买卖点是否命中某个主类型（'1'/'2'/'3'），二买就是 main='2'。"""
    return any(str(t.main_type()) == main for t in bsp.type)


def _bsp_idx(bsp: CBS_Point) -> int:
    """
    买卖点的时间先后比较键。

    Note:
        用 KLU 下标而不是 CTime：CTime 没有实现比较运算符（它只存 ts 字段），
        而 KLU idx 天然单调递增，且 CBS_Point.klu 就是 »其所在笔的尾端极值K线»。
    """
    idx = getattr(bsp.klu, "idx", -1)
    return idx if isinstance(idx, int) and idx >= 0 else 0


def _bsp_price(bsp: CBS_Point) -> float:
    """
    买卖点的触发价位。

    Note:
        沿用原 BuySellPoint 的口径：买点取低点、卖点取高点 ——
        买点是"跌到这里就该接"，取 low；卖点反过来取 high。
    """
    return float(bsp.klu.low if bsp.is_buy else bsp.klu.high)


def _bsp_confidence(bsp: CBS_Point) -> float:
    """取买卖点置信度：features 里有 'confidence' 就用它，否则回落默认基准。"""
    try:
        v = bsp.features["confidence"]
    except (KeyError, TypeError, AttributeError):
        v = None
    if v is None:
        return DEFAULT_BSP_CONFIDENCE
    return max(0.0, min(1.0, float(v)))


def _bsp_description(bsp: CBS_Point) -> str:
    """
    拼一句人类可读的买卖点说明。

    Note:
        CBS_Point 本身没有 description 字段，这里从它自带的 ``type2str()``
        与 features 里挑几个有用信息拼出来。有 divergence_rate 特征时一并带上，
        那是 ZS.is_divergence 判背驰时记下的实际力度比。
    """
    parts = ["/".join(_bsp_labels(bsp)), bsp.type2str()]
    price = _bsp_price(bsp)
    parts.append(f"价位{price:.2f}")
    try:
        rate = bsp.features["divergence_rate"]
        if rate is not None:
            parts.append(f"背驰力度比{rate:.2f}")
    except (KeyError, TypeError, AttributeError):
        pass
    return f"{' '.join(parts)}"


class StrategyEngine:
    """
    策略推荐:
    1. 结合缠论信号 + 走势分类 + 背驰判断
    2. 输出带置信度的操作建议
    3. 止损/止盈计算

    Note:
        ``signals`` 是 newchan 的买卖点列表（``CBSPointList`` 迭代出来的 ``CBS_Point``）；
        期望按时间升序传入，因为下面到处用 ``[-1]`` 取"最近一个"。
    """

    def __init__(self, signals: list[CBS_Point],
                 trend: str,
                 current_price: float,
                 current_level: str,
                 zhongshus: list[CZS],
                 divergence: Optional[dict] = None):
        self.signals = signals
        self.trend = trend
        self.current_price = current_price
        self.current_level = current_level
        self.zhongshus = zhongshus
        self.divergence = divergence

    def generate_signal(self) -> AiSignal:
        """生成AI策略信号"""
        # CBS_Point.is_buy 是买卖方向的权威判定（由所在笔的方向推出），
        # 比在类型字符串里找"买"字更直接
        buy_signals = [s for s in self.signals if s.is_buy]
        sell_signals = [s for s in self.signals if not s.is_buy]

        # 最近信号
        recent_buy = buy_signals[-1] if buy_signals else None
        recent_sell = sell_signals[-1] if sell_signals else None

        if recent_buy and recent_sell:
            if _bsp_idx(recent_buy) > _bsp_idx(recent_sell):
                active_signal = recent_buy
                direction = "买入"
            else:
                active_signal = recent_sell
                direction = "卖出"
        elif recent_buy:
            active_signal = recent_buy
            direction = "买入"
        elif recent_sell:
            active_signal = recent_sell
            direction = "卖出"
        else:
            active_signal = None
            direction = "观望"

        # 计算置信度
        confidence = self._calc_confidence(active_signal)

        # 风险评级
        risk = self._calc_risk(active_signal, direction)

        # 止损止盈
        entry, stop_loss, take_profit = self._calc_levels(active_signal, direction)

        # 操作窗口
        period = self._calc_holding_period()

        # 自然语言描述
        desc = self._make_description(active_signal, direction, confidence)

        return AiSignal(
            stock_code="",
            level=self.current_level,
            direction=direction,
            confidence=confidence,
            risk_level=risk,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            holding_period=period,
            description=desc,
            signals=self.signals[-5:] if self.signals else []
        )

    def _calc_confidence(self, signal: Optional[CBS_Point]) -> float:
        """计算置信度"""
        if not signal:
            return 0.0

        base = _bsp_confidence(signal)

        # 背驰加持 +15%
        if self.divergence and self.divergence.get('probability', 0) > 0.7:
            base = min(1.0, base + 0.15)

        # 多中枢震荡降低置信度
        if len(self.zhongshus) > 2:
            base *= 0.85

        # 趋势同向加持
        if self.trend == "上涨" and signal.is_buy:
            base = min(1.0, base + 0.1)
        elif self.trend == "下跌" and not signal.is_buy:
            base = min(1.0, base + 0.1)

        return round(base, 2)

    def _calc_risk(self, signal: Optional[CBS_Point],
                    direction: str) -> str:
        """风险评级"""
        if not signal:
            return "中"

        risk = _bsp_confidence(signal)

        # 在中枢内风险高
        if self.zhongshus:
            last_zs = self.zhongshus[-1]
            if last_zs.low <= self.current_price <= last_zs.high:
                risk *= 0.8

        # 背驰信号降低风险
        if self.divergence and self.divergence.get('probability', 0) > 0.7:
            risk = min(1.0, risk + 0.1)

        if risk >= 0.75:
            return "低"
        elif risk >= 0.5:
            return "中"
        return "高"

    def _calc_levels(self, signal: Optional[CBS_Point],
                     direction: str) -> tuple:
        """
        计算入场价、止损价、止盈价。

        一买 / 三买：入场价 = 信号价位（背驰点 / 回踩低点），止损在信号价下方。
        二买：入场价 = 信号价位（回踩低点），止损在信号价和一买价格之间取更低的。
        一卖 / 三卖：入场价 = 信号价位，止损在信号价上方。
        二卖：入场价 = 信号价位（反弹高点），止损在信号价和一卖价格之间取更高的。

        若无信号，入场价用当前价，止损止盈按当前价 ±3% / ±5% 估算。

        Note:
            ``CBS_Point`` 不带 stop_loss / take_profit（那是策略层的产物），
            所以这里一律走按百分比估算的兜底分支。
        """
        if not signal:
            return self.current_price, round(self.current_price * 0.97, 2), round(self.current_price * 1.05, 2)

        # 入场价 = 信号对应的实际价位，而非当前市场价
        entry = round(_bsp_price(signal), 2)

        if direction == "买入":
            if _bsp_has_main_type(signal, "2"):
                # 二买止损：信号价下方3%，但不超过一买点（确保不破一买）
                sl = round(entry * 0.97, 2)
                sl = round(min(sl, entry * 0.97), 2)
            else:
                sl = round(entry * 0.97, 2)
            tp = round(entry * 1.05, 2)
            if sl >= entry:
                sl = round(entry * 0.97, 2)
            if tp <= entry:
                tp = round(entry * 1.05, 2)
        else:
            if _bsp_has_main_type(signal, "2"):
                sl = round(entry * 1.03, 2)
                sl = round(max(sl, entry * 1.03), 2)
            else:
                sl = round(entry * 1.03, 2)
            tp = round(entry * 0.95, 2)
            if sl <= entry:
                sl = round(entry * 1.03, 2)
            if tp >= entry:
                tp = round(entry * 0.95, 2)
            if sl is not None and tp is not None and sl < tp:
                sl, tp = tp, sl

        return entry, sl, tp

    def _calc_holding_period(self) -> str:
        """操作窗口"""
        level_periods = {
            "1min": "30分钟内",
            "5min": "2-4小时",
            "15min": "半天-1天",
            "30min": "1-3天",
            "60min": "3-7天",
            "daily": "1-4周",
            "weekly": "1-3个月",
            "monthly": "3-12个月"
        }
        return level_periods.get(self.current_level, "1-3天")

    def _make_description(self, signal: Optional[CBS_Point],
                        direction: str, confidence: float) -> str:
        """生成自然语言描述"""
        parts = [f"当前: {self.trend}趋势"]

        if signal:
            parts.append(f"信号: {'/'.join(_bsp_labels(signal))}(置信{confidence:.0%})")
            parts.append(_bsp_description(signal))
        else:
            parts.append("暂无明确信号")

        if self.divergence:
            div = self.divergence
            mf = div.get("macd_force")
            mf_cn = {"directional": "同向柱", "abs": "绝对值"}.get(mf, "")
            extras: list[str] = []
            if div.get("rsi_confirm"):
                extras.append("RSI")
            if div.get("kdj_confirm"):
                extras.append("KDJ")
            osc = f" {'/'.join(extras)}确认" if extras else ""
            mf_suffix = f"({mf_cn})" if mf_cn else ""
            parts.append(
                f"背驰概率: {div.get('probability', 0):.0%}{mf_suffix}{osc}"
            )

        if self.zhongshus:
            last_zs = self.zhongshus[-1]
            parts.append(f"中枢: [{last_zs.low:.2f},{last_zs.high:.2f}]")

        return " | ".join(parts)
