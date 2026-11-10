"""TK-522 (Судья 10.11 §3 п.3, §7 п.7): сквозная проверка усечения у дыры. Запуск: python3 gap_e2e_check.py <пара>.exits.csv <пара>.holes [--need-both]
Дыры — файл P40_HOLES (строки `начало_мс,конец_мс`, [начало; конец)); exits — выход ядра при P40_TRUNC=1 (колонка trunc: 0 — нет, 1 — конец данных, 2 — дыра).
Инварианты: (1) старт эпизода t0 не в дыре, эпизод с trunc=0 не пересекает дыру (позиция [t0; выход] через дыру); (2) trunc=2 — после t0 есть дыра; (3) --need-both: есть дыра, у которой эпизоды и до (t0 < начала), и после (t0 >= конца). Код выхода 1 — нарушение. Исходов не читает, числа не считает."""
import csv
import sys


def load_holes(path):
    out = []
    for line in open(path).read().split("\n"):
        if line.strip():
            a, b = line.split(",")
            out.append((int(a), int(b)))
    return sorted(out)


def check(rows, holes, need_both=False):
    """rows — dict-строки exits.csv; возвращает (нарушения, сводка)"""
    bad, trunc2 = [], 0
    for r in rows:
        t0, ex, tr = int(r["t0_ms"]), int(r["exit_ms"]), r.get("trunc", "")
        end = ex if ex >= 0 else t0
        for hs, he in holes:
            if hs <= t0 < he:
                bad.append(f"старт эпизода в дыре [{hs};{he}): t0={t0} trunc={tr}")
            elif tr in ("0", "") and t0 < he and end >= hs:
                bad.append(f"trunc=0 пересекает дыру [{hs};{he}): t0={t0} exit={ex} kind={r['exit_kind']}")
        if tr == "2":
            trunc2 += 1
            if not any(he > t0 for _, he in holes):
                bad.append(f"trunc=2 без дыры после t0={t0}")
    t0s = [int(r["t0_ms"]) for r in rows]
    bracketed = sum(any(t < hs for t in t0s) and any(t >= he for t in t0s) for hs, he in holes)
    if need_both and bracketed == 0:
        bad.append("нет дыры, у которой эпизоды и до, и после")
    return bad, {"episodes": len(rows), "holes": len(holes), "bracketed": bracketed, "trunc2": trunc2}


if __name__ == "__main__":
    ex, hp = sys.argv[1], sys.argv[2]
    bad, s = check(list(csv.DictReader(open(ex))), load_holes(hp), "--need-both" in sys.argv)
    print(" ".join(f"{k}={v}" for k, v in s.items()))
    for b in bad:
        print("НАРУШЕНИЕ:", b)
    print("ok" if not bad else "FAIL")
    sys.exit(1 if bad else 0)
