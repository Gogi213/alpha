#!/usr/bin/env python3
"""G-022 турбо: что теряет снятие дублей «ключ не больше записанного» при двух линиях — по полному захвату кадров (ALPHA_COLLECT_CAPTURE=1).
Читает `capture-<шард>.tsv` (сессия, приём нс, кадр — всё, что пришло, до дедупа), считает по (вид, символ):
  внутри сессии: ключи растут? (нарушения порядка самой биржи/пути);
  объединение ключей всех сессий и покрытие каждой сессии;
  что даёт дедуп по последнему ключу в порядке прихода (как писатель) против «набора виденных ключей» (дубль — только точный повтор).
Запуск: lanecmp.py <binance|bybit> <каталог с capture-*.tsv> --out lanecmp.json
"""
import argparse
import glob
import json
import os
from collections import defaultdict

TRADE_SLOT = 1000


def keys_of(ex, frame):
    """[(вид, символ, ключ, личность)] кадра; личность — настоящий id сделки (Bybit: UUID `i`), иначе None; пусто — кадр не данные."""
    try:
        d = json.loads(frame)
    except ValueError:
        return []
    if ex == "binance":
        x = d.get("data") if isinstance(d, dict) else None
        if isinstance(x, dict):
            if x.get("e") == "bookTicker":
                return [("b", x["s"], x["u"], None)]
            if x.get("e") == "aggTrade":
                return [("t", x["s"], x["a"], None)]
        elif isinstance(x, list):
            return [("m", y["s"], y["E"], None) for y in x if y.get("e") == "markPriceUpdate"]
        return []
    topic = d.get("topic", "") if isinstance(d, dict) else ""
    if topic.startswith("orderbook.1."):
        x = d["data"]
        return [("b", x["s"], x["u"], None)]
    if topic.startswith("publicTrade."):
        return [("t", x["s"], x["seq"] * TRADE_SLOT + i, x["i"]) for i, x in enumerate(d["data"])]
    return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ex")
    ap.add_argument("dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-s", type=float, default=15.0, help="отбросить первые секунды (подписка, снимки)")
    a = ap.parse_args()
    res = {}
    for p in sorted(glob.glob(os.path.join(a.dir, "capture-*.tsv"))):
        shard = os.path.basename(p)[8:-4]
        rows = []
        with open(p, encoding="utf-8", errors="replace") as f:
            for ln in f:
                parts = ln.rstrip("\n").split("\t", 2)
                if len(parts) == 3:
                    rows.append((int(parts[0]), int(parts[1]), parts[2]))
        if not rows:
            continue
        t_lo = rows[0][1] + int(a.skip_s * 1e9)
        per = defaultdict(lambda: {"order": [], "by_sender": defaultdict(list), "ident": defaultdict(dict)})
        for sender, rx, frame in rows:
            if rx < t_lo:
                continue
            for kind, sym, key, ident in keys_of(a.ex, frame):
                e = per[(kind, sym)]
                e["order"].append((sender, key))
                e["by_sender"][sender].append(key)
                if ident is not None:
                    e["ident"][ident].setdefault(sender, key)
        agg = defaultdict(lambda: defaultdict(int))
        for (kind, sym), e in per.items():
            g = agg[kind]
            allk = {k for _, k in e["order"]}
            g["symbols"] += 1
            g["frames_keys"] += len(e["order"])
            g["union"] += len(allk)
            # внутри сессии: ключ не больше предыдущего (повтор или обратный ход)
            for s, ks in e["by_sender"].items():
                for x, y in zip(ks, ks[1:]):
                    if y < x:
                        g["in_session_reorder"] += 1
                    elif y == x:
                        g["in_session_repeat"] += 1
            # дедуп «ключ не больше записанного» в порядке прихода
            last, kept = -1, set()
            for _, k in e["order"]:
                if k > last:
                    last = k
                    kept.add(k)
            g["kept_monotone"] += len(kept)
            g["lost_by_monotone"] += len(allk - kept)
            # покрытие сессиями
            for s, ks in e["by_sender"].items():
                g[f"sender_missing_{len(e['by_sender'])}way"] += len(allk - set(ks))
            g["senders_max"] = max(g["senders_max"], len(e["by_sender"]))
            nsend = len(e["by_sender"])
            for ident, by in e["ident"].items():
                g["fills"] += 1
                if len(by) < nsend:
                    g["fill_seen_by_fewer_senders"] += 1  # сделка пришла не по всем сессиям символа
                if len(set(by.values())) > 1:
                    g["fill_key_differs_between_senders"] += 1  # одна сделка — разные ключи a у сессий (разная нарезка пачек)
        res[shard] = {k: dict(v) for k, v in agg.items()}
    tot = defaultdict(lambda: defaultdict(int))
    for sh in res.values():
        for kind, g in sh.items():
            for k, v in g.items():
                tot[kind][k] = max(tot[kind][k], v) if k == "senders_max" else tot[kind][k] + v
    out = {"total": {k: dict(v) for k, v in tot.items()}, "shards": res}
    json.dump(out, open(a.out, "w"), indent=1)
    print(json.dumps(out["total"]))


if __name__ == "__main__":
    main()
