# TK-522 (Судья 10.11 §2 п.1): S_max по L1 — описательная величина контракта, как в G0 (docs/findings/p40-g0-books-clocks-2026-11-08.md:73):
# S_max = max(1 с, медиана почасовых p99 возраста лучшей цены) по (биржа, контракт); час НЕ бракуется (G0 :132, П-40 §8 «из G0»: контракт не исключается).
# Часы с age_max > 300 с — только счётчик (порог gap Судьи 09.11 считается по событиям книги, в L1 только смены лучшей цены -> не статус).
# Запуск: python3 smax_l1.py <validity_l1.csv> [выход smax_l1.csv]
import collections, csv, statistics, sys

GAP_MS = 300_000


def smax_rows(rows):
    """rows: dict-строки validity_l1.csv -> [(ex, contract, S_max_c, часов_с_p99, часов_age_max>300с)] по ключу; часы без >= 2 состояний (no_data/no_diffs) возраста не дают"""
    p99, big = collections.defaultdict(list), collections.Counter()
    for r in rows:
        if int(r["checked"]) < 2:
            continue
        k = (r["ex"], r["contract"])
        p99[k].append(int(r["age_p99_ms"]))
        big[k] += int(r["age_max_ms"]) > GAP_MS
    return [(ex, c, max(1.0, statistics.median(v) / 1000), len(v), big[(ex, c)]) for (ex, c), v in sorted(p99.items())]


def quantiles(vals, qs=(0, 10, 25, 50, 75, 90, 100)):
    v = sorted(vals)
    return [v[round(q / 100 * (len(v) - 1))] for q in qs]


if __name__ == "__main__":
    out = smax_rows(csv.DictReader(open(sys.argv[1])))
    dst = sys.argv[2] if len(sys.argv) > 2 else "smax_l1.csv"
    with open(dst, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ex", "contract", "s_max_s", "hours", "hours_age_max_gt300s"])
        w.writerows(out)
    for ex in sorted({r[0] for r in out}):
        s = [r[2] for r in out if r[0] == ex]
        print(f"{ex:18s} контрактов {len(s):4d}  S_max, с (0/10/25/50/75/90/100 %): " + " / ".join(f"{x:.1f}" for x in quantiles(s)) + f"  >60 с: {sum(x > 60 for x in s)}")
