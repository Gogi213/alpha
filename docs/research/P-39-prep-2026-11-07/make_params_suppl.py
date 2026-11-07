#!/usr/bin/env python3
"""TK-493, П-39: params ДОБАВОЧНОГО семейства Cu (S_n со снятием сдвига единиц, кратного ln(10)·1e4 bps) -> docs/research/P-39-params-suppl-2026-11-07.csv.
Причина (Инженер, 07.11, ДО поиска): β(n, 0,99) по формированию = 69 074 bps = ln(1000)·1e4 - у ~1 % пар-суток спред имеет сдвиг единиц (контракты 1000PEPE / PEPE и т. п.), а не структурный разрыв; буквальный S_n = |медиана s|
отсекает такие пары при q = 0,90 и 0,95 механически. Протокольные 18 строк C считаются как записаны (буквально); Cu - 18 добавочных строк, ОТМЕЧЕНЫ как добавленные Инженером сверх протокола (счётчик испытаний 158 + 18).
Запуск: python make_params_suppl.py > ../P-39-params-suppl-2026-11-07.csv   (stdlib)
"""
import csv
import sys

TPL = ("id={id}; fam=Cu; base={base}; th=40; k=3; p=3; T={T}; s0=none; lam=320; ecap=base; q5={q5}; ffilt=none; sgap=n:{n}/q:{q}/u:1; excl_ex=none; shortban=none; terc=all; out=none; "
       "delay=1; cls=crypto; gate=no; stage=1")
rows = [["key", "value", "status", "when", "source"],
        ["protocol", "P-39 r1 + добавка Инженера 07.11 (семейство Cu)", "из источника", "P-39 r1 07.11", "docs/research/P-39-convergence-explore-2026-11-07.md"],
        ["suppl_reason", "β(n, 0,99) по формированию = 69 074 bps = ln(1000)·1e4: у ~1 % пар-суток сдвиг единиц (контракт x10^k), не структурный разрыв; буквальные строки C (S_n = |медиана s|) отсекают такие пары механически. "
                         "Cu = те же 18 клеток (n x q x база) со снятием ближайшего кратного ln(10)·1e4 bps перед модулем; пороги β_u измерены по формированию 2472..2478 (addendum, ключ calib_beta_sgap_unit)",
         "измерено; решение Инженера, назначено без Судьи, подтвердить", "07.11 до поиска", "explore_thresholds.py"],
        ["suppl_trial_count", "+18 (158 по протоколу + 18 добавочных = 176 в стадии 1)", "назначено без Судьи, подтвердить", "07.11 до поиска", "честный счётчик испытаний §9"]]
for base, T, q5 in (("B1", 1440, "on"), ("B2", 120, "off")):
    for n in (1, 3, 7):
        for q in ("0.90", "0.95", "0.99"):
            vid = "Cu-%s-n%d-q%s" % (base, n, q[2:])
            rows.append(["v_" + vid, TPL.format(id=vid, base=base, T=T, q5=q5, n=n, q=q), "назначено без Судьи, подтвердить (добавлено Инженером сверх протокола)", "07.11 до поиска",
                         "docs/research/P-39-prep-2026-11-07/make_params_suppl.py"])
w = csv.writer(sys.stdout, lineterminator="\n")
w.writerows(rows)
