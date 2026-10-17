"""
买卖点配置（BSPointConfig）模块：缠论链路中「中枢 -> 买卖点」这一环的算法参数。

CPointConfig 是"单侧"的一份配置（买侧或卖侧各一份），CBSPointConfig 把这两份打包在一起，
因为同一个标的的买点判据和卖点判据常常要分别调（例如涨得猛的票卖点条件要放宽）。

链路位置：
    原始K线 -> 包含合并 -> 分型 -> 笔 -> 线段 -> 中枢 -> 【本模块：买卖点算法参数】

Note:
    - 配置的默认值来自 ChanConfig.set_bsp_config：先铺一份默认参数表，
      再用 conf 里带 "-buy"/"-sell"/"-segbuy"/"-segsell"/"-seg" 后缀的键做定向覆盖，
      最后统一调 parse_target_type() 把 "1,2,3a" 这类字符串解析成 BSP_TYPE 列表。
    - 笔级买卖点用 bs_point_conf，线段级买卖点用 seg_bs_point_conf；后者在默认表之上再覆写两项：
      macd_algo 改成 slope（线段跨度大，斜率比峰值稳）、bsp1_only_multibi_zs 改成 False
      （线段级别允许单笔中枢出1买）。
    - 参数的实际消费方是 BuySellPoint/BSPointList.py 与 ZS/ZS.py（背驰判定）。
"""
from typing import Dict, List, Optional

from ..Common.CEnum import BSP_TYPE, MACD_ALGO
from ..Common.func_util import _parse_inf


class CBSPointConfig:
    """
    买卖点配置的容器：同时持有买侧（b_conf）与卖侧（s_conf）两套 CPointConfig。

    Note:
        两套配置的字段完全一样、只是取值可以不同；业务代码不直接访问 b_conf/s_conf，
        一律通过 GetBSConfig(is_buy) 取，避免把"买用卖的配置"这类错误散落到各处。
    """

    def __init__(self, **args):
        """按同一份入参造出买/卖两套配置。

        要点：
        - 这里买/卖共享入参，构造阶段两边一定相同；要让两边不同得在构造之后
          用 b_conf.set() / s_conf.set() 单独改（ChanConfig.set_bsp_config 就是这么做的）
        """
        self.b_conf = CPointConfig(**args)
        self.s_conf = CPointConfig(**args)

    def GetBSConfig(self, is_buy):
        """按买卖方向取对应的那份配置（True 取买侧 b_conf，False 取卖侧 s_conf）。"""
        return self.b_conf if is_buy else self.s_conf


