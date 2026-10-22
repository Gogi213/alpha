#!/usr/bin/env python3
"""TK-369 / G-016: генератор банка гипотез DEX<->CEX (протокол docs/research/P-25-g016-dex-cex-bank.md).
Строка банка = событие x условие x направление; горизонты, страты и контроль — общие (в протоколе).
Пороги — квантили распределения самого пула/монеты по неделе 15-21.10 нарастающим итогом (causal), числа не подбираются.
    python tools/compute/tk369-bank.py [--out docs/research/g016-hypothesis-bank-2026-10-23.csv]
"""
import argparse, csv, itertools

# (family, event, mechanism, sign_rule, data, gated_on)  gated_on='' — данные есть/будут в TK-364+TK-366
FAM = [
    ("A", "dex_big_swap", "крупный своп сдвигает пул, арбитражёры/информированные несут сторону на Bybit", "buy->long, sell->short", ""),
    ("B", "dex_cluster", "серия свопов в одну сторону = поток, а не одиночный шум", "buy->long, sell->short", ""),
    ("C", "dex_priority_fee", "высокая плата за срочность = информированный/спешащий", "buy->long, sell->short", "tx-детали (effectiveGasPrice/priority) — нет в outw*, ждёт решения CEO по TK-364"),
    ("D", "dex_new_wallet", "новый/«умный» кошелёк (история входов до события) знает больше", "buy->long, sell->short", "кошелёк инициатора (tx.from) — нет в outw*"),
    ("E", "dex_organic_vs_arb", "органика (не арбитражёр: нет обратной сделки в блоке/на Bybit) несёт информацию, арбитражёр — нет", "buy->long, sell->short", "tx-детали (to/txIndex)"),
    ("F", "div_pool_bybit", "расхождение цены пула и Bybit >= Y бп: кто сходится к кому", "к пулу (+) и к Bybit (-) — обе стороны, знак S показывает лидера", ""),
    ("G", "bybit_move_pool_lag", "резкий ход Bybit: пул отстаёт на W секунд (калибровка F/A, продолжение на Bybit)", "по ходу Bybit", ""),
    ("H", "dex_liquidity", "добавление/вывод ликвидности из пула", "add->нет знака (сила); remove->нет знака", "Mint/Burn логи — нет в outw*"),
    ("I", "binance_to_bybit", "Binance ходит раньше Bybit на тех же монетах", "по ходу Binance", "Binance aggTrades (data.binance.vision) — бесплатно, не скачано"),
    ("J", "spot_vs_perp", "спот Bybit ведёт перп на не-мажорах (или наоборот)", "по ходу ведущего", "спот+фьюч Bybit сделки (TK-366)"),
    ("K", "pools_cross", "на монете несколько пулов/сетей: кто ведёт (пул A -> пул B и Bybit)", "по ходу ведущего", ""),
    ("L", "flow_imbalance_dex", "дисбаланс покупок/продаж DEX за N с (скользящее окно) -> Bybit", "по знаку дисбаланса", ""),
]

# пороги: квантили собственного распределения (causal), не назначенные числа
Q_SIZE = [("q95", .95), ("q99", .99), ("q999", .999)]
K_N = [(3, 10), (5, 60)]                    # >=k свопов одного знака за N секунд
Y_DIV = [("q90", .90), ("q99", .99)]        # |div| пул-Bybit по монете
W_MOVE = [(1, "q99"), (5, "q99"), (30, "q99"), (300, "q99")]  # ход Bybit за W с >= квантиля |ход W| монеты
LF_WIN = [(10, "q95"), (30, "q95"), (60, "q95"), (300, "q95")]  # |дисбаланс| за окно, квантиль


