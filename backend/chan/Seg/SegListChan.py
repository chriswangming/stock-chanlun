"""
线段算法之一：缠论原生的「特征序列法」（seg_algo='chan'，也是默认算法）。

判定的核心是特征序列（见 Seg/EigenFX.py）：
    上升线段的内部是那些向下笔，它们两两做包含处理后形成"特征序列元素"，
    当这个序列出现顶分型时，这条上升线段就结束了；下降线段对称处理。

链路位置：
    原始K线 -> 包含合并 -> 分型 -> 笔 -> 【本模块：特征序列法切线段】 -> 中枢 -> 买卖点

Note:
    本模块只负责"怎么切"，容器与补收尾逻辑都在 Seg/SegListComm.py 里。
"""
from ..Bi.BiList import CBiList
from ..Common.CEnum import BI_DIR, SEG_TYPE

from .EigenFX import CEigenFX
from .SegConfig import CSegConfig
from .SegListComm import CSegListComm


class CSegListChan(CSegListComm):
    """
    用特征序列切分线段的线段列表实现。

    Note:
        与另外两种算法（SegListDYH / SegListDef）相比，它最贴近缠论原文，
        代价是要维护特征序列的合并与缺口判定，实现最复杂。
    """

    def __init__(self, seg_config=CSegConfig(), lv=SEG_TYPE.BI):
        """按线段配置与层级语义构造特征序列法的线段列表。"""
        super(CSegListChan, self).__init__(seg_config=seg_config, lv=lv)

    def do_init(self):
        """重算前的清理：把尾部未确认的线段撤掉，让它们有机会被重新算出来。

        要点：
        - 最后一条确认线段也可能要撤：判定它的那个特征序列的第三元素里若含有未确认笔，
          这条线段的端点其实还可能变动，必须先撤掉重算
        - 撤掉线段时要把段内笔的 parent_seg 反向指针一并清掉
        """
        while len(self) and not self.lst[-1].is_sure:
            _seg = self[-1]
            for bi in _seg.bi_list:
                bi.parent_seg = None
            if _seg.pre:
                _seg.pre.next = None
            self.lst.pop()
        # 最后一条确认线段的分形第三元素若含未确认笔，端点还可能变，也要撤掉重算
        if len(self):
            assert self.lst[-1].eigen_fx and self.lst[-1].eigen_fx.ele[-1]
            if not self.lst[-1].eigen_fx.ele[-1].lst[-1].is_sure:
                # 如果确定线段的分形的第三元素包含不确定笔，也需要重新算，不然线段分形元素的高低点可能不对
                self.lst.pop()

    def update(self, bi_lst: CBiList):
        """重算线段列表（特征序列法对基类 update 的实现）。

        要点：
        - 因为有补收尾兜底（collect_left_seg），可以放心只从"上一条确认线段的终点笔"之后开始扫描
        - 还没有任何线段时从第 0 根笔开始
        """
        self.do_init()
        if len(self) == 0:
            self.cal_seg_sure(bi_lst, begin_idx=0)
        else:
            self.cal_seg_sure(bi_lst, begin_idx=self[-1].end_bi.idx+1)
        self.collect_left_seg(bi_lst)

    def cal_seg_sure(self, bi_lst: CBiList, begin_idx: int):
        """用两个方向的特征序列容器并行推进，直到有线段被确定出来。

        要点：
        - 两个容器的分工（缠论特征序列法的标准约定）：
          up_eigen 装向下笔、出现顶分型 => 上升线段结束；
          down_eigen 装向上笔、出现底分型 => 下降线段结束
        - 只把笔喂给"当前方向之外的那个容器"：上一条线段向上时，接下来要找的是下降线段
        - 两个容器并行推进，谁先走出分型谁先产出线段
        - 第一段方向未知时两边都喂，等证据够了再定方向（见下面那段分支）
        """
        up_eigen = CEigenFX(BI_DIR.UP, lv=self.lv)  # 上升线段下降笔
        down_eigen = CEigenFX(BI_DIR.DOWN, lv=self.lv)  # 下降线段上升笔
        last_seg_dir = None if len(self) == 0 else self[-1].dir
        for bi in bi_lst[begin_idx:]:
            fx_eigen = None
            # 只把笔喂给「当前不是这个方向」的那个容器：
            # 上一条线段向上时，接下来要找的是下降线段，其特征序列由向上笔构成
            if bi.is_down() and last_seg_dir != BI_DIR.UP:
                if up_eigen.add(bi):
                    fx_eigen = up_eigen
            elif bi.is_up() and last_seg_dir != BI_DIR.DOWN:
                if down_eigen.add(bi):
                    fx_eigen = down_eigen
            if len(self) == 0:  # 尝试确定第一段方向，不要以谁先成为分形来决定，反例：US.EVRG
                # 第一段方向的判定：不要以「谁先成分为准」（反例 US.EVRG），
                # 而是看哪个方向的特征序列已经攒到第二个元素：向下笔走得足够开 -> 趋势向下
                if up_eigen.ele[1] is not None and bi.is_down():
                    last_seg_dir = BI_DIR.DOWN
                    down_eigen.clear()
                elif down_eigen.ele[1] is not None and bi.is_up():
                    up_eigen.clear()
                    last_seg_dir = BI_DIR.UP
                # 证据和当前笔的方向矛盾时，把方向判定退回「未定」，继续观察
                if up_eigen.ele[1] is None and last_seg_dir == BI_DIR.DOWN and bi.dir == BI_DIR.DOWN:
                    last_seg_dir = None
                elif down_eigen.ele[1] is None and last_seg_dir == BI_DIR.UP and bi.dir == BI_DIR.UP:
                    last_seg_dir = None

            if fx_eigen:
                self.treat_fx_eigen(fx_eigen, bi_lst)
                break

    def treat_fx_eigen(self, fx_eigen, bi_lst: CBiList):
        """处理一个已经成型的特征序列分型：据此切出一条线段。

        要点：
        - can_be_end 的返回值三态：True 分型成立（有缺口时后面也确实找到了反向分型）=> 线段确认；
          None 分型成立但后面还没走出反向分型 => 只能算未确认线段；
          False 分型不算数 => 从它的第二元素所在位置重算
        - 线段是否确认还要看构成它的笔是否都已确认（all_bi_is_sure）
        - 首条线段方向/首尾值异常被 add_new_seg 吞掉时（返回 False），从该笔之后重新来过
        """
        _test = fx_eigen.can_be_end(bi_lst)
        end_bi_idx = fx_eigen.GetPeakBiIdx()
        # 分型成立（或还没找到反向分型）-> 按它切线段；否则丢掉它从第二元素重算
        if _test in [True, None]:  # None表示反向分型找到尾部也没找到
            is_true = _test is not None  # 如果是正常结束
            if not self.add_new_seg(bi_lst, end_bi_idx, is_sure=is_true and fx_eigen.all_bi_is_sure()):  # 防止第一根线段的方向与首尾值异常
            # 首线段方向/首尾值异常被 add_new_seg 吞掉了 -> 从这条笔之后重新来过
                self.cal_seg_sure(bi_lst, end_bi_idx+1)
                return
            self.lst[-1].eigen_fx = fx_eigen
            if is_true:
                self.cal_seg_sure(bi_lst, end_bi_idx + 1)
        else:
            # 注意这里取的是 lst[1]（喂进来的第二根笔），而不是 ele[1]（第二个特征序列元素）
            self.cal_seg_sure(bi_lst, fx_eigen.lst[1].idx)
