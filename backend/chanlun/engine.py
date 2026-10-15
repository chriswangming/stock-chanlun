"""
缠论分析引擎 — 整合所有组件
"""
from .config import ChanlunConfig
from .elements import (
    KLine, Zhongshu, BuySellPoint,
    ChanlunAnalysis, MergedKLineInfo, IncludedRaw
)
from .base import reindex_klines
from .bi_detector import BiDetector
from .macd import MACDSeries
from .segment_detector import SegmentDetector
from .signals import SignalDetector


class ChanlunEngine:
    """
    缠论分析引擎
    用法:
        engine = ChanlunEngine(klines)          # klines: list[KLine]
        result = engine.analyze(level="daily")
    """

    def __init__(self, klines: list[KLine], config: ChanlunConfig | None = None):
        """
        klines: list[KLine]（DataFrame 只允许活在最外层：run_analysis 里
                用 KLine.from_dataframe 转一次，引擎开始不再接触 DataFrame）
        config: 缠论计算参数（笔/线段/中枢），默认对齐 chan.py
        """
        # 统一按下标写 idx：MACD 序列 / 笔端点下标 / 合并K线区间都按它对齐
        self.klines: list[KLine] = reindex_klines(list(klines))
        self.config = config or ChanlunConfig()

    def analyze(self, level: str = "daily") -> ChanlunAnalysis:
        """执行完整缠论分析"""
        # 1. 包含处理 + 分型（在 BiDetector 内部完成）
        bi_detector = BiDetector(self.klines, config=self.config)
        bis = bi_detector.detect()

        # 2. 力度序列（MACD）—— 买卖点的背驰判定要用，必须在建笔序列前算好
        macd_series = MACDSeries.from_klines(bi_detector.klines)

        # 3. 线段（特征序列法）与中枢（段内中枢，基于笔的重叠）
        seg_detector = SegmentDetector(bis, config=self.config, macd_series=macd_series)
        segments = seg_detector.detect_segments()
        zhongshus = seg_detector.detect_zhongshus(segments)

        # 4. 买卖点判定（chan.py BuySellPoint 口径，需要笔序列 + 段内中枢 + 力度）
        sig_detector = SignalDetector(
            bis, segments, zhongshus, level=level,
            config=self.config.bsp,
            bi_lines=seg_detector.lines,
            seg_list=seg_detector.seg_list,
            macd_series=macd_series,
        )
        signals = sig_detector.detect_all()
        # trend = sig_detector.detect_trend()

        # 5. 支撑阻力位
        klines_dict = [{"date": k.date, "high": k.high, "low": k.low} for k in self.klines]
        support_resistance = sig_detector.detect_support_resistance(klines_dict, signals)

        # 6. 自然语言总结
        # summary = self._make_summary(trend, signals, zhongshus)

        return ChanlunAnalysis(
            stock_code="",
            level=level,
            klines=self.klines,
            merged_klines=self._to_merged_klines(bi_detector.merged_klines),
            bis=bis,
            xians=segments,
            zhongshus=zhongshus,
            signals=signals,
            trend='未知',
            summary='summary',
            # trend=trend,
            # summary=summary,
            support_resistance=support_resistance
        )



    @staticmethod
    def _to_merged_klines(merged) -> list[MergedKLineInfo]:
        """把包含处理后的合并K线转成可序列化的结构。

        注意 high/low 用合并后的区间值（MergedKLine.high/low），而不是区间内
        原始K线的最高/最低 —— 包含处理的核心就是按合并方向「削掉」被包住的部分。
        """
        out: list[MergedKLineInfo] = []
        for m in merged:
            if not len(m.lst):
                continue
            first, last = m.lst[0], m.lst[-1]
            # 被包含进来的原始K线（lst[1:]）：前端用蓝色细线画它们的真实高低范围
            included = [
                IncludedRaw(date=r.date, high=float(r.high), low=float(r.low))
                for r in m.lst[1:]
            ]
            out.append(
                MergedKLineInfo(
                    start=first.date,
                    end=last.date,
                    start_idx=int(first.idx),
                    end_idx=int(last.idx),
                    high=float(m.high),
                    low=float(m.low),
                    dir=m.dir,
                    count=len(m.lst),
                    fx=m.fx,
                    included=included,
                )
            )
        return out

    def _make_summary(self, trend: str, signals: list[BuySellPoint],
                      zhongshus: list[Zhongshu]) -> str:
        parts = [f"当前走势: {trend}"]
        if zhongshus:
            last_zs = zhongshus[-1]
            parts.append(
                f"中枢区间: [{last_zs.range_low:.2f}, {last_zs.range_high:.2f}]"
            )
        if signals:
            latest = signals[-1]
            parts.append(f"最近信号: {latest.type} @ {latest.price:.2f}")
        else:
            parts.append("暂无明确信号")
        return " | ".join(parts)
