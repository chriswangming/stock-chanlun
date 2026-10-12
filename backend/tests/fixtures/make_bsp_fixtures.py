"""
生成 tests/fixtures/bsp_variants.json —— 买卖点对齐测试的 chan.py 基准。

用法（需要本机有 chan.py，默认路径见 CHAN_PY）：

    python tests/fixtures/make_bsp_fixtures.py

原理：把K线写成 chan.py 根目录下的 `{code}_day.csv`，用它的 CSV_API 跑一遍
CChan，再把 `bs_point_lst`（笔买卖点）dump 出来。临时 CSV 会自动删除。

注意 chan.py 的 CChanConfig 会用 ConfigWithCheck 就地消费传入的 dict，
同一份 conf 不能复用两次（第二次会退化成默认配置）—— 每次都要 dict(conf)。
"""
import json
import os
import sys

CHAN_PY = os.environ.get("CHAN_PY_PATH", "/Users/chriswang/workspace/chan.py")
HERE = os.path.dirname(os.path.abspath(__file__))

# 需要 Python 3.10+（chan.py 本身的要求）
sys.path.insert(0, CHAN_PY)

import pandas as pd  # noqa: E402

from Chan import CChan  # noqa: E402
from ChanConfig import CChanConfig  # noqa: E402
from Common.CEnum import AUTYPE, DATA_SRC, KL_TYPE  # noqa: E402

VARIANTS = {
    "default": {},
    "min_zs0": {"min_zs_cnt": 0},
    "area": {"macd_algo": "area", "min_zs_cnt": 0},
    "full_area": {"macd_algo": "full_area", "min_zs_cnt": 0},
    "diff": {"macd_algo": "diff", "min_zs_cnt": 0},
    "slope": {"macd_algo": "slope", "min_zs_cnt": 0},
    "amp": {"macd_algo": "amp", "min_zs_cnt": 0},
    "bsp3": {"min_zs_cnt": 0, "bsp3a_max_zs_cnt": 3, "bsp3_peak": True},
    "strict3": {"min_zs_cnt": 0, "strict_bsp3": True, "bsp3a_max_zs_cnt": 3},
    "div09": {"divergence_rate": 0.9, "min_zs_cnt": 0},
    "allzs": {"min_zs_cnt": 0, "bsp1_only_multibi_zs": False},
    "no_bs1peak": {"min_zs_cnt": 0, "bs1_peak": False},
    "bsp2s": {"min_zs_cnt": 0, "bsp2s_follow_2": True, "max_bsp2s_lv": 3},
    "nofollow": {"min_zs_cnt": 0, "bsp2_follow_1": False, "bsp3_follow_1": False},
    "only1": {"bs_type": "1,1p", "min_zs_cnt": 0},
    "only3": {"bs_type": "3a,3b", "min_zs_cnt": 0, "bsp3_follow_1": False},
}


def run_chan_py(code: str, df: pd.DataFrame, conf: dict):
    csv_path = os.path.join(CHAN_PY, f"{code}_day.csv")
    with open(csv_path, "w") as f:
        f.write("time,open,high,low,close\n")
        for r in df.itertuples(index=False):
            f.write(f"{pd.Timestamp(r.date).strftime('%Y-%m-%d')},"
                    f"{r.open},{r.high},{r.low},{r.close}\n")
    try:
        chan = CChan(code=code, begin_time=None, end_time=None, data_src=DATA_SRC.CSV,
                     lv_list=[KL_TYPE.K_DAY], config=CChanConfig(dict(conf)),
                     autype=AUTYPE.QFQ)
        kl = chan[KL_TYPE.K_DAY]
        return [{
            "bi_idx": b.bi.idx,
            "is_buy": bool(b.is_buy),
            "types": [t.value for t in b.type],
        } for b in kl.bs_point_lst.getSortedBspList()]
    finally:
        if os.path.exists(csv_path):
            os.remove(csv_path)


def main():
    with open(os.path.join(HERE, "chan_reference.json")) as f:
        base_rows = json.load(f)["klines"]
    with open(os.path.join(HERE, "bsp_reference.json")) as f:
        ref = json.load(f)

    datasets = {
        "fixture": base_rows,
        "random500": ref["random500"]["klines"],
        "real002384": ref["real002384"]["klines"],
    }

    out = {}
    for name, rows in datasets.items():
        df = pd.DataFrame([{"date": r[0], "open": float(r[1]), "high": float(r[2]),
                            "low": float(r[3]), "close": float(r[4]), "volume": 1e6}
                           for r in rows])
        out[name] = {"klines": rows, "variants": {}}
        for vname, conf in VARIANTS.items():
            bsps = run_chan_py(f"V{vname}{name}"[:20], df, conf)
            out[name]["variants"][vname] = {"config": conf, "bsps": bsps}
            print(name, vname, len(bsps))

    with open(os.path.join(HERE, "bsp_variants.json"), "w") as f:
        json.dump(out, f, ensure_ascii=False)
    print("written bsp_variants.json")


if __name__ == "__main__":
    main()
