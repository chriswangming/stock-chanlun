"""
走势自动分类 — 上涨/下跌/盘整
"""
from newchan.Seg.Seg import CSeg
from newchan.ZS.ZS import CZS


class WaveClassifier:
    """
    走势分类:
    - 上涨: 连续向上段 + 不创新低
    - 下跌: 连续向下段 + 不创新高
    - 盘整: 中枢震荡，无明确方向

    Note:
        入参要「同级配对」：``segments`` 取某个级别的线段列表
        （``CKLine_List.seg_list``），``zhongshus`` 就取同一级的线段中枢列表
        （``CKLine_List.segzs_list``）；反过来用笔列表时就得配笔中枢列表
        （``bi_list`` / ``zs_list``）。跨级混喂会让"价格 vs 中枢"的比较失去意义。
    """

    def classify(self, segments: list[CSeg],
                 zhongshus: list[CZS],
                 current_price: float) -> dict:
        """
        返回分类结果及置信度
        """
        if not segments:
            return {"trend": "未知", "confidence": 0.0, "description": "数据不足"}

        recent = segments[-3:] if len(segments) >= 3 else segments

        up_count = sum(1 for s in recent if s.is_up())
        down_count = sum(1 for s in recent if s.is_down())

        if not zhongshus:
            # 无中枢，纯方向判断
            if up_count > down_count:
                return {
                    "trend": "上涨",
                    "confidence": round(up_count / len(recent), 2),
                    "description": f"近{len(recent)}段中{up_count}段向上"
                }
            elif down_count > up_count:
                return {
                    "trend": "下跌",
                    "confidence": round(down_count / len(recent), 2),
                    "description": f"近{len(recent)}段中{down_count}段向下"
                }
            else:
                return {"trend": "盘整", "confidence": 0.5,
                        "description": "多空均衡"}

        # 有中枢: 判断价格相对中枢位置
        last_zs = zhongshus[-1]

        if current_price > last_zs.high:
            direction_score = up_count / len(recent)
            return {
                "trend": "上涨",
                "confidence": round(min(1.0, direction_score + 0.2), 2),
                "description": f"价格{current_price:.2f}在{last_zs.high:.2f}之上"
            }
        elif current_price < last_zs.low:
            direction_score = down_count / len(recent)
            return {
                "trend": "下跌",
                "confidence": round(min(1.0, direction_score + 0.2), 2),
                "description": f"价格{current_price:.2f}在{last_zs.low:.2f}之下"
            }
        else:
            # 在中枢内震荡
            return {
                "trend": "盘整",
                "confidence": 0.6,
                "description": f"价格在中枢[{last_zs.low:.2f},{last_zs.high:.2f}]内"
            }

    def multi_level_resonance(self, level_results: list[dict]) -> dict:
        """
        多级别共振:
        输入: [{'trend': '上涨', 'level': 'daily'}, ...]
        返回共振结果
        """
        if len(level_results) < 2:
            return {"共振": False, "levels": []}

        directions = [r.get('trend') for r in level_results]
        buy_count = directions.count('上涨')
        sell_count = directions.count('下跌')

        if buy_count >= 2:
            return {
                "共振": True,
                "direction": "买入",
                "levels": [r['level'] for r in level_results],
                "description": f"{buy_count}个级别共振上涨"
            }
        elif sell_count >= 2:
            return {
                "共振": True,
                "direction": "卖出",
                "levels": [r['level'] for r in level_results],
                "description": f"{sell_count}个级别共振下跌"
            }
        return {"共振": False, "levels": [], "description": "无共振"}
