import os
import sys
import unittest
from datetime import datetime, timedelta

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from chanlun.elements import Bi, BuySellPoint, Zhongshu
from chanlun.signals import SignalDetector


class SignalDetectorTests(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime(2026, 1, 1, 9, 30)

    def _bi(self, idx: int, direction: str, start_minutes: int, end_minutes: int, high: float, low: float) -> Bi:
        start = self.t0 + timedelta(minutes=start_minutes)
        end = self.t0 + timedelta(minutes=end_minutes)
        return Bi(
            id=f"bi_{idx}",
            start=start,
            end=end,
            direction=direction,
            high=high,
            low=low,
            start_price=low if direction == "up" else high,
            end_price=high if direction == "up" else low,
        )

    def test_detect_2nd_buy_uses_bi_end_time_after_first_buy(self):
        detector = SignalDetector(bis=[], segments=[], zhongshus=[], level="daily")
        first_buy_time = self.t0 + timedelta(minutes=30)
        first_buy = BuySellPoint(
            type="一买",
            level="daily",
            price=100.0,
            datetime=first_buy_time,
            confidence=0.8,
        )

        # 起点早于一买，但终点晚于一买，应该被纳入候选
        crossing_bi = self._bi(
            idx=1,
            direction="down",
            start_minutes=20,
            end_minutes=35,
            high=103.0,
            low=98.5,
        )
        detector.bis = [crossing_bi]

        signals = detector._detect_2nd_buy([first_buy])
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].type, "二买")
        self.assertEqual(signals[0].datetime, crossing_bi.end)

    def test_detect_3rd_buy_uses_latest_breakout_zhongshu(self):
        detector = SignalDetector(bis=[], segments=[], zhongshus=[], level="daily")

        older = Zhongshu(
            id="zs_old",
            start=self.t0,
            end=self.t0 + timedelta(minutes=30),
            range_high=100.0,
            range_low=95.0,
            xian_ids=["x1", "x2", "x3"],
            level=2,
        )
        latest = Zhongshu(
            id="zs_latest",
            start=self.t0 + timedelta(minutes=40),
            end=self.t0 + timedelta(minutes=60),
            range_high=110.0,
            range_low=106.0,
            xian_ids=["x4", "x5", "x6"],
            level=2,
        )

        # 先有向上突破，再有回踩但不破中枢上沿 => 三买成立
        break_up = self._bi(
            idx=2,
            direction="up",
            start_minutes=65,
            end_minutes=75,
            high=112.5,
            low=107.0,
        )
        retest = self._bi(
            idx=3,
            direction="down",
            start_minutes=76,
            end_minutes=84,
            high=111.5,
            low=110.3,
        )

        detector.zhongshus = [older, latest]
        detector.bis = [break_up, retest]

        signals = detector._detect_3rd_buy()
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].type, "三买")
        self.assertEqual(signals[0].stop_loss, latest.range_low)
        self.assertIn("zs_latest", latest.id)

    def test_detect_support_resistance_dedup_keeps_stronger_level(self):
        detector = SignalDetector(bis=[], segments=[], zhongshus=[], level="daily")
        shared_price = 10.123

        # 同价位（两位小数后相同）来源冲突时，应保留更高强度
        weak_signal = BuySellPoint(
            type="一买",
            level="daily",
            price=shared_price,
            datetime=self.t0 + timedelta(minutes=10),
            confidence=0.6,
        )
        strong_signal = BuySellPoint(
            type="三买",
            level="daily",
            price=10.124,  # 与 10.123 按两位小数同 key
            datetime=self.t0 + timedelta(minutes=20),
            confidence=0.9,
        )

        levels = detector.detect_support_resistance(
            klines=[],
            signals=[weak_signal, strong_signal],
        )
        support_levels = [lvl for lvl in levels if lvl.type == "support"]
        self.assertEqual(len(support_levels), 1)
        self.assertAlmostEqual(support_levels[0].strength, 0.9)
        self.assertAlmostEqual(support_levels[0].price, strong_signal.price)


if __name__ == "__main__":
    unittest.main()
