#!/usr/bin/env python3
"""TK-018 (В-151): вкладка «защиты» за июль у 9 вариантов — `protections['jul'][key]` теми же ступенями и функциями,
что `titration-dashboard-merge.protection_steps` (`account_row` + `account_summary`), из сетки portfolio-sim
`jall-protect.py` (дека `tmp-p07/jall-prot/<имя>.json`, эпоха «июль», $2500/$500). Сверка: ступень «Без защит» =
`account['jul'][key]` страницы (n и $), иначе стоп.

    python tools/compute/dash-add-jall-prot.py --data data/titration-dashboard/data-v40pre4.json \\
        --prot-dir data/titration-dashboard/jall-prot --out data/titration-dashboard/data-v40.json
"""
import argparse
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("merge", os.path.join(HERE, "titration-dashboard-merge.py"))
merge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merge)
spec = importlib.util.spec_from_file_location("jp", os.path.join(HERE, "jall-protect.py"))
jp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jp)
STEPS = [  # = titration-dashboard-merge.protection_steps
    ("Без защит", dict(max_pos=0, day_stop=0.0, kill=0.0, exclude_name="нет")),
    ("+ выключатель BTC −1,5 %/1 ч и исключение прокидов", dict(max_pos=0, day_stop=0.0, kill=150.0, exclude_name="прокиды")),
    ("+ потолок 3 позиции", dict(max_pos=3, day_stop=0.0, kill=150.0, exclude_name="прокиды")),
]
SPAN = 31 * 1440


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--prot-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    D = json.load(open(a.data, encoding="utf-8"))
    dep = D["deposit_usd"]
    out = D["protections"].setdefault("jul", {})
    for name, key in jp.NAMES.items():
        grid = json.load(open(os.path.join(a.prot_dir, f"{name}.json"), encoding="utf-8"))["grid"]
        rows = []
        for label, c in STEPS:
            s = merge.account_summary(merge.account_row(grid, key, "июль", **c), dep, SPAN)
            rows.append({"label": label, **(s or {})})
        acc = D["account"]["jul"][key]
        assert (rows[0]["n"], rows[0]["net_usd"]) == (acc["n"], acc["net_usd"]), (key, rows[0]["n"], acc["n"])
        out[key] = rows
        print(key, " | ".join(f"{r['n']} / {r['net_usd']:+.2f}" for r in rows))
    open(a.out, "w", encoding="utf-8").write(json.dumps(D, ensure_ascii=False, separators=(",", ":")))
    print(a.out)


if __name__ == "__main__":
    main()
