"""Макет v2 вкладки «Арбитраж»: одна таблица (монета, пара, расхождение, раз в час, схождение) и один график по клику.

Запуск: python build_mockup_v2.py <result.json> <detail.pkl> <v3.json> <page.html> <шаблон> <выход.html>
Расхождение — медиана пика по эпизодам порога комиссий (12,6 bps); раз в час — эпизодов за час покрытия; схождение — медиана длительности.
График — закрытия баров Sell/Buy и центр по слотам 3 мин (в макете, чтобы файл был лёгким).
"""
import json
import pickle
import re
import sys

import numpy as np

sys.path.insert(0, __import__("pathlib").Path(__file__).parent.as_posix())
import core  # noqa: E402

NAMES = {"binance": "Binance", "bybit": "Bybit", "okx": "OKX", "bingx": "BingX"}
SLOT_MIN = 3


def slots(b, tmin0, n):
    """Закрытия Sell, Buy и центр по слотам SLOT_MIN минут (значения ×10, целые; None — нет данных)."""
    mins = b["m0"] + b["mins"]
    sl = (mins - tmin0) // SLOT_MIN
    ok = (sl >= 0) & (sl < n)
    mins, sl = mins[ok], sl[ok]
    S, B = b["S"][3][ok], b["B"][3][ok]
    cen = b["centre"]
    C = cen[np.clip(mins - b["centre_m0"], 0, len(cen) - 1)]
    outS, outB, outC = [None] * n, [None] * n, [None] * n
    for k in range(len(sl)):   # бары идут по возрастанию — в слоте остаётся последний
        i = int(sl[k])
        outS[i], outB[i] = int(round(S[k] * 10)), int(round(B[k] * 10))
        outC[i] = int(round(C[k] * 10)) if np.isfinite(C[k]) else None
    return outS, outB, outC


def main(res_p, det_p, v3_p, page_p, tmpl_p, out_p):
    res = json.load(open(res_p, encoding="utf-8"))
    det = pickle.load(open(det_p, "rb"))
    snap = json.load(open(v3_p, encoding="utf-8"))
    page = open(page_p, encoding="utf-8").read()
    tmpl = open(tmpl_p, encoding="utf-8").read()
    t0, t1 = res["t0"], res["t1"]
    fee_i = res["levels"].index(core.FEE_RT_BPS)
    tmin0 = t0 // 60000
    n = int(np.ceil((t1 - t0) / (SLOT_MIN * 60000)))
    rows = sorted((r for r in res["rows"] if "eps" in r), key=lambda r: (r["coin"], r["A"], r["B"]))
    out, detail = [], {}
    for i, r in enumerate(rows):
        e = r["eps"][fee_i]
        out.append({"i": i, "c": r["coin"], "a": r["A"], "b": r["B"],
                    "size": round(e["peak_med"], 1) if e["n"] else None,
                    "h": round(e["n"] / (r["cover_s"] / 3600), 2),
                    "tau": (round(e["tau_s"][0], 3) if e["tau_s"] else None)})
        S, B, C = slots(det[(r["coin"], r["A"], r["B"])]["bars"], tmin0, n)
        detail[i] = {"S": S, "B": B, "C": C}
    arb = {"t0": t0, "t1": t1, "slot": SLOT_MIN * 60, "names": NAMES, "rows": out, "detail": detail}
    css = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    js = re.search(r"<script>(.*?)</script>", page, re.S).group(1)
    js = re.sub(r"async function load\(\)\{.*?\n\}\n", "function load(){D=SNAP;render()}\n", js, flags=re.S)
    js = js.replace("load();setInterval(load,30000);", "load();")
    assert "SNAP" in js and "setInterval" not in js
    js = "const SNAP=" + json.dumps(snap, ensure_ascii=False, separators=(",", ":")) + ";\n" + js
    html = tmpl.replace("/*HEALTH_CSS*/", css).replace("/*HEALTH_JS*/", js).replace("__ARB__", json.dumps(arb, ensure_ascii=False, separators=(",", ":")))
    open(out_p, "w", encoding="utf-8").write(html)
    print(json.dumps({"bytes": len(html), "rows": len(out), "with_eps": sum(1 for r in out if r["size"] is not None)}))


if __name__ == "__main__":
    main(*sys.argv[1:7])
