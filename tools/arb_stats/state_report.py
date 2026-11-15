"""Сводка по state.pkl живого счёта (arb_daemon): сколько пар, из них у каждой биржи, сколько у Gate и какие пары отброшены правилом «разные активы» (отношение середин вне 0,9…1,1).

Запуск: python3 state_report.py <каталог с state.pkl | state.pkl> [биржа, по умолчанию gate] [--brief]
        python3 state_report.py arbjson <arb.json> [биржа]     — строки живой таблицы с биржей (для проверки после выкладки)
--brief — две строки вместо JSON; код выхода 1, если у биржи нет ни одной пары. Иначе печатает один JSON: t_a, символы, пары, пары по биржам, монеты биржи, пары биржи, не готовые к парам (мало событий), отброшенные (монета, A, B, отношение).
Нужен пакет zstandard (arb_daemon его импортирует; на srv — PYTHONPATH=/opt/arb-tab/pylib).
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import arb_daemon  # noqa: E402
from common import EXCHANGES  # noqa: E402


def report(W, coins, ex="gate"):
    by_ex = {e: 0 for e in EXCHANGES}
    for _, a, b in W.pairs:
        by_ex[a] += 1
        by_ex[b] += 1
    out = {"t_a": W.t_a, "symbols": len(W.syms), "pairs": len(W.pairs), "pairs_by_exchange": by_ex,
           "coins_with_exchange": 0, "pairs_with_exchange": sum(1 for k in W.pairs if ex in k[1:]), "not_ready": [], "excluded": []}
    for coin, per in sorted(coins.items()):
        if ex not in per:
            continue
        out["coins_with_exchange"] += 1
        for e, (sym, _, _) in per.items():
            if not arb_daemon.ready(W, e, sym):
                out["not_ready"].append([coin, e])
        exs = [e for e in EXCHANGES if e in per and arb_daemon.ready(W, e, per[e][0])]
        for i in range(len(exs)):
            for j in range(i + 1, len(exs)):
                a, b = exs[i], exs[j]
                if ex not in (a, b):
                    continue
                sa, sb = W.syms[(a, per[a][0])], W.syms[(b, per[b][0])]
                if not arb_daemon.pair_ok(sa, sb):
                    out["excluded"].append([coin, a, b, round(sa.mid / sb.mid, 6)])
    return out


def brief(d, ex):
    import time
    print(f"t_a {d['t_a']} ({(time.time() - d['t_a'] / 1000) / 60:.1f} мин назад) символов {d['symbols']} пар {d['pairs']} по биржам {d['pairs_by_exchange']}")
    print(f"{ex}: монет {d['coins_with_exchange']} пар {d['pairs_with_exchange']} отброшено «разные активы» {len(d['excluded'])} не готовы (мало событий) {len(d['not_ready'])}")
    return 0 if d["pairs_with_exchange"] else 1


def arbjson(path, ex="gate"):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = [r for r in d["rows"] if ex in (r["a"], r["b"])]
    print(f"arb.json: строк {len(d['rows'])}, с {ex} {len(rows)}, монет с {ex} {len({r['c'] for r in rows})}, names {d.get('names')}")
    return 0 if rows and ex in (d.get("names") or {}) else 1


def main(path, ex="gate", short=False):
    p = Path(path)
    live = str(p if p.is_dir() else p.parent)
    W = arb_daemon.load_world(live)
    if W is None:
        print(json.dumps({"error": f"нет state.pkl нужного формата в {live}"}))
        sys.exit(1)
    _, coins = arb_daemon.coin_map(datetime.now(timezone.utc).date().isoformat())
    d = report(W, coins, ex)
    if short:
        sys.exit(brief(d, ex))
    print(json.dumps(d, ensure_ascii=False))


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if x != "--brief"]
    if a[0] == "arbjson":
        sys.exit(arbjson(a[1], a[2] if len(a) > 2 else "gate"))
    main(a[0], a[1] if len(a) > 1 else "gate", "--brief" in sys.argv)
