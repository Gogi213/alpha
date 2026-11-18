"""TK-609: drop-in для `tk594_feat.py` — те же аргументы (`episodes out --mode --base-date --procs --coins`), счёт делает Rust `alpha-feat`.

parquet эпизодов → CSV `ep_id,sym,side,sig_ms,turn24h` → `alpha-feat <mode>` → CSV → parquet (30 float64-столбцов + `ep_id`) + `<out>.info.json`.
Бинарь — `--bin` или `P42_FEAT_BIN`; каталоги источников — те же переменные `TK594_*`, что у Python (`alpha-feat` читает их сам).
Строки результата идут в порядке эпизодов входа (у Python — по завершению монет); сравнение — по `ep_id` (`rustfeat_gate.py`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
import tempfile
import time

import pandas as pd

FLOW = ["tfv10", "tfv60", "tfv300", "tfv1800", "tfn10", "tfn60", "tfn300", "tfn1800", "acc_v", "acc_n", "big300", "big1800", "tfp300", "lmsz300", "msz_r",
        "vpin"]
DERIV = ["oi_c1", "oi_c4", "oi_c24", "loi_turn", "ls_tc", "ls_ts", "ls_a", "dls_tc", "dls_ts", "dls_a", "fund_last", "fund_chg24", "fund_int", "basis"]


def read_rust_csv(path: str) -> pd.DataFrame:
    """Выход alpha-feat: float — круговая запись (`float_precision=round_trip`), все 30 столбцов float64 даже при пустом столбце."""
    dtype = {c: "float64" for c in FLOW + DERIV}
    dtype["ep_id"] = "object"
    return pd.read_csv(path, dtype=dtype, float_precision="round_trip", keep_default_na=True)


def cpu_children() -> float:
    """ЦП-секунды (user+sys) дочерних процессов — `alpha-feat` и его `unzip`: честное число при нагрузке на машину (стена растягивается)."""
    ru = resource.getrusage(resource.RUSAGE_CHILDREN)
    return ru.ru_utime + ru.ru_stime


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episodes")
    ap.add_argument("out")
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--coins", default="")
    ap.add_argument("--base-date", default="2026-09-01")
    ap.add_argument("--mode", default="all", choices=["all", "flow", "deriv"])
    ap.add_argument("--bin", default=os.environ.get("P42_FEAT_BIN", ""))
    a = ap.parse_args()
    if not a.bin:
        sys.exit("нужен --bin или P42_FEAT_BIN (путь к alpha-feat)")
    t0 = time.time()
    e = pd.read_parquet(a.episodes, columns=["ep_id", "sym", "side", "sig_ms", "turn24h"])
    if a.coins:
        e = e[e.sym.isin(a.coins.split(","))]
    tmp = tempfile.mkdtemp(prefix="rustfeat-", dir=os.path.dirname(os.path.abspath(a.out)))
    eps_csv, out_csv = os.path.join(tmp, "eps.csv"), os.path.join(tmp, "out.csv")
    e.to_csv(eps_csv, index=False)  # float — repr (кратчайшая круговая запись), NaN — пусто
    cmd = [a.bin, a.mode, "--eps", eps_csv, "--out", out_csv, "--base-date", a.base_date, "--threads", str(a.procs)]
    if a.coins:
        cmd += ["--coins", a.coins]
    print("запуск:", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode:
        sys.exit(r.returncode)
    summary = json.loads(r.stdout.strip().splitlines()[-1])
    res = read_rust_csv(out_csv)
    assert len(res) == len(e) and res["ep_id"].tolist() == e["ep_id"].tolist(), "порядок/число строк выхода не совпало со входом"
    res.to_parquet(a.out)
    os.remove(eps_csv)
    os.remove(out_csv)
    os.rmdir(tmp)
    cov = {k: float(res[k].notna().mean()) for k in FLOW + DERIV}
    info = {"episodes": int(len(res)), "seconds": time.time() - t0, "coverage": cov, "missing_trade_days": int(summary["missing_trade_days"]),
            "engine": "alpha-feat", "cpu_s_children": cpu_children(), "bin_md5": hashlib.md5(open(a.bin, "rb").read()).hexdigest(), "rust": summary}
    json.dump(info, open(a.out + ".info.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in info.items() if k != "coverage"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
