#!/usr/bin/env python3
# Гейт TK-025, проверка кэша подходов с --r1-cols против прежнего:
#   python3 gate-r1-cols.py <approaches-old.csv> <approaches-r1.csv>
# Прежние колонки (и заголовок) — побайтно те же строки; следом ровно 61 новая колонка R1 в порядке
# src/lob/r1.rs (FLOW_NAMES, затем LEVEL_NAMES); печатает долю определённых клеток по колонкам.
import csv
import sys

FLOW = (
    "tape_press_lots_15s tape_with_lots_15s tape_all_lots_15s tape_press_n_15s tape_with_n_15s tape_all_n_15s "
    "tape_press_lots_30s tape_with_lots_30s tape_all_lots_30s tape_press_n_30s tape_with_n_30s tape_all_n_30s "
    "tape_press_lots_60s tape_with_lots_60s tape_all_lots_60s tape_press_n_60s tape_with_n_60s tape_all_n_60s "
    "tape_press_60m_lots tape_with_60m_lots tape_burst_press_15s_bp tape_burst_with_15s_bp tape_burst_press_30s_bp "
    "tape_burst_with_30s_bp tape_press_avg_30s_e2 tape_with_avg_30s_e2 sign_ac_15s_bp sign_ac_60s_bp vpin_bp "
    "trade_size_p50_15m trade_size_p90_15m obi1_bp obi5_bp obi10_bp obi50_bp micro_off_cbps ofi_10s_lots "
    "ofi_60s_lots best_flips_15s best_flips_60s"
).split()
LEVEL = (
    "cancel_1s_lots cancel_3s_lots cancel_60m_lots cancel_life_lots wall_add_max_lots wall_add_max_age_ms "
    "front_add_max_lots front_add_max_age_ms born_shift_cbps born_shift_lots prev_death_gap_ms prev_death_outcome "
    "size_share15_bp nz_levels15 best_move_1s_cbps best_move_10s_cbps opp_wall_dist_cbps opp_wall_ratio_bp "
    "frontrun_delta_10s_lots frontrun_levels since_far_ms"
).split()
NAMES = FLOW + LEVEL
assert len(FLOW) == 40 and len(LEVEL) == 21


def rows(path):
    with open(path, newline="") as f:
        return list(csv.reader(f))


def main():
    old, new = rows(sys.argv[1]), rows(sys.argv[2])
    k = len(old[0])
    bad = []
    if len(new) != len(old):
        bad.append(f"строк {len(new)} ≠ {len(old)}")
    if new[0][:k] != old[0]:
        bad.append("заголовок прежних колонок изменился")
    if new[0][k:] != NAMES:
        bad.append(f"имена новых колонок не по r1.rs: {new[0][k:][:3]}…")
    if any(len(r) != k + 61 for r in new[1:]):
        bad.append("есть строки не с k+61 клеток")
    ndiff = sum(1 for o, n in zip(old[1:], new[1:]) if n[:k] != o)
    if ndiff:
        bad.append(f"строк с изменёнными прежними клетками: {ndiff}")
    if bad:
        print("DIFF " + "; ".join(bad))
        return 1
    n = len(old) - 1
    undef = [sum(1 for r in new[1:] if r[k + i] == "") for i in range(61)]
    print(f"OK   прежние {k} колонок побайтно, +61 новых, {n} строк")
    print("     определено, %: " + " ".join(f"{NAMES[i]}={100 * (n - undef[i]) / max(n, 1):.1f}" for i in range(61)))
    return 0


sys.exit(main())
