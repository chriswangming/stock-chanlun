"""
买卖点与 chan.py 的对齐回归测试。

fixtures/bsp_variants.json 里存了三份K线（chan.py 自带的日线样例、500 根随机走势、
东山精密 002384 真实日线），以及 chan.py 在 15 组买卖点参数（BuySellPoint 配置）
下算出的买卖点列表（笔下标 / 买或卖 / 类型集合）。

本测试要求 stock-chanlun 的实现逐项给出完全一致的结果 —— 改动了笔的力度算法
（MACD）、背驰判定、二/三类的取舍规则，这里会立刻失败。

对照组：chan.py 的 BuySellPoint/BSPointList.py（CChanConfig 默认参数）。
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

from chanlun.bi_detector import BiDetector
from chanlun.config import BSPConfig, BSPointConfig
from chanlun.macd import MACDSeries
from chanlun.segment_detector import SegmentDetector
from chanlun.signals import BSPointList

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "bsp_variants.json")


def _df_from_rows(rows):
    return pd.DataFrame([
        {
            "date": datetime.strptime(r[0], "%Y-%m-%d"),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": 1e6,
        }
        for r in rows
    ])


class BSPAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(FIXTURE) as f:
            cls.data = json.load(f)

    def _run(self, rows, conf: dict):
        df = _df_from_rows(rows)
        bi_detector = BiDetector(df)
        bis = bi_detector.detect()
        macd = MACDSeries.from_bars(bi_detector.raw_bars)
        seg_detector = SegmentDetector(bis, macd_series=macd)
        seg_detector.detect_segments()
        seg_detector.detect_zhongshus()
        bsp_list = BSPointList(
            BSPConfig(buy=BSPointConfig(**conf), sell=BSPointConfig(**conf)), macd)
        bsp_list.cal(seg_detector.lines, seg_detector.seg_list)
        return [(b.bi.idx, b.is_buy, tuple(b.types)) for b in bsp_list.get_sorted_bsp()]

    def test_all_variants_match_chan_py(self):
        checked = 0
        for name, block in self.data.items():
            rows = block["klines"]
            for vname, variant in block["variants"].items():
                with self.subTest(dataset=name, variant=vname):
                    got = self._run(rows, variant["config"])
                    expect = [(b["bi_idx"], b["is_buy"], tuple(b["types"]))
                              for b in variant["bsps"]]
                    self.assertEqual(got, expect)
                    checked += 1
        # 防止 fixture 被误删导致测试空转
        self.assertGreaterEqual(checked, 30)

    def test_variants_cover_all_bsp_types(self):
        """确保基准里出现过 1 / 1p / 2 / 2s / 3a 各类买卖点。"""
        seen = set()
        for block in self.data.values():
            for variant in block["variants"].values():
                for b in variant["bsps"]:
                    seen.update(b["types"])
        self.assertTrue({"1", "1p", "2", "2s", "3a"}.issubset(seen), seen)


if __name__ == "__main__":
    unittest.main()
