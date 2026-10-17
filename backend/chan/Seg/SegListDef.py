"""
线段算法之一：「线段破坏法」（seg_algo='break'，已不推荐使用）。

注意：本文件的实现目前整体处于注释停用状态（KLine_List 的线段工厂也不再导入它），
只保留代码原文作为算法参考，新代码请用 Seg/SegListChan.py（特征序列法）。

算法思路：不构造特征序列，而是直接看"后面那笔有没有破坏前面那笔的端点" ——
破坏发生之前把线段一路延长，直到出现反向的破坏，才在极值笔处收一条线段。
与特征序列法的区别：不做包含合并、不判缺口，所以更粗糙，震荡行情里更容易多切线段。

链路位置：
    原始K线 -> 包含合并 -> 分型 -> 笔 -> 【本模块：线段破坏法切线段】 -> 中枢 -> 买卖点
"""
from Bi.BiList import CBiList
from Common.CEnum import SEG_TYPE

from .SegConfig import CSegConfig
from .SegListComm import CSegListComm


def is_up_seg(bi, pre_bi):
    """
    判断 bi 是否"破坏"了前面隔一根的笔 pre_bi 的高点。

    要点：
    - bi 的高点更高则为 True
    """
    return bi._high() > pre_bi._high()


def is_down_seg(bi, pre_bi):
    """
    判断 bi 是否"破坏"了前面隔一根的笔 pre_bi 的低点。

    要点：
    - bi 的低点更低则为 True
    """
    return bi._low() < pre_bi._low()


class CSegListDef(CSegListComm):
    """
    用线段破坏法切分线段的线段列表实现（seg_algo='break'）。

    要点：
    - 算法已不再维护，新代码请用 SegListChan（seg_algo='chan'）
    """

    def __init__(self, seg_config=CSegConfig(), lv=SEG_TYPE.BI):
        """
        按配置与层级语义构造线段破坏法的线段列表。

        要点：
        - sure_seg_update_end 表示"确认线段的终点还允许往后延伸"，本实现恒为 False，
          即确认线段切出之后就不再被改动
        """
        super(CSegListDef, self).__init__(seg_config=seg_config, lv=lv)
        self.sure_seg_update_end = False

    def update(self, bi_lst: CBiList):
        """
        重算线段列表（线段破坏法对基类 update 的实现）。

        要点：
        - 与另外两种算法一样，最后必须用 collect_left_seg 补收尾，
          否则最新那截凑不满线段的笔会被整个丢掉
        """
        self.do_init()
        self.cal_bi_sure(bi_lst)
        self.collect_left_seg(bi_lst)

    def update_last_end(self, bi_lst, new_endbi_idx: int):
        """
        把最后一条线段的终点笔往后挪到 new_endbi_idx。

        要点：
        - 断言要求新终点至少比旧终点大 2：中间至少要跨过两根笔，
          延伸之后线段的方向才不会翻转
        - update_bi_list 是 append 语义且区间含两端，所以旧终点笔会被再登记一次
        """
        last_endbi_idx = self[-1].end_bi.idx
        assert new_endbi_idx >= last_endbi_idx + 2
        self[-1].end_bi = bi_lst[new_endbi_idx]
        self.lst[-1].update_bi_list(bi_lst, last_endbi_idx, new_endbi_idx)

    def cal_bi_sure(self, bi_lst):
        """
        扫描笔列表，边推进边在"被破坏"的位置切出线段。

        要点：
        - 主循环维护一个 peak_bi（当前方向上最极端的那根笔）：
          同方向又创新高/新低的笔直接把 peak_bi 换成它（线段跟着延长）；
          反向笔破坏了 peak_bi 的端点则说明这条线段该结束，
          就在 peak_bi 处切一条线段，再把 peak_bi 指向新的方向
        - 切段要求与 peak_bi 至少隔 2 根笔，太短的段不收
        - 循环结束后若 peak_bi 还挂着，说明最后的走势还没被破坏，以它收一条未确认线段
        """
        peak_bi = None
        if len(bi_lst) == 0:
            return
        for idx, bi in enumerate(bi_lst):
            if idx < 2:
                continue
            # 同方向又创新高/新低 -> 候选终点直接换成它
            if peak_bi and ((bi.is_up() and peak_bi.is_up() and bi._high() >= peak_bi._high()) or (bi.is_down() and peak_bi.is_down() and bi._low() <= peak_bi._low())):
                peak_bi = bi
                continue
            # 确认线段的终点若还允许延伸：同方向笔创了新极值就把线段终点挪过去
            if self.sure_seg_update_end and len(self) and bi.dir == self[-1].dir and ((bi.is_up() and bi._high() >= self[-1].end_bi._high()) or (bi.is_down() and bi._low() <= self[-1].end_bi._low())):
                self.update_last_end(bi_lst, bi.idx)
                peak_bi = None
                continue
            # 与前面隔一根的同向笔比较，看这一笔有没有破坏它的端点
            pre_bi = bi_lst[idx-2]
            if (bi.is_up() and is_up_seg(bi, pre_bi)) or \
               (bi.is_down() and is_down_seg(bi, pre_bi)):
                # 破坏发生了：先定下这条线段的候选终点，再决定要不要切
                if peak_bi is None:
                    if len(self) == 0 or bi.dir != self[-1].dir:
                        peak_bi = bi
                        continue
                # 方向已经翻转：说明上一条线段该收了，但至少要满 3 根笔才收
                elif peak_bi.dir != bi.dir:
                    if bi.idx - peak_bi.idx <= 2:
                        continue
                    self.add_new_seg(bi_lst, peak_bi.idx)
                    peak_bi = bi
                    continue
        # 最后还没被破坏的那部分，收成一条未确认线段
        if peak_bi is not None:
            self.add_new_seg(bi_lst, peak_bi.idx, is_sure=False)
