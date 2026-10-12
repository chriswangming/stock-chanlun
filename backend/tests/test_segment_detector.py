import os
import sys
import unittest
from datetime import datetime, timedelta

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from chanlun.elements import Bi
from chanlun.segment_detector import SegmentDetector, CZS, build_lines


class SegmentDetectorTests(unittest.TestCase):
    """线段（特征序列法）与中枢（chan.py 段内中枢）的单元测试。"""

    def setUp(self):
        self.t0 = datetime(2026, 1, 2, 9, 30)

    def _bi(self, idx: int, direction: str, start_min: int, end_min: int,
            high: float, low: float, is_sure: bool = True) -> Bi:
        start = self.t0 + timedelta(minutes=start_min)
        end = self.t0 + timedelta(minutes=end_min)
        return Bi(
            id=f"bi_{idx}",
            start=start,
            end=end,
            direction=direction,
            high=high,
            low=low,
            start_price=low if direction == "up" else high,
            end_price=high if direction == "up" else low,
            is_sure=is_sure,
        )

    # ── 线段 ────────────────────────────────────────────────
    def _overlapping_bis(self) -> list[Bi]:
        """9 根交替方向、整体向上推进且互相重叠的笔（chan.py 特征序列可正常分段）。"""
        specs = [
            (1, "down", 0, 5, 11.0, 10.0),
            (2, "up", 5, 10, 11.5, 10.2),
            (3, "down", 10, 15, 11.3, 10.4),
            (4, "up", 15, 20, 12.0, 10.5),
            (5, "down", 20, 25, 11.6, 10.6),
            (6, "up", 25, 30, 12.4, 10.8),
            (7, "down", 30, 35, 12.0, 10.9),
            (8, "up", 35, 40, 12.8, 11.0),
            (9, "down", 40, 45, 12.2, 11.1),
        ]
        return [self._bi(*s) for s in specs]

    def test_detect_segments_returns_empty_when_bis_less_than_three(self):
        detector = SegmentDetector(
            bis=[self._bi(1, "up", 0, 5, 11.0, 9.0), self._bi(2, "down", 6, 10, 12.0, 10.0)]
        )
        self.assertEqual(detector.detect_segments(), [])

    def test_segments_cover_every_bi_without_gap_or_overlap(self):
        """线段必须首尾相接、无缝无重叠地把所有笔串起来。"""
        bis = self._overlapping_bis()
        segs = SegmentDetector(bis=bis).detect_segments()
        self.assertTrue(segs)
        self.assertEqual(segs[0].bi_ids[0], "bi_1")
        self.assertEqual(segs[-1].bi_ids[-1], "bi_9")
        for prev, cur in zip(segs, segs[1:]):
            self.assertEqual(prev.end, cur.start)
        # 每根笔都恰好归属一个线段
        flat = [bid for s in segs for bid in s.bi_ids]
        self.assertEqual(flat, [b.id for b in bis])

    def test_segment_high_low_spans_all_member_bis(self):
        bis = [
            self._bi(1, "up", 0, 5, 11.0, 9.0),
            self._bi(2, "down", 5, 10, 11.8, 9.5),
            self._bi(3, "up", 10, 15, 12.3, 9.8),
        ]
        segs = SegmentDetector(bis=bis).detect_segments()
        self.assertTrue(segs)
        for s in segs:
            member = [b for b in bis if b.id in s.bi_ids]
            self.assertAlmostEqual(s.high, max(b.high for b in member))
            self.assertAlmostEqual(s.low, min(b.low for b in member))

    def test_break_algo_is_available(self):
        bis = [
            self._bi(1, "up", 0, 5, 11.0, 9.0),
            self._bi(2, "down", 5, 10, 11.8, 9.5),
            self._bi(3, "up", 10, 15, 12.3, 9.8),
        ]
        segs = SegmentDetector(bis=bis).detect_segments(algo="break")
        self.assertIsInstance(segs, list)

    # ── 中枢：由连续次级别（笔）的重叠区域构成 ────────────────
    def test_zhongshu_comes_from_overlapping_bis_not_segments(self):
        """
        chan.py 口径：中枢由连续若干笔的重叠区间构成（段内中枢不跨段），
        而不是旧实现里「三个线段围一块」。
        """
        detector = SegmentDetector(bis=self._overlapping_bis())
        detector.detect_segments()
        zs_list = detector.detect_zhongshus()
        self.assertEqual(len(zs_list), 1)
        zs = zs_list[0]
        # ZD = max(low)、ZG = min(high)，且必须由若干连续笔构成
        self.assertAlmostEqual(zs.range_low, 10.6)
        self.assertAlmostEqual(zs.range_high, 11.3)
        self.assertAlmostEqual(zs.center, (10.6 + 11.3) / 2)
        self.assertEqual(zs.bi_ids, ["bi_3", "bi_4", "bi_5", "bi_6", "bi_7"])
        self.assertEqual(zs.bi_cnt, 5)

    def test_zhongshu_not_created_when_bis_do_not_overlap(self):
        bis = [
            self._bi(1, "down", 0, 5, 11.0, 10.0),
            self._bi(2, "up", 5, 10, 20.0, 18.0),
            self._bi(3, "down", 10, 15, 17.0, 15.0),
        ]
        detector = SegmentDetector(bis=bis)
        detector.detect_segments()
        self.assertEqual(detector.detect_zhongshus(), [])

    def test_zhongshu_range_is_never_inverted(self):
        detector = SegmentDetector(bis=self._overlapping_bis())
        detector.detect_segments()
        zs_list = detector.detect_zhongshus()
        self.assertTrue(zs_list)
        for zs in zs_list:
            self.assertLess(zs.range_low, zs.range_high)

    def test_get_zhongshu_for_price_returns_latest_matching_zone(self):
        detector = SegmentDetector(bis=self._overlapping_bis())
        detector.detect_segments()
        zs_list = detector.detect_zhongshus()
        self.assertTrue(zs_list)
        hit = SegmentDetector.get_zhongshu_for_price(zs_list, zs_list[0].range_low)
        self.assertIsNotNone(hit)
        self.assertIsNone(SegmentDetector.get_zhongshu_for_price(zs_list, 9999.0))

    # ── CZS 原语 ────────────────────────────────────────────
    def test_czs_range_and_peak_tracking(self):
        lines = build_lines([
            self._bi(1, "down", 0, 5, 11.0, 10.0),
            self._bi(2, "up", 5, 10, 11.5, 10.2),
        ])
        zs = CZS(lines, is_sure=True)
        self.assertAlmostEqual(zs.low, 10.2)    # max(low)
        self.assertAlmostEqual(zs.high, 11.0)   # min(high)
        self.assertAlmostEqual(zs.peak_low, 10.0)
        self.assertAlmostEqual(zs.peak_high, 11.5)
        self.assertFalse(zs.is_one_bi_zs())

    def test_czs_try_add_to_end_only_when_in_range(self):
        lines = build_lines([
            self._bi(1, "down", 0, 5, 11.0, 10.0),
            self._bi(2, "up", 5, 10, 11.5, 10.2),
            self._bi(3, "down", 10, 15, 20.0, 19.0),   # 完全落在区间之外
        ])
        zs = CZS(lines[:2], is_sure=True)
        self.assertFalse(zs.try_add_to_end(lines[2]))
        self.assertEqual(zs.end_bi.idx, 1)


if __name__ == "__main__":
    unittest.main()
