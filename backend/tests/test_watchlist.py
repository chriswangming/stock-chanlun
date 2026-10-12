"""自选股持久化与行情降级回归测试。

历史上这里踩过一次：前端按 6 位补零写入 '000700'，读取时又被 zfill(6)，
写读口径不一致导致富途批量快照整批报错，接口把整个自选列表返回成空数组，
前端照单全收 → 用户一刷新自选股就全没了。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from routers import watchlist as wl_router
from stores import local_json


class NormalizeCodeTests(unittest.TestCase):
    def test_various_inputs(self):
        f = local_json._normalize_watchlist_code
        self.assertEqual(f("700"), "00700")
        self.assertEqual(f("00700"), "00700")
        self.assertEqual(f("000700"), "00700")   # 前端 padStart(6) 造出来的脏形态
        self.assertEqual(f("HK.00700"), "00700")
        self.assertEqual(f(" 09927 "), "09927")
        self.assertEqual(f("009927"), "09927")
        self.assertEqual(f(""), "")
        self.assertEqual(f("BTCUSDT"), "BTCUSDT")  # 非纯数字原样保留

    def test_write_and_read_roundtrip(self):
        """写入与读取必须用同一套归一化，否则磁盘上会出现同一只股票的两个键。"""
        f = local_json._normalize_watchlist_code
        self.assertEqual(f(f("700")), f("700"))
        self.assertEqual(f(f("000700")), f("00700"))


class LoadFromDiskTests(unittest.TestCase):
    def test_dirty_codes_are_merged(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "watchlist.json"
            path.write_text(
                json.dumps({
                    "000700": "2026-10-12T18:30:51",
                    "00700": "2026-10-12T18:33:51",
                    "09927": "2026-10-12T18:33:12",
                    "01024": "2026-10-12T18:34:14",
                }),
                encoding="utf-8",
            )
            with mock.patch.object(local_json, "_WATCHLIST_FILE", path):
                loaded = local_json._load_watchlist_from_disk()
            # 000700 与 00700 是同一只，去重后只剩 3 条
            self.assertEqual(sorted(loaded), ["00700", "01024", "09927"])
            # 撞键时保留更早的加入时间
            self.assertEqual(loaded["00700"], "2026-10-12T18:30:51")

    def test_migration_rewrites_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "watchlist.json"
            path.write_text(json.dumps({"000700": "t1"}), encoding="utf-8")
            saved = local_json.get_watchlist_map()
            self.addCleanup(lambda: local_json._watchlist.update(saved))
            with mock.patch.object(local_json, "_WATCHLIST_FILE", path):
                local_json.init_stores()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"00700": "t1"})


class WatchlistResponseTests(unittest.TestCase):
    """行情拉不到时，自选列表本身绝不能被清空。"""

    def setUp(self):
        wl_router.watchlist_quote_cache.clear()
        self.codes = {"00700": "t1", "01024": "t2"}
        patcher = mock.patch.object(wl_router, "get_watchlist_map", lambda: dict(self.codes))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(wl_router.watchlist_quote_cache.clear)

    def _quote_df(self, codes):
        table = {
            "00700": ("腾讯控股", 425.8, 0.24),
            "01024": ("快手-W", 29.26, -2.79),
        }
        rows = []
        for c in codes:
            if c in table:
                name, price, pct = table[c]
                rows.append({"代码": c, "名称": name, "最新价": price, "涨跌幅": pct})
        return pd.DataFrame(rows)

    def test_all_quotes_ok(self):
        with mock.patch.object(wl_router, "get_realtime_quote", self._quote_df):
            res = wl_router._get_watchlist_response()
        self.assertTrue(res["quote_ok"])
        self.assertEqual([s["code"] for s in res["stocks"]], ["00700", "01024"])
        self.assertEqual(res["stocks"][0]["name"], "腾讯控股")

    def test_keeps_stocks_when_batch_quote_fails(self):
        """整批失败（富途不认其中某个代码）→ 逐只兜底，列表仍要完整。"""
        calls: list[list[str]] = []

        def fake(codes):
            calls.append(list(codes))
            if len(codes) > 1:
                return pd.DataFrame()          # 批量整批失败
            return self._quote_df(codes)       # 单只成功

        with mock.patch.object(wl_router, "get_realtime_quote", fake):
            res = wl_router._get_watchlist_response()
        self.assertEqual([s["code"] for s in res["stocks"]], ["00700", "01024"])
        self.assertTrue(all(s["quote_ok"] for s in res["stocks"]))
        self.assertTrue(any(len(c) == 1 for c in calls), "整批失败后应退回逐只拉取")

    def test_placeholder_rows_when_nothing_available(self):
        """行情完全不可用时，股票条目仍在（价格占位），而不是返回空数组。"""
        with mock.patch.object(wl_router, "get_realtime_quote", lambda codes: pd.DataFrame()):
            res = wl_router._get_watchlist_response()
        self.assertEqual(res["total"], 2)
        self.assertEqual([s["code"] for s in res["stocks"]], ["00700", "01024"])
        self.assertFalse(res["quote_ok"])
        self.assertTrue(all(s["quote_ok"] is False for s in res["stocks"]))

    def test_partial_failure_keeps_unavailable_rows(self):
        """一只拉不到、一只拉得到时，两条都要在列表里。"""
        def fake(codes):
            return self._quote_df([c for c in codes if c == "00700"])

        with mock.patch.object(wl_router, "get_realtime_quote", fake):
            res = wl_router._get_watchlist_response()
        by_code = {s["code"]: s for s in res["stocks"]}
        self.assertEqual(set(by_code), {"00700", "01024"})
        self.assertTrue(by_code["00700"]["quote_ok"])
        self.assertFalse(by_code["01024"]["quote_ok"])

    def test_empty_watchlist(self):
        with mock.patch.object(wl_router, "get_watchlist_map", lambda: {}):
            res = wl_router._get_watchlist_response()
        self.assertEqual(res["stocks"], [])


if __name__ == "__main__":
    unittest.main()
