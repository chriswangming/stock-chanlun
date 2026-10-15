"""买卖点（SignalDetector / BSPointList）行为测试。"""
import os
import sys
import unittest
from datetime import datetime, timedelta

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

import pandas as pd

from chanlun.config import BSPConfig, BSPointConfig, ChanlunConfig
from chanlun.elements import Bi, BuySellPoint, KLine
from chanlun.engine import ChanlunEngine
from chanlun.signals import SignalDetector, _main_name

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "chan_reference.json")


def _fixture_df() -> pd.DataFrame:
    import json
    with open(FIXTURE) as f:
        data = json.load(f)
    return pd.DataFrame([
        {
            "date": datetime.strptime(r[0], "%Y-%m-%d"),
            "open": float(r[1]), "high": float(r[2]),
            "low": float(r[3]), "close": float(r[4]),
            "volume": 1000.0,
        }
        for r in data["klines"]
    ])


class SignalDetectorTests(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 1, 1, 9, 30)

    # ── 类型命名 ──────────────────────────────────
    def test_main_type_naming(self):
        self.assertEqual(_main_name("1", True), "一买")
        self.assertEqual(_main_name("1p", True), "一买")
        self.assertEqual(_main_name("2", False), "二卖")
        self.assertEqual(_main_name("2s", False), "二卖")
        self.assertEqual(_main_name("3a", True), "三买")
        self.assertEqual(_main_name("3b", False), "三卖")

    # ── 缺内部结构时不出假信号 ─────────────────────
    def test_detect_all_without_internal_context_returns_empty(self):
        detector = SignalDetector(bis=[], segments=[], zhongshus=[], level="daily")
        self.assertEqual(detector.detect_all(), [])

    # ── 端到端：chan.py 自带日线样例 ────────────────
    def _analyze(self, **bsp_conf):
        cfg = ChanlunConfig()
        cfg.bsp = BSPConfig(buy=BSPointConfig(**bsp_conf),
                            sell=BSPointConfig(**bsp_conf))
        return ChanlunEngine(KLine.from_dataframe(_fixture_df()), config=cfg).analyze(level="daily")

    def test_signals_follow_chan_py_on_fixture(self):
        """chan.py 在同样数据 + min_zs_cnt=0 下给出 6 个买卖点，主类型序列须一致。"""
        result = self._analyze(min_zs_cnt=0)
        got = [(s.type, s.datetime) for s in result.signals]
        self.assertEqual([t for t, _ in got],
                         ["一卖", "二卖", "一买", "二买", "二买", "一卖"])
        # 时间递增
        self.assertEqual([d for _, d in got], sorted(d for _, d in got))

    def test_buy_price_is_bi_end_low_and_sell_is_bi_end_high(self):
        result = self._analyze(min_zs_cnt=0)
        bi_by_end = {b.end: b for b in result.bis}
        for sig in result.signals:
            bi = bi_by_end[sig.datetime]
            if "买" in sig.type:
                self.assertAlmostEqual(sig.price, bi.low, places=6)
            else:
                self.assertAlmostEqual(sig.price, bi.high, places=6)

    def test_signal_confidence_and_stop_loss_in_valid_range(self):
        result = self._analyze(min_zs_cnt=0)
        self.assertTrue(result.signals)
        for sig in result.signals:
            self.assertGreaterEqual(sig.confidence, 0.0)
            self.assertLessEqual(sig.confidence, 1.0)
            self.assertIsNotNone(sig.stop_loss)
            if "买" in sig.type:
                self.assertLess(sig.stop_loss, sig.price)
                self.assertGreater(sig.take_profit, sig.price)
            else:
                self.assertGreater(sig.stop_loss, sig.price)
                self.assertLess(sig.take_profit, sig.price)
            self.assertTrue(sig.description)

    def test_default_config_needs_at_least_one_zhongshu(self):
        """默认配置 min_zs_cnt=1：中枢不够的段不会产生一类买卖点。"""
        result = self._analyze()
        self.assertEqual([s.type for s in result.signals], ["一卖"])

    def test_bsp_type_filter_narrows_signals(self):
        result = self._analyze(bs_type="1,1p", min_zs_cnt=0)
        self.assertEqual([s.type for s in result.signals], ["一卖", "一买", "一卖"])

    # ── 支撑阻力（不依赖内部结构） ──────────────────
    def test_detect_support_resistance_dedup_keeps_stronger_level(self):
        detector = SignalDetector(bis=[], segments=[], zhongshus=[], level="daily")
        shared_price = 10.123

        weak_signal = BuySellPoint(
            type="一买", level="daily", price=shared_price,
            datetime=self.t0 + timedelta(minutes=10), confidence=0.6,
        )
        strong_signal = BuySellPoint(
            type="三买", level="daily", price=10.124,  # 与 10.123 按两位小数同 key
            datetime=self.t0 + timedelta(minutes=20), confidence=0.9,
        )

        levels = detector.detect_support_resistance(
            klines=[], signals=[weak_signal, strong_signal],
        )
        support_levels = [lvl for lvl in levels if lvl.type == "support"]
        self.assertEqual(len(support_levels), 1)
        self.assertAlmostEqual(support_levels[0].strength, 0.9)
        self.assertAlmostEqual(support_levels[0].price, strong_signal.price)


if __name__ == "__main__":
    unittest.main()
