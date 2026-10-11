import csv, itertools, sys
out = sys.argv[1] if len(sys.argv) > 1 else "docs/findings/p12-cells-2026-10-06.csv"
rows = []
def add(fam, cls, cell, axes): rows.append((fam, cls, cell, axes))
f = lambda x: str(x).replace("/", "d").replace(",", "p")
for N, u in itertools.product((3, 4, 5), ("1/3", "2/3", "1")): add("g92", "R2", f"g92-N{N}-u{f(u)}", f"N={N};u={u};K=5")
for u, K in itertools.product(("1/3", "2/3", "1"), (1, 2, 3)): add("g93", "R2", f"g93-u{f(u)}-K{K}", f"u={u};K={K}")
for n in (2, 3, 4, 5): add("g94", "R2", f"g94-n{n}", f"n={n}")
for n in (2, 3, 4, 5): add("g87", "R2", f"g87-n{n}", f"n={n}")
for w in (3, 4, 5): add("g95", "R2", f"g95-w{w}", f"w={w}")
for g in ("e112", "e116"):
    for W, q in itertools.product((15, 30, 60), (33, 50, 67, 80)): add(g, "R2", f"{g}-W{W}-q{q}", f"W={W};q={q}")
for t in (0, 1, 2): add("e119", "R2", f"e119-tol{t}", f"tol_ticks={t}")
for x in ("0.5", "1", "2"): add("e106", "R2", f"e106-act{x}", f"x={x}")
for tr, fr in itertools.product(("halfstop", "halflevel"), ("1/4", "1/2", "3/4")): add("e114", "R2", f"e114-{tr}-f{f(fr)}", f"trigger={tr};f={fr}")
for g, T in itertools.product(("0.5", "1", "2"), ("1/4", "1/2", "1")): add("e117", "R2", f"e117-g{g}-T{f(T)}", f"gamma={g};T={T}")
for a in ("0.5", "1", "2"): add("e133", "R2", f"e133-w{a}", f"a={a}")
add("e65", "R2", "e65-t2", "offset=-1tick"); add("e65", "R2", "e65-t2x", "offset=+1tick")
Q = (20, 33, 50, 67, 80)
R1 = [("g59", ["tape_press_lots_15s", "tape_press_lots_30s", "tape_press_lots_60s"]), ("g75", ["tape_all_lots_15s"]), ("g70", ["vpin_bp"]),
      ("g71", ["sign_ac_15s_bp", "sign_ac_60s_bp"]), ("g68", ["obi1_bp", "obi5_bp", "obi10_bp", "obi50_bp"]), ("g69", ["ofi_10s_lots", "ofi_60s_lots"]),
      ("g139", ["obi50_bp"]), ("g67", ["best_flips_15s", "best_flips_60s"]), ("g63", ["frontrun_delta_10s_lots"]), ("g35", ["front_add_max_lots"]),
      ("g42", ["size_share15_bp"]), ("g43", ["best_move_1s_cbps", "best_move_10s_cbps"]), ("g44", ["opp_wall_ratio_bp"]),
      ("g82", ["tape_burst_press_15s_bp", "tape_burst_press_30s_bp", "tape_burst_press_60s_bp"]), ("g04", ["wall_add_max_lots"]), ("g39", ["cancel_life_lots"])]
for fam, cols in R1:
    for c, q in itertools.product(cols, Q): add(fam, "R1", f"{fam}-{c}-q{q}", f"col={c};keep_q={q}")
for fam in ("g73-conj", "g83-conj", "g64-drift"):
    for q in Q: add(fam, "R1", f"{fam}-q{q}", f"common_q={q}")
for fam, cell in [("g100", "g100-micro0"), ("g34", "g34-notmoved"), ("g40", "g40-retest"), ("g58", "g58-cancel0"), ("g05", "g05-c3"), ("g05", "g05-c1"),
                  ("g61", "g61-le60"), ("g61", "g61-5to20"), ("g06", "g06-slow")]: add(fam, "R1", cell, "source")
with open(out, "w", encoding="utf-8", newline="\n") as fh:
    w = csv.writer(fh, lineterminator="\n"); w.writerow(["family", "class", "cell", "axes"]); w.writerows(rows)
r1 = [r for r in rows if r[1] == "R1"]; r2 = [r for r in rows if r[1] == "R2"]
print("R2", len(r2), len({r[0] for r in r2}), "R1", len(r1), len({r[0] for r in r1}), "all", len(rows), len({r[2] for r in rows}))
