"""Макет v3 вкладки «Арбитраж»: таблица (монета, пара, расхождение, частота за 1/4/6 ч/сутки, схождение, после комиссий) и график по клику.

Запуск: python build_mockup_v3.py <result.json> <detail.pkl> <v3.json> <page.html> <шаблон> <выход.html>
Два счёта: медиана (медиана пика, медиана схождения, медиана круга) и выбросы (p95 и максимум пика, p95 схождения, p95 круга);
всё по эпизодам порога комиссий (12,6 bps). Частота — эпизодов за час покрытия, в таблице пересчитывается на 1/4/6 ч/сутки.
График — максимум Sell и минимум Buy за слот 3 мин (пики, не закрытия) и центр.
"""
import json
import pickle
import re
import sys

import numpy as np

sys.path.insert(0, __import__("pathlib").Path(__file__).parent.as_posix())
import core  # noqa: E402

NAMES = {"binance": "Binance", "bybit": "Bybit", "okx": "OKX", "bingx": "BingX"}


def r1(v):
    return None if v is None else round(v, 1)


def main(res_p, det_p, v3_p, page_p, tmpl_p, out_p):
    res = json.load(open(res_p, encoding="utf-8"))
    det = pickle.load(open(det_p, "rb"))
    snap = json.load(open(v3_p, encoding="utf-8"))
    page = open(page_p, encoding="utf-8").read()
    tmpl = open(tmpl_p, encoding="utf-8").read()
    t0, t1 = res["t0"], res["t1"]
    tmin0 = t0 // 60000
    n = int(np.ceil((t1 - t0) / (core.SLOT_MIN * 60000)))
    rows = sorted((r for r in res["rows"] if "eps" in r), key=lambda r: (r["coin"], r["A"], r["B"]))
    out, detail = [], {}
    for i, r in enumerate(rows):
        d = det[(r["coin"], r["A"], r["B"])]
        s = core.ep_table_stats(d["eps"][core.FEE_RT_BPS])
        out.append({"i": i, "c": r["coin"], "a": r["A"], "b": r["B"], "n": s["n"], "h": round(s["n"] / (r["cover_s"] / 3600), 3),
                    "pk": s["pk"] and [r1(x) for x in s["pk"]], "tau": s["tau"] and [round(x, 3) for x in s["tau"]],
                    "net": s["net"] and [r1(x) for x in s["net"]]})
        S, B, C = core.chart_slots(d["bars"], tmin0, n)
        detail[i] = {"S": S, "B": B, "C": C}
    arb = {"t0": t0, "t1": t1, "slot": core.SLOT_MIN * 60, "fee": core.FEE_RT_BPS, "names": NAMES, "rows": out, "detail": detail}
    css = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    js = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
    js = re.sub(r"async function load\(\)\{.*?\n\}\n", "function load(){D=SNAP;render()}\n", js, flags=re.S)
    js = js.replace("load();setInterval(load,30000);", "load();")
    assert "SNAP" in js and "setInterval" not in js
    js = "const SNAP=" + json.dumps(snap, ensure_ascii=False, separators=(",", ":")) + ";\n" + js
    html = tmpl.replace("/*HEALTH_CSS*/", css).replace("/*HEALTH_JS*/", js).replace("__ARB__", json.dumps(arb, ensure_ascii=False, separators=(",", ":")))
    open(out_p, "w", encoding="utf-8").write(html)
    print(json.dumps({"bytes": len(html), "rows": len(out), "with_eps": sum(1 for r in out if r["n"])}))


if __name__ == "__main__":
    main(*sys.argv[1:7])
