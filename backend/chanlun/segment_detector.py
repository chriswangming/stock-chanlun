"""
线段 + 中枢检测器 —— 对外 API 层。

内部的线段 / 中枢构件（Line、CSeg、Eigen、EigenFX、SegListComm、SegListChan、
SegListDef、CZS、CZSList，以及配套的 FindPeakBi / cal_seg / update_zs_in_seg）
都放在 chanlun/base.py（对应 chan.py 的 Seg/、ZS/、KLine 目录）；本模块只负责
按配置挑算法、驱动它们，并把结果转成对外的 XianSegment / Zhongshu 模型。

与 chan.py 的结构性差异：chan.py 是流式增量计算（每根新K线触发一次重算），
这里改为批处理：一次性拿到全部笔后跑同样的算法。因此 chan.py 中专门为流式服务的
部分（do_init 裁剪不确定尾部、虚笔 "virtual"、calibration `last_sure_pos` 增量优化）
被简化掉 —— 批处理每次都是全量重算，天然等价于 chan.py 跑到最后一根K线的状态。
"""

from __future__ import annotations

from typing import Optional

from .base import (
    Line, SegListChan, SegListComm, SegListDef, CZSList,
    build_lines, cal_seg, update_zs_in_seg,
)
from .config import ChanlunConfig, SegConfig, ZSConfig
from .elements import Bi, XianSegment, Zhongshu


# ══════════════════════════════════════════════════════════════
#  对外 API
# ══════════════════════════════════════════════════════════════
class SegmentDetector:
    """
    线段 + 中枢检测器。

    线段采用 chan.py 的特征序列法（默认）或线段破坏法；
    中枢采用 chan.py 的 normal 段内中枢：由奇数个连续次级别重叠区域构成，
    且同样方向的中枢可以延伸 / 合并 —— 而不是旧实现里「三个线段围一块」。
    """

    def __init__(self, bis: list[Bi], config: Optional[ChanlunConfig] = None,
                 macd_series=None):
        self.bis = list(bis)
        self.config = config or ChanlunConfig()
        self.macd_series = macd_series
        self._lines = build_lines(self.bis, macd_series)
        self._seg_list: Optional[SegListComm] = None
        self._zs_list: Optional[CZSList] = None

    # ── 内部结构（买卖点判定需要笔序列/线段/段内中枢） ──
    @property
    def lines(self) -> list[Line]:
        return self._lines

    @property
    def seg_list(self) -> Optional[SegListComm]:
        return self._seg_list

    # ── 线段 ─────────────────────────────────────
    def detect_segments(self, algo: Optional[str] = None, **_deprecated) -> list[XianSegment]:
        """
        algo 取自 SegConfig.seg_algo；旧签名里的 min_overlap_bis / max_iterations
        属于已被替换的「三笔重叠成段」实现，这里忽略。
        """
        algo = algo or self.config.seg.seg_algo
        if algo == "chan":
            seg_list: SegListComm = SegListChan(self.config.seg)
        elif algo == "break":
            seg_list = SegListDef(self.config.seg)
        else:
            raise NotImplementedError(f"seg_algo={algo} 暂未实现（支持 chan/break）")

        seg_list.update(self._lines)
        cal_seg(self._lines, seg_list)
        self._seg_list = seg_list
        return self._to_segment_models(seg_list)

    def _to_segment_models(self, seg_list: SegListComm) -> list[XianSegment]:
        out: list[XianSegment] = []
        for seg in seg_list:
            out.append(
                XianSegment(
                    id=f"xian_{len(out) + 1}",
                    start=seg.start_bi.start_date,
                    end=seg.end_bi.end_date,
                    direction="up" if seg.is_up() else "down",
                    high=max(b._high() for b in seg.bi_list),
                    low=min(b._low() for b in seg.bi_list),
                    start_price=float(seg.get_begin_val()),
                    end_price=float(seg.get_end_val()),
                    bi_ids=[b.bi_id for b in seg.bi_list],
                    level=2,
                    is_sure=bool(seg.is_sure),
                )
            )
        return out

    # ── 中枢 ─────────────────────────────────────
    def detect_zhongshus(self, segments: Optional[list[XianSegment]] = None,
                         zs_algo: Optional[str] = None) -> list[Zhongshu]:
        """
        中枢计算依赖线段的划分结果（段内中枢不跨段），因此必须同时拿到线段。
        若未完成线段划分会先自动跑一遍。
        """
        if self._seg_list is None:
            self.detect_segments()
        assert self._seg_list is not None

        cfg = self.config.zs
        if zs_algo and zs_algo != cfg.zs_algo:
            cfg = ZSConfig(need_combine=cfg.need_combine,
                           zs_combine_mode=cfg.zs_combine_mode,
                           one_bi_zs=cfg.one_bi_zs, zs_algo=zs_algo)

        zs_list = CZSList(cfg)
        zs_list.cal_bi_zs(self._lines, self._seg_list)
        update_zs_in_seg(self._lines, self._seg_list, zs_list)
        self._zs_list = zs_list
        return self._to_zs_models(zs_list)

    def _to_zs_models(self, zs_list: CZSList) -> list[Zhongshu]:
        seg_items = list(self._seg_list or [])
        out: list[Zhongshu] = []
        for zs in zs_list:
            # 中枢所属线段：chan.py 的 update_zs_in_seg 用同样的包含判定
            xian_ids = [f"xian_{i + 1}" for i, seg in enumerate(seg_items) if zs.is_inside(seg)]
            out.append(
                Zhongshu(
                    id=f"zs_{len(out) + 1}",
                    start=zs.begin,
                    end=zs.end,
                    range_high=float(zs.high),
                    range_low=float(zs.low),
                    xian_ids=xian_ids,
                    bi_ids=[b.bi_id for b in zs.bi_lst],
                    level=2,
                    center=float(zs.mid),
                    is_sure=bool(zs.is_sure),
                    bi_cnt=zs.uniq_bi_cnt(),
                )
            )
        return out