class CPointConfig:
    """
    单侧（买或卖）的买卖点算法参数集合。

    各项含义（默认值见 ChanConfig.set_bsp_config）：
        divergence_rate       ：背驰力度比阈值，判据是"出中枢笔力度 / 进中枢笔力度 <= 该值"；
                                值越小越严格，>100 时 ZS.is_divergence 走"保送"分支：
                                只要突破中枢就算背驰，完全跳过力度比较。默认 float("inf") 即不限
        min_zs_cnt            ：1类买卖点要求线段内至少几个中枢，<=0 不做限制。默认 1
        bsp1_only_multibi_zs  ：统计中枢个数时是否只数"非一笔中枢"，默认 True；
                                线段级配置被改成 False，因为线段内中枢本来就少
        max_bs2_rate          ：2类/类二允许的最大回撤比例，取值 (0, 1]，默认 0.9999（几乎不限）；
                                调小则只认"浅回撤"，例如 0.5 表示回撤不到一半才算
        macd_algo             ：力度口径（area/peak/full_area/diff/slope/amp/amount/volumn/
                                amount_avg/volumn_avg/turnrate_avg/rsi），默认 "peak"，见 SetMacdAlgo
        bs1_peak              ：1类是否要求"出中枢那笔创了这段行情的极值"，默认 True
        bs_type               ：要算的买卖点类型（逗号分隔字符串或字符串列表），
                                合法元素只有 '1'/'1p'/'2'/'2s'/'3a'/'3b'，默认六类全开
        bsp2_follow_1         ：2类是否必须跟在已有1类之后（1类那笔上没有买卖点就不出2类），默认 True
        bsp3_follow_1         ：3类是否必须跟在已有1类之后，默认 True
        bsp3_peak             ：T3A 是否额外要求"突破中枢极值"，默认 False
        bsp2s_follow_2        ：类二是否必须先有2类（2类没成立时是否还往后找类二），默认 False
        max_bsp2s_lv          ：类二允许的最大层级（第 n 个类二层级为 n），None 表示不限，默认 None
        strict_bsp3           ：3类是否要求中枢的进/出笔与1类那笔严格相邻，默认 False
        bsp3a_max_zs_cnt      ：T3A 最多考察下一段里的前几个中枢（>=1），默认 1（只看第一个）

    Note:
        构造时只校验两项（max_bs2_rate <= 1、bsp3a_max_zs_cnt >= 1），其余参数都是
        "用到时才生效"，取值范围由调用方保证；bs_type 只是暂存的原始字符串，
        必须再调 parse_target_type() 才会变成判定逻辑真正使用的 target_types 列表。
    """

    def __init__(self,
                 divergence_rate,
                 min_zs_cnt,
                 bsp1_only_multibi_zs,
                 max_bs2_rate,
                 macd_algo,
                 bs1_peak,
                 bs_type,
                 bsp2_follow_1,
                 bsp3_follow_1,
                 bsp3_peak,
                 bsp2s_follow_2,
                 max_bsp2s_lv,
                 strict_bsp3,
                 bsp3a_max_zs_cnt,
                 ):
        """保存单侧的买卖点参数，各项取值的含义见类文档。

        要点：
        - 只做两项校验：max_bs2_rate > 1 或 bsp3a_max_zs_cnt < 1 直接 assert 失败
          （回撤比例超过1等于不限制、一个中枢都不看则 T3A 永远不会产生，都属配置写错）
        - bs_type 原样暂存为 tmp_target_types，必须再调 parse_target_type() 才有 target_types
        - 所有参数都能在构造后用 set() 改写，唯独改了 bs_type 要重新 parse_target_type()
        """
        self.divergence_rate = divergence_rate
        self.min_zs_cnt = min_zs_cnt
        self.bsp1_only_multibi_zs = bsp1_only_multibi_zs
        self.max_bs2_rate = max_bs2_rate
        # 回撤比例超过1就没有筛选意义了（等于不限制），视为配置写错
        assert self.max_bs2_rate <= 1
        self.SetMacdAlgo(macd_algo)
        self.bs1_peak = bs1_peak
        # bs_type 只是"用户原始输入"的暂存，真正供判定使用的枚举列表要等 parse_target_type
        self.tmp_target_types = bs_type
        self.target_types: List[BSP_TYPE] = []
        self.bsp2_follow_1 = bsp2_follow_1
        self.bsp3_follow_1 = bsp3_follow_1
        self.bsp3_peak = bsp3_peak
        self.bsp2s_follow_2 = bsp2s_follow_2
        self.max_bsp2s_lv: Optional[int] = max_bsp2s_lv
        self.strict_bsp3 = strict_bsp3
        self.bsp3a_max_zs_cnt = bsp3a_max_zs_cnt
        # 一个都不看的话3A买卖点永远不会产生，属于无效配置
        assert self.bsp3a_max_zs_cnt >= 1

    def parse_target_type(self):
        """把 bs_type（逗号分隔字符串或字符串列表）解析成 BSP_TYPE 枚举列表，写入 target_types。

        要点：
        - 先过一遍白名单 '1'/'1p'/'2'/'2s'/'3a'/'3b'，拼错立刻 assert 失败；
          不校验的话后面会静默地一个买卖点都算不出来
        - 由 ChanConfig 在所有 set() 之后调用一次；在此之前 target_types 是空列表，
          所有类型都会被降级成非目标买卖点（1类仍会建出来，只作内部串联用）
        """
        _d: Dict[str, BSP_TYPE] = {x.value: x for x in BSP_TYPE}
        if isinstance(self.tmp_target_types, str):
            self.tmp_target_types = [t.strip() for t in self.tmp_target_types.split(",")]
        for target_t in self.tmp_target_types:
            # 白名单校验：类型名写错时立刻炸，否则后面的判定会静默地一个买卖点都算不出来
            assert target_t in ['1', '2', '3a', '2s', '1p', '3b']
        self.target_types = [_d[_type] for _type in self.tmp_target_types]

    def SetMacdAlgo(self, macd_algo):
        """把字符串形式的力度口径翻译成 MACD_ALGO 枚举，写进 self.macd_algo。

        要点：
        - 取值（值越大表示这笔越有力）：area 半面积（从笔的一端累加到柱子变色为止）/
          peak 同色柱峰值（最敏感，默认）/ full_area 同色柱面积之和 / diff 柱高极差 /
          slope 笔斜率（线段级买卖点默认用它）/ amp 笔涨跌幅（跨价位可比）/
          amount、volumn 成交额与成交量之和 / amount_avg、volumn_avg、turnrate_avg 三项均值 /
          rsi 笔上 RSI（向下笔已翻转成越大越强）
        - 所有口径统一"越大越强"是为了让买卖点处能用 out_metric/in_metric 直接比背驰；不同口径间不可比
        - 传入上表以外的名字会抛 KeyError
        """
        _d = {
            "area": MACD_ALGO.AREA,
            "peak": MACD_ALGO.PEAK,
            "full_area": MACD_ALGO.FULL_AREA,
            "diff": MACD_ALGO.DIFF,
            "slope": MACD_ALGO.SLOPE,
            "amp": MACD_ALGO.AMP,
            "amount": MACD_ALGO.AMOUNT,
            "volumn": MACD_ALGO.VOLUMN,
            "amount_avg": MACD_ALGO.AMOUNT_AVG,
            "volumn_avg": MACD_ALGO.VOLUMN_AVG,
            "turnrate_avg": MACD_ALGO.TURNRATE_AVG,
            "rsi": MACD_ALGO.RSI,
        }
        self.macd_algo = _d[macd_algo]

    def set(self, k, v):
        """改写单个参数（配置装载 / 回测调参的入口）。

        要点：
        - 实现是 exec(f"self.{k} = {v}")，v 被当成源码字面量求值：传字符串要自带引号，
          "inf" 会先被 _parse_inf 转成 float("inf")；写成没引号的字符串会抛 NameError/SyntaxError
        - macd_algo 走 SetMacdAlgo 翻译分支（取值非法时 KeyError），其余参数直接赋值
        - 用 exec 而非 setattr 是为了让 inf、表达式这类写法也能直接用；
          代价是键名与值都不做白名单校验，前提是配置文件可信
        """
        v = _parse_inf(v)
        if k == "macd_algo":
            self.SetMacdAlgo(v)
        else:
            exec(f"self.{k} = {v}")
