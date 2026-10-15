"""
与 chan.py 的对齐回归测试。

fixtures/chan_reference.json 里存了一份固定日线数据，以及 chan.py（/Users/chriswang/workspace/chan.py
默认 CChanConfig）在同一份数据上算出的 合并K线 / 分型 / 笔 / 线段 / 中枢 结果。

本测试要求 stock-chanlun 的新实现逐项给出完全一致的结果 —— 一旦有人改动了
包含处理、分型、笔、特征序列线段或中枢算法，这里会立刻失败。
"""

import json
import os
import sys
import unittest
from datetime import datetime

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import pandas as pd

from chanlun.elements import KLine
from chanlun.engine import ChanlunEngine
from chanlun.bi_detector import BiDetector
from chanlun.fenxing_detector import FenxingDetector
from chanlun.segment_detector import SegmentDetector

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "chan_reference.json")


def _norm_date(s: str) -> str:
    """chan.py 输出 2023/01/11，我们输出 2023-01-11 00:00:00。"""
    return str(s).replace(" 00:00:00", "").replace("/", "-")


def _load_fixture():
    with open(FIXTURE) as f:
        data = json.load(f)
    df = pd.DataFrame(
        [
            {
                "date": datetime.strptime(r[0], "%Y-%m-%d"),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": 1000.0,
            }
            for r in data["klines"]
        ]
    )
    return df, data["expected"]


class ChanPyAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.df, cls.expected = _load_fixture()
        cls.klines = KLine.from_dataframe(cls.df)
        cls.fx = FenxingDetector(cls.klines)
        cls.bd = BiDetector(cls.klines)
        cls.bis = cls.bd.detect()
        cls.result = ChanlunEngine(cls.klines).analyze(level="daily")

    # ── 1. 包含处理后的合并K线 ───────────────────────────────
    def test_merged_klines_match_chan_py(self):
        got = [
            [_norm_date(m.time_begin), _norm_date(m.time_end), round(m.high, 4),
             round(m.low, 4), (m.fx or "UNKNOWN").upper(), len(m.lst)]
            for m in self.bd.merged_klines
        ]
        want = [
            [_norm_date(e[0]), _norm_date(e[1]), e[2], e[3], e[4], e[5]]
            for e in self.expected["klc"]
        ]
        self.assertEqual(len(got), len(want))
        for i, (g, e) in enumerate(zip(got, want)):
            self.assertEqual(g, e, f"第 {i} 根合并K线不一致")

    # ── 2. 分型 ─────────────────────────────────────────────
    def test_fenxings_match_chan_py(self):
        got = [
            [_norm_date(m.time_begin), m.fx.upper(), round(m.high, 4), round(m.low, 4)]
            for m in self.bd.merged_klines
            if m.fx
        ]
        want = [
            [_norm_date(e[0]), e[4], e[2], e[3]]
            for e in self.expected["klc"] if e[4] != "UNKNOWN"
        ]
        self.assertEqual(got, want)

    # ── 3. 笔 ───────────────────────────────────────────────
    def test_bis_match_chan_py(self):
        got = [
            [_norm_date(b.start), _norm_date(b.end), b.direction.upper(),
             round(b.start_price, 4), round(b.end_price, 4), b.is_sure]
            for b in self.bis
        ]
        want = [
            [_norm_date(e[0]), _norm_date(e[1]), e[2], e[3], e[4], e[5]]
            for e in self.expected["bi"]
        ]
        self.assertEqual(len(got), len(want), "笔的数量与 chan.py 不一致")
        self.assertEqual(got, want)

    # ── 4. 线段（特征序列法） ───────────────────────────────
    def test_segments_match_chan_py(self):
        got = [
            [_norm_date(s.start), _norm_date(s.end), s.direction.upper(),
             s.is_sure, len(s.bi_ids)]
            for s in self.result.xians
        ]
        want = [
            [_norm_date(e[0]), _norm_date(e[1]), e[2], e[3], e[4]]
            for e in self.expected["seg"]
        ]
        self.assertEqual(got, want)

    # ── 5. 中枢 ─────────────────────────────────────────────
    def test_zhongshus_match_chan_py(self):
        got = [
            [_norm_date(z.start), _norm_date(z.end), round(z.range_low, 4),
             round(z.range_high, 4), z.is_sure]
            for z in self.result.zhongshus
        ]
        want = [
            [_norm_date(e[0]), _norm_date(e[1]), e[2], e[3], e[4]]
            for e in self.expected["zs"]
        ]
        self.assertEqual(got, want)

    # ── 结构性不变量（不依赖 fixture，保证一般正确性） ────────
    def test_bis_alternate_direction_and_are_connected(self):
        for prev, cur in zip(self.bis, self.bis[1:]):
            self.assertNotEqual(prev.direction, cur.direction, "相邻笔必须方向相反")
            self.assertLessEqual(prev.end, cur.start, "相邻笔必须首尾相接")
        for b in self.bis:
            if b.direction == "up":
                self.assertEqual(b.start_price, b.low)
                self.assertEqual(b.end_price, b.high)
            else:
                self.assertEqual(b.start_price, b.high)
                self.assertEqual(b.end_price, b.low)

    def test_segments_cover_all_bis_in_order(self):
        if not self.result.xians:
            return
        self.assertEqual(self.result.xians[0].bi_ids[0], self.bis[0].id)
        self.assertEqual(self.result.xians[-1].bi_ids[-1], self.bis[-1].id)

    def test_zhongshu_range_is_an_overlap(self):
        for z in self.result.zhongshus:
            self.assertLess(z.range_low, z.range_high, "中枢区间必须有正宽度")
            self.assertGreaterEqual(z.bi_cnt, 1)
            self.assertTrue(z.bi_ids, "中枢必须记录构成它的笔")

    def test_merged_klines_monotonic_and_nested(self):
        merged = self.bd.merged_klines
        self.assertEqual(merged[0].time_begin, min(m.time_begin for m in merged))
        total_raw = sum(len(m.lst) for m in merged)
        self.assertEqual(total_raw, len(self.df), "合并后原始K线总数应保持不变")


if __name__ == "__main__":
    unittest.main()
