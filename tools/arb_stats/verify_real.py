"""Сверка инкрементального состояния (state.pkl службы) с полным счётом на настоящих данных: те же куски <live>/parsed, граница t_a состояния.

Запуск на srv: python3 verify_real.py <live> [PAR]  (полный счёт — как прежний почасовой проход: analyze.compute + arb_live.hook, ≈ 5 мин на 3 ядрах)
Печатает число пар, число расхождений и первые расхождения; выход 1, если есть.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import analyze  # noqa: E402
import arb_daemon  # noqa: E402
import arb_live  # noqa: E402


def diff(a, b):
    out = []
    for k in ("cnt", "big"):
        if a[k] != b[k]:
            out.append((k, a[k], b[k]))
    for k in ("n", "pk", "tau", "net"):
        if a["st"][k] != b["st"][k]:
            out.append((k, a["st"][k], b["st"][k]))
    for k in ("S", "B", "C"):
        if a[k] != b[k]:
            bad = [i for i, (x, y) in enumerate(zip(a[k], b[k])) if x != y]
            out.append((k, len(bad), bad[:5], len(a[k]), len(b[k])))
    return out


def main(live, par=3):
    W = arb_daemon.load_world(live)
    t_b = W.t_a
    _, inc = arb_daemon.results(W, t_b)
    t0 = max(arb_live.START_MS, t_b - arb_live.WINDOW_MS)
    day = datetime.fromtimestamp(t_b / 1000, timezone.utc).date().isoformat()
    _, det, _, _, _ = analyze.compute(str(Path(live, "parsed")), day, t0, t_b, "e", hook=arb_live.hook, par=par)
    ref = {k: v for k, v in det.items() if v}
    only_inc, only_ref = sorted(set(inc) - set(ref)), sorted(set(ref) - set(inc))
    bad = {}
    for k in sorted(set(inc) & set(ref)):
        d = diff(inc[k], ref[k])
        if d:
            bad[k] = d
    print(json.dumps({"t_b": t_b, "pairs_inc": len(inc), "pairs_ref": len(ref), "only_inc": only_inc[:10], "only_ref": only_ref[:10],
                      "n_only_inc": len(only_inc), "n_only_ref": len(only_ref), "mismatched_pairs": len(bad)}, ensure_ascii=False, default=str))
    for k, d in list(bad.items())[:10]:
        print("MISMATCH", k, d)
    sys.exit(1 if (bad or only_inc or only_ref) else 0)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3)