def rows():
    n = 0
    def row(fam, cond_id, cond, **kw):
        nonlocal n
        n += 1
        f = next(x for x in FAM if x[0] == fam)
        return dict(id=f"G16-{fam}{n:03d}", family=fam, event=f[1], cond_id=cond_id, condition=cond,
                    sign_rule=kw.get("sign_rule", f[3]), mechanism=f[2], gated_on=f[4], role="test")
    out = []
    for qn, _ in Q_SIZE:
        out.append(row("A", f"size_{qn}", f"размер свопа >= {qn} размеров свопов этого пула (нарастающим итогом, >=200 свопов до события)"))
    for qn, _ in Q_SIZE[:2]:
        for sd in ("buy", "sell"):
            out.append(row("A", f"size_{qn}_{sd}", f"то же, только {sd} токена", sign_rule="buy->long" if sd == "buy" else "sell->short"))
    out.append(row("A", "size_q999_alone", "размер >= q999 и в пуле нет других свопов за ±60 с"))
    out.append(row("A", "size_q99_after_quiet", "размер >= q99 после >= 300 с тишины в пуле"))
    out.append(row("A", "size_q99_dir_runlen", "размер >= q99 и предыдущие 3 свопа пула того же знака"))
    for k, N in K_N:
        out.append(row("B", f"clu_{k}in{N}s", f">= {k} свопов одного знака за {N} с в пуле"))
        out.append(row("B", f"clu_{k}in{N}s_size", f">= {k} свопов одного знака за {N} с, суммарный объём >= q95 пула"))
    out.append(row("B", "clu_multiwallet", ">= 3 свопов за 60 с от разных sender/recipient (ждёт кошельков)"))
    for fam in ("C", "D", "E", "H"):
        for qn, _ in Q_SIZE[:2]:
            out.append(row(fam, f"{fam.lower()}_{qn}", f"условие семьи {fam} x размер >= {qn} (ждёт данных)"))
    for yn, _ in Y_DIV:
        out.append(row("F", f"div_{yn}", f"|цена пула - mid Bybit| >= {yn} по монете; S к пулу"))
        out.append(row("F", f"div_{yn}_big", f"то же и за последние 60 с был свопе >= q95", sign_rule="к пулу"))
        out.append(row("F", f"div_{yn}_persist", f"расхождение держится >= 10 с подряд >= {yn}"))
    for W, qn in W_MOVE:
        out.append(row("G", f"bmove_{W}s_{qn}", f"ход Bybit за {W} с >= {qn}; смотрим продолжение Bybit и догон пула"))
        out.append(row("G", f"bmove_{W}s_{qn}_poolflat", f"то же, пул за эти {W} с не двигался (отстал)"))
    for src in ("binance",):
        for W, qn in W_MOVE:
            out.append(row("I", f"{src}_{W}s_{qn}", f"ход {src} за {W} с >= {qn} -> Bybit (ждёт скачивания)"))
    for W, qn in W_MOVE[:2]:
        out.append(row("J", f"spot_{W}s_{qn}", f"ход спота Bybit за {W} с >= {qn} -> перп"))
        out.append(row("J", f"perp_{W}s_{qn}", f"ход перпа Bybit за {W} с >= {qn} -> спот"))
    out.append(row("K", "xpool_first", "первый своп >= q95 на монете среди её пулов/сетей -> остальные пулы и Bybit"))
    out.append(row("K", "xpool_chain_lead", "сеть, чей пул ведёт по лагу корреляции (кросс-корр. по неделе) -> остальные"))
    for w, qn in LF_WIN:
        out.append(row("L", f"imb_{w}s_{qn}", f"|(buy-sell)/(buy+sell)| за {w} с >= {qn} монеты, знак = сторона дисбаланса"))
        out.append(row("L", f"imb_{w}s_{qn}_n3", f"то же и >= 3 свопов в окне"))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/research/g016-hypothesis-bank-2026-10-23.csv")
    a = ap.parse_args()
    r = rows()
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(r[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(r)
    ready = sum(1 for x in r if not x["gated_on"])
    print(f"hypotheses={len(r)} ready_now={ready} gated={len(r)-ready} -> {a.out}")
