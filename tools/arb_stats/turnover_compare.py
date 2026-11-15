"""Сверка службы arb_turnover.py с проверенным счётом turnover.py на том же окне.

python3 turnover_compare.py <каталог для состояния> <проверенный turnover.json> [PAR] — bootstrap arb_turnover.py за окно win.page из проверенного файла,
суммы по (биржа, символ) и по (монета, биржа) против sym[биржа][символ][0] и coin[монета][биржа][0]. Печатает: время bootstrap, память, число сверенных, расхождения.
Живые службы и каталоги не трогает: состояние пишется в <каталог> (например /data/arb-shadcn/turn-live).
"""
import json
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import arb_daemon  # noqa: E402
import arb_turnover as T  # noqa: E402

MIN = T.MIN


def main(live, ver_path, par=3):
    ver = json.loads(Path(ver_path).read_text())
    t0, t1 = ver["win"]["page"]
    Path(live).mkdir(parents=True, exist_ok=True)
    started = time.time()
    S = T.bootstrap(live, t1, par)
    boot_s = time.time() - started
    lo, hi = t0 // MIN, t1 // MIN
    bad, n, worst_abs, worst_rel, extra = [], 0, 0.0, 0.0, []
    tot = {}
    for ex, syms in ver["sym"].items():
        for s, a in syms.items():
            mine = T.window_sum(S["bins"], S["m0"], (ex, s), lo, hi)
            d = abs(mine - a[0])
            worst_abs, worst_rel = max(worst_abs, d), max(worst_rel, d / max(a[0], 1.0))
            n += 1
            tot.setdefault(ex, [0.0, 0.0])
            tot[ex][0] += mine
            tot[ex][1] += a[0]
            if d > max(1e-6 * a[0], 0.01):
                bad.append((ex, s, mine, a[0]))
    for (ex, s), arr in S["bins"].items():
        if s not in ver["sym"].get(ex, {}) and T.window_sum(S["bins"], S["m0"], (ex, s), lo, hi) > 0:
            extra.append((ex, s))
    day = time.strftime("%Y-%m-%d", time.gmtime(t1 / 1000))
    _, coins = arb_daemon.coin_map(day)
    cbad, cn = [], 0
    for coin, per in coins.items():
        for ex, (sym, _, _) in per.items():
            ref = (ver["coin"].get(coin) or {}).get(ex)
            if ref is None:
                continue
            mine = T.window_sum(S["bins"], S["m0"], (ex, sym), lo, hi)
            cn += 1
            if abs(mine - ref[0]) > max(1e-6 * ref[0], 0.5):
                cbad.append((coin, ex, sym, round(mine), round(ref[0])))
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // 1024
    print(json.dumps({"window": [t0, t1], "par": par, "bootstrap_s": round(boot_s, 1), "rss_mb_self": ru[0], "rss_mb_largest_child": ru[1], "files": len(S["parse"]),
                      "symbols_checked": n, "symbols_mismatch": len(bad), "max_abs_usd": worst_abs, "max_rel": worst_rel, "extra_symbols_with_turnover": extra[:20],
                      "per_exchange_usd": {ex: {"mine": round(v[0]), "verified": round(v[1])} for ex, v in tot.items()}, "coin_exchange_checked": cn,
                      "coin_exchange_mismatch": len(cbad)}, ensure_ascii=False))
    for b in bad[:20]:
        print("SYM", b)
    for b in cbad[:30]:
        print("COIN", b)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 3)
