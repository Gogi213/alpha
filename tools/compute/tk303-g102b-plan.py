#!/usr/bin/env python3
"""TK-303: Г-102 B (T 600/720/840/960 мин × k 1…8 = 32 клетки; утв. CEO 18.10 13:25, tk302-f2-plan «Дополнение») — master.plan(P-15, 2, [Г-102]).
План = Г-102 A (F2 как есть, в Летописи) + B; новыми выйдут только хеши B. Пишет plan_cells.jsonl (ключ P-15/2/Г-102 заменяется A+B) — проверить wc -l.
  python tools/compute/tk303-g102b-plan.py <out.jsonl>   (из корня репозитория)"""
import os, sys
sys.path.insert(0, os.path.join("tools", "registry"))
sys.path.insert(0, os.path.join("tools", "compute"))
import master, importlib.util

spec = importlib.util.spec_from_file_location("f2plan", os.path.join("tools", "compute", "tk303-f2-plan.py"))
f2 = importlib.util.module_from_spec(spec); spec.loader.exec_module(f2)

_orig = master.build_grid
TS, KS = ["600", "720", "840", "960"], [str(k) for k in range(1, 9)]


def build_grid(c, protocol, k, hyps=None, rules=None, f1=None):
    grids, closed = _orig(c, protocol, k, hyps, rules, f1)
    axes = master._axes(c, protocol, "Г-102")
    paths = dict(c.execute("select name, cfg_path from params where cfg_path is not null and cfg_path != ''"))
    sv, _ = master.stack_values(c, axes[0][2], axes[0][3] or None)
    mm = {a[0]: a[5] for a in axes if a[5]}
    fixed = {a[0] for a in axes if a[4] == "fixed"}
    for t in TS:
        for kk in KS:
            av = {"k": kk, "half_min": t}
            assert set(av) == {a[0] for a in axes}, [a[0] for a in axes]
            grids["Г-102"].append((av, master.make_cfg(paths, sv, av, "Г-102", fixed, mm)))
    return grids, closed


master.build_grid = build_grid
if __name__ == "__main__":
    master.plan("P-15", 2, ["Г-102"], sys.argv[1], f1=f2.f1())
