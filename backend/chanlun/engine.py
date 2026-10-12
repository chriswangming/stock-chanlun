"""
缠论分析引擎 — 整合所有组件
"""
import pandas as pd
from datetime import datetime
from .elements import (
    KLine, Bi, XianSegment, Zhongshu, BuySellPoint,
    ChanlunAnalysis
)
from .bi_detector import BiDetector
from .segment_detector import SegmentDetector
from .signals import SignalDetector


class ChanlunEngine:
    """
    缠论分析引擎
    用法:
        engine = ChanlunEngine(klines_df)
        result = engine.analyze(level="daily")
    """

    def __init__(self, klines: pd.DataFrame):
        """
        klines: DataFrame, columns=[date, open, high, low, close, volume]
        """
        self.raw_klines = klines.copy()
        self._ensure_columns()

    def _ensure_columns(self):
        col_map = {
            '日期': 'date', '开盘': 'open', '收盘': 'close',
            '最高': 'high', '最低': 'low', '成交量': 'volume',
            '时间': 'date'
        }
        self.raw_klines.rename(columns=col_map, inplace=True)
        if 'date' in self.raw_klines.columns:
            self.raw_klines['date'] = pd.to_datetime(self.raw_klines['date'])
        self.raw_klines.sort_values('date', inplace=True)
        self.raw_klines.reset_index(drop=True, inplace=True)

    def analyze(self, level: str = "daily") -> ChanlunAnalysis:
        """执行完整缠论分析"""
        # 笔检测器内部已完成分型识别与包含处理，无需单独跑一遍分型
        bi_detector = BiDetector(self.raw_klines)
        bis = bi_detector.detect(min_bars=5)

        # 3. 线段识别
        seg_detector = SegmentDetector(bis)
        segments = seg_detector.detect_segments(min_overlap_bis=3)

        # 4. 中枢识别
        zhongshus = seg_detector.detect_zhongshus(segments)

        # 5. 买卖点判定
        sig_detector = SignalDetector(bis, segments, zhongshus, level=level)
        signals = sig_detector.detect_all()
        trend = sig_detector.detect_trend()

        # 6. K 线对象：API/图表用原始 OHLC（笔/中枢在引擎内基于包含处理后序列计算，前端 dateToIdxRobust 映射）
        kline_objects = self._to_klines()

        # 5b. 支撑阻力位
        klines_dict = [{"date": k.date, "high": k.high, "low": k.low} for k in kline_objects]
        support_resistance = sig_detector.detect_support_resistance(klines_dict, signals)

        # 7. 自然语言总结
        summary = self._make_summary(trend, signals, zhongshus)

        return ChanlunAnalysis(
            stock_code="",
            level=level,
            klines=kline_objects,
            bis=bis,
            xians=segments,
            zhongshus=zhongshus,
            signals=signals,
            trend=trend,
            summary=summary,
            support_resistance=support_resistance
        )

    def _to_klines(self, df: pd.DataFrame | None = None) -> list[KLine]:
        """默认返回原始 K 线窗口；传入 df 时可序列化任意 DataFrame。"""
        source = df if df is not None else self.raw_klines
        rows: list[KLine] = []
        for row in source.itertuples(index=False):
            d = row.date
            if hasattr(d, "to_pydatetime"):
                d = d.to_pydatetime()
            rows.append(
                KLine(
                    date=d,
                    open=float(row.open),
                    high=float(row.high),
                    low=float(row.low),
                    close=float(row.close),
                    volume=float(getattr(row, "volume", 0) or 0),
                    amount=float(getattr(row, "amount", 0) or 0),
                )
            )
        return rows

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
