"""ChanlunEngine K 线输出与包含处理。"""
import os
import sys
import unittest
from datetime import datetime

import pandas as pd

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from chanlun.bi_detector import BiDetector
from chanlun.elements import KLine
from chanlun.engine import ChanlunEngine


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"date": datetime(2024, 1, 2), "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "volume": 1000.0},
        {"date": datetime(2024, 1, 3), "open": 10.4, "high": 10.8, "low": 10.0, "close": 10.6, "volume": 800.0},
        {"date": datetime(2024, 1, 4), "open": 10.7, "high": 12.0, "low": 10.6, "close": 11.8, "volume": 1200.0},
        {"date": datetime(2024, 1, 5), "open": 11.5, "high": 11.8, "low": 11.0, "close": 11.2, "volume": 900.0},
        {"date": datetime(2024, 1, 8), "open": 11.0, "high": 11.3, "low": 10.5, "close": 10.8, "volume": 700.0},
    ])


def _sample_klines() -> list[KLine]:
    """引擎只认 KLine；DataFrame 到 KLine 的转换只在最外层做一次。"""
    return KLine.from_dataframe(_sample_df())


class ChanlunEngineKlinesTests(unittest.TestCase):
    def test_result_klines_keep_raw_window_length(self):
        klines = _sample_klines()
        result = ChanlunEngine(klines).analyze(level="daily")
        self.assertEqual(len(result.klines), len(klines))

    # [冗余-已注释] D1: BiDetector.processed_klines 已注释（生产代码无引用）
    # def test_processed_klines_fewer_than_raw_after_inclusion(self):
    #     raw = _sample_df()
    #     processed = BiDetector(raw).processed_klines
    #     self.assertLess(len(processed), len(raw))

    def test_merged_klines_cover_all_raw_bars(self):
        """合并K线必须首尾相接、无缝覆盖全部原始K线（前端据此画区间框）。"""
        klines = _sample_klines()
        result = ChanlunEngine(klines).analyze(level="daily")
        mk = result.merged_klines
        # 该样例: [0,1] 被合并 -> [2,3] 被合并 -> [4] 独立
        self.assertEqual([m.count for m in mk], [2, 2, 1])
        self.assertEqual(mk[0].start_idx, 0)
        self.assertEqual(mk[-1].end_idx, len(klines) - 1)
        for a, b in zip(mk, mk[1:]):
            self.assertEqual(b.start_idx, a.end_idx + 1)
        for m in mk:
            self.assertGreaterEqual(m.high, m.low)

    def test_merged_klines_high_low_follow_combine_direction(self):
        """向上合并时 low 取 max（被包住的低点被削掉），不是区间内最低价。"""
        result = ChanlunEngine(_sample_klines()).analyze(level="daily")
        first = result.merged_klines[0]
        # 前两根: [9.5, 11] 与 [10.0, 10.8]，向上合并后应为 [10.0, 11]
        self.assertEqual(first.low, 10.0)
        self.assertEqual(first.high, 11.0)

    def test_serialize_includes_merged_klines(self):
        from core.chanlun_response import serialize_chanlun_analysis

        result = ChanlunEngine(_sample_klines()).analyze(level="daily")
        data = serialize_chanlun_analysis(result)
        self.assertIn("merged_klines", data)
        self.assertEqual(len(data["merged_klines"]), len(result.merged_klines))
        row = data["merged_klines"][0]
        for key in ("start", "end", "start_idx", "end_idx", "high", "low", "dir", "count"):
            self.assertIn(key, row)
        self.assertEqual(row["count"], 2)


if __name__ == "__main__":
    unittest.main()
