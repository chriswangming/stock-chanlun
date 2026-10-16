"""
缠论分析引擎 — 整合所有组件，是本包对外的唯一入口。

链路位置::

    原始K线 → 包含处理 → 分型 → 笔 → 线段 → 中枢 → 买卖点/支撑阻力
    ↑__________________ 本模块负责按序编排 __________________↑

用法::

    engine = ChanlunEngine(klines_df)
    result = engine.analyze(level="daily")

Note:
    引擎内部计算用的是**包含合并后序列**（由 ``BiDetector`` 内部的
    ``FenxingDetector`` 产出），但对外返回的 ``klines`` 是**原始 OHLC**，
    详见 ``_to_klines`` 的说明。
"""
import pandas as pd
from datetime import datetime
from .elements import (
    KLine, MergedKLine, IncludedRaw, Bi, XianSegment, Zhongshu, BuySellPoint,
    ChanlunAnalysis
)
from .bi_detector import BiDetector
from .segment_detector import SegmentDetector
from .signal_detector import SignalDetector


class ChanlunEngine:
    """
    缠论分析引擎
    用法:
        engine = ChanlunEngine(klines_df)
        result = engine.analyze(level="daily")
    """

    def __init__(self, klines: pd.DataFrame):
        """
        Args:
            klines: DataFrame，列为 [date, open, high, low, close, volume]；
                也支持中文列名，会在 ``_ensure_columns`` 里自动映射。

        Note:
            构造时先 ``copy()``，避免后续 ``rename`` / ``sort_values``
            污染调用方传入的原始 DataFrame。
        """
        self.raw_klines = klines.copy()
        self._ensure_columns()

    def _ensure_columns(self):
        """
        规范化输入 DataFrame：中文列名 → 英文列名，日期转 datetime，并按日期升序排序。

        Note:
            列名映射覆盖了常见数据源的中文表头；``'时间'`` 与 ``'日期'`` 都映射到
            ``date``。若数据源用的是其它列名（如 ``'Date'``），需在上层先统一。
            排序与重建索引是必须的：笔 / 线段 / 中枢的滑动窗口都依赖严格升序。
        """
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
        """
        执行完整缠论分析，返回结构化的 ``ChanlunAnalysis``。

        Args:
            level: 级别标签（如 "daily" / "30min"），仅作为标记原样写入结果，
                不影响任何计算逻辑。

        Returns:
            包含原始K线、笔、线段、中枢、买卖点、走势与支撑阻力的完整结果。

        Note:
            编排顺序是**严格依赖**的，共 7 步，不可调换：

            1. 笔检测（内部已含包含处理与分型识别）；
            2. ——（分型已合并在第 1 步内）；
            3. 线段识别（依赖笔）；
            4. 中枢识别（依赖线段）；
            5a. 买卖点判定（依赖笔 + 线段 + 中枢）与走势判断；
            5b. 支撑阻力位（依赖 K 线与第 5 步的买卖点）；
            6. 自然语言总结。
        """
        # 1. 先把原始K线统一成 KLine 对象：笔检测与最终对外返回共用同一份，
        #    避免两次转换产生口径差异
        kline_objects = self._to_klines()

        # 笔检测器内部已完成分型识别与包含处理，无需单独跑一遍分型
        bi_detector = BiDetector(kline_objects)
        bis = bi_detector.detect(min_bars=5)

        # 3. 线段识别
        seg_detector = SegmentDetector(bis)
        segments = seg_detector.detect_segments(min_overlap_bis=3)

        # 4. 中枢识别
        zhongshus = seg_detector.detect_zhongshus(segments)

        # 5a. 买卖点判定
        sig_detector = SignalDetector(bis, segments, zhongshus, level=level)
        signals = sig_detector.detect_all()
        trend = sig_detector.detect_trend()

        # 5b. 支撑阻力位：需要第 6 步产出的 kline_objects，故排在其后
        klines_dict = [{"date": k.date, "high": k.high, "low": k.low} for k in kline_objects]
        support_resistance = sig_detector.detect_support_resistance(klines_dict, signals)

        # 6. 自然语言总结
        summary = self._make_summary(trend, signals, zhongshus)

        return ChanlunAnalysis(
            stock_code="",
            level=level,
            klines=kline_objects,
            merged_klines=bi_detector._fenxing_detector.merged_klines,
            bis=bis,
            xians=segments,
            zhongshus=zhongshus,
            signals=signals,
            trend=trend,
            summary=summary,
            support_resistance=support_resistance
        )

    def _to_klines(self, df: pd.DataFrame | None = None) -> list[KLine]:
        """
        把 DataFrame 转成 ``KLine`` 对象列表。

        Args:
            df: 可选。传入时序列化该 DataFrame；不传则用 ``self.raw_klines``。

        Returns:
            ``KLine`` 列表。

        Note:
            默认返回的是**原始 OHLC**，而不是包含合并后的序列。原因：

            - 前端要画的是真实行情（开高低收），合并序列会丢信息；
            - 笔 / 中枢的端点只有**日期**没有合并后下标，前端靠
              ``dateToIdxRobust`` 这类按日期查找的映射把它们对齐到原始K线上；
              若这里返回合并序列，日期对不上就会错位。
        """
        source = df if df is not None else self.raw_klines
        rows: list[KLine] = []
        for idx, row in enumerate(source.itertuples(index=False)):
            d = row.date
            # pandas Timestamp → python datetime，保证 pydantic 直接校验通过
            if hasattr(d, "to_pydatetime"):
                d = d.to_pydatetime()
            rows.append(
                KLine(
                    date=d,
                    open=float(row.open),
                    high=float(row.high),
                    low=float(row.low),
                    close=float(row.close),
                    # 原始序列下标：合并K线的 start_idx/end_idx 靠它回溯定位
                    idx=idx,
                    # 用 getattr 兜底：数据源可能缺 volume / amount 列
                    volume=float(getattr(row, "volume", 0) or 0),
                    amount=float(getattr(row, "amount", 0) or 0),
                )
            )
        return rows

    # @staticmethod
    # def _to_merged_klines(merged) -> list[MergedKLineInfo]:
    #     """把包含处理后的合并K线转成可序列化的结构。

    #     注意 high/low 用合并后的区间值（MergedKLine.high/low），而不是区间内
    #     原始K线的最高/最低 —— 包含处理的核心就是按合并方向「削掉」被包住的部分。
    #     """
    #     out: list[MergedKLineInfo] = []
    #     for m in merged:
    #         if not len(m.lst):
    #             continue
    #         first, last = m.lst[0], m.lst[-1]
    #         # 被包含进来的原始K线（lst[1:]）：前端用蓝色细线画它们的真实高低范围
    #         included = [
    #             IncludedRaw(date=r.date, high=float(r.high), low=float(r.low))
    #             for r in m.lst[1:]
    #         ]
    #         out.append(
    #             MergedKLineInfo(
    #                 start=first.date,
    #                 end=last.date,
    #                 start_idx=int(first.idx),
    #                 end_idx=int(last.idx),
    #                 high=float(m.high),
    #                 low=float(m.low),
    #                 dir=m.dir,
    #                 count=len(m.lst),
    #                 fx=m.fx,
    #                 included=included,
    #             )
    #         )
    #     return out
    
    def _make_summary(self, trend: str, signals: list[BuySellPoint],
                      zhongshus: list[Zhongshu]) -> str:
        """
        生成一段简短的自然语言总结，供前端直接展示。

        Args:
            trend:     当前走势（上涨 / 下跌 / 盘整 / 未知）
            signals:   已检出的买卖点（按时间升序）
            zhongshus: 已检出的中枢（按时间升序）

        Returns:
            形如「当前走势: 上涨 | 中枢区间: [x, y] | 最近信号: 一买 @ z」的字符串。

        Note:
            只取**最后一个**中枢和**最后一个**信号（即当下的状态），
            历史中枢 / 信号不出现在总结里。
        """
        parts = [f"当前走势: {trend}"]
        # 只描述当前所处的最后一个中枢
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
