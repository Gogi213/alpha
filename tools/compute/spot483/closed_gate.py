#!/usr/bin/env python3
"""TK-483 гейт сборки свечей из архива сделок spot (closed.py) против REST kline spot на одних сутках.
Для каждой пары и суток D: скачивает сутки D-1 и D из public.bybit.com/spot, строит D-1 (даёт перенос o через границу суток), строит D той же
candles_day, что и боевой closed.py, сверяет с REST 1440 минут: по общим минутам o,h,l,c,v,quote_v; REST-минуты без сделок — плоские ли
(o=h=l=c, v=0), сделочные минуты без REST — сколько. rc 0, если общих минут > 0, v и quote_v совпали во всех, REST-минуты без сделок — все плоские
v=0 (они не пишутся в файл), а каждое расхождение o/h/l/c объяснено неоднозначной мс (последняя мс минуты или предыдущей сделочной с ≥ 2 разных
цен: порядок исполнения внутри мс архив не хранит); unexplained = 0. Числа расхождений печатаются — это неустранимый остаток сборки из сделок.
usage: closed_gate.py ГГГГ-ММ-ДД СИМВОЛ [СИМВОЛ...]   (сутки D, сутки D-1 берутся сами)"""
import datetime as dt, io, json, os, sys, tempfile, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import closed  # noqa: E402


def rest_day(sym, day):
    s = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp()) * 1000
    out = {}
    for st in (s, s + 1000 * 60000):
        url = "https://api.bybit.com/v5/market/kline?category=spot&symbol=%s&interval=1&start=%d&end=%d&limit=1000" % (sym, st, st + 999 * 60000)
        with urllib.request.urlopen(url, timeout=60) as r:
            d = json.load(r)
        if d.get("retCode") != 0:
            raise RuntimeError("REST %s %s retCode %s" % (sym, day, d.get("retCode")))
        for k in d["result"]["list"]:
            if s <= int(k[0]) < s + 86400000:
                out[int(k[0])] = tuple(float(x) for x in k[1:7])
    return out


def built(sym, day, tmp):
    """Свечи суток day (с переносом o из day-1) -> ({t_ms: (o,h,l,c,v,quote_v,n)}, {неоднозначные минуты})."""
    prev = (dt.datetime.strptime(day, "%Y-%m-%d") - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    state = [None]
    for d in (prev, day):
        p = "%s/%s_%s.csv.gz" % (tmp, sym, d)
        with open(p, "wb") as f:
            f.write(closed.http("%s/%s/%s_%s.csv.gz" % (closed.BASE, sym, sym, d)))
        buf = io.StringIO()
        _, _, amb = closed.candles_day(p, state, buf)
    res = {}
    for ln in buf.getvalue().splitlines():
        p = ln.split(",")
        res[int(p[0])] = tuple(float(x) for x in p[1:7]) + (int(p[7]),)
    return res, amb


def close(a, b, rel=1e-9):
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b)) + 1e-12


def compare(a, amb, r):
    """a — сборка, amb — неоднозначные минуты сборки, r — REST. -> словарь счётчиков.
    unexplained — минуты с расхождением o/h/l/c, где нет неоднозначной последней мс ни в этой минуте, ни в предыдущей сделочной (от неё o)."""
    both = [k for k in a if k in r]
    keys = sorted(a)
    prev_of = {k: keys[i - 1] for i, k in enumerate(keys) if i}
    res = {"common": len(both), "built_only": len(a) - len(both), "rest_only": sum(1 for k in r if k not in a),
           "rest_only_flat": sum(1 for k in r if k not in a and r[k][4] == 0 and r[k][0] == r[k][1] == r[k][2] == r[k][3]),
           "ambiguous": len(amb)}
    bad_any = set()
    for i, name in enumerate(("o", "h", "l", "c", "v", "quote_v")):
        bad = [k for k in both if not close(a[k][i], r[k][i], 1e-6 if name == "quote_v" else 1e-9)]
        res["bad_" + name] = len(bad)
        if i < 4:
            bad_any.update(bad)
    res["unexplained"] = sum(1 for k in bad_any if k not in amb and prev_of.get(k) not in amb)
    return res


def ok(res):
    return (res["common"] > 0 and res["bad_v"] == 0 and res["bad_quote_v"] == 0 and res["unexplained"] == 0
            and res["rest_only"] == res["rest_only_flat"])


def main():
    day, syms = sys.argv[1], sys.argv[2:]
    rc = 0
    with tempfile.TemporaryDirectory() as tmp:
        for sym in syms:
            a, amb = built(sym, day, tmp)
            res = compare(a, amb, rest_day(sym, day))
            res.update(sym=sym, day=day, ok=ok(res))
            print(json.dumps(res, ensure_ascii=False))
            rc |= 0 if res["ok"] else 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
