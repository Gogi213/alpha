#!/usr/bin/env python3
"""TK-493 F5, G4: итог ручного аудита (50 случайных + 30 пограничных пар). «Истина» - решение просмотром глазами: имена, тикеры, класс, уровни цен
(медианы закрытий), отношение цен, корреляция, дата листинга. Автоматической проверки истины нет; решение каждой пары - в TRUTH ниже
(S - тот же актив / D - разные активы), примечание - только где вердикт таблицы расходится с истиной или есть оговорка.
Типы ошибок: ЛП - ложная пара (вердикт «одно», истина D); ЛР - ложное «разное» (S, вердикт «разное»); ПГ - «пограничное» при истине S
(не принято в C1/D2: потеря охвата); НД - «нет данных» при истине S (потеря охвата, не ошибка вердикта); ЯР - ошибка ярлыка (вердикт не тот,
хотя пара исключена верно). Вывод: data/xdl9/audit_result.csv + печать итога."""
import csv, os, collections
import asset_lib as L

S_, D_ = "S", "D"
TRUTH = {  # id: (истина, примечание)
    "R-03": (D_, "Bybit BBX (акция BlackBerry, 9.08) против Aster BB (крипто, 0.0095): разные активы; вердикт был «нет данных» вместо «класс не совпал» (порядок условий исправлен в asset_table.py, ярлык)"),
    "R-13": (D_, "Kraken PF_QNTUSD (крипто QNT, 244) против Bitget QNTSTOCK (акция, 44.2): разные"),
    "R-41": (D_, "BingX QNT-USDT (крипто) против Bitget QNTSTOCK (акция): разные"),
    "R-18": (S_, "BingX и Bybit GILD (акция Gilead), цены 147.26 / 147.61, отношение 1.00007; S=0.0721 чуть выше порога; вердикт «пограничное» - актив тот же"),
    "R-35": (S_, "BingX и Bybit SDGR (акция), отношение 0.9989, обе ноги тонкие (нулевых минут 80 %); S=0.135 из-за малого σ уровня 0.0216 - ложное «разное»"),
}
SAME_DEFAULT = True
NOTES_SAME = {
    "R-04": "обе ноги тонкие (Lighter 94 % нулевых), n_ret=57 < 300", "R-05": "n_ret=32", "R-15": "n_ret=37", "R-16": "n_ret=120",
    "R-25": "n_ret=17", "R-30": "n_ret=97", "R-32": "n_ret=215 (Bitget WIFPERP - дубль)", "R-34": "n_ret=3", "R-38": "n_ret=218", "R-39": "n_ret=2",
    "R-40": "n_ret=22", "R-12": "нога BingX NEAR-USDC - дубль, c1d2=0 верно", "R-24": "обе ноги дубли (USDC/PERP), c1d2=0 верно",
    "R-26": "нога Bybit MOODENGPERP - дубль, c1d2=0 верно", "R-42": "нога Bybit STRKPERP - дубль, c1d2=0 верно", "R-47": "нога Binance AAVEUSDC - дубль, c1d2=0 верно",
    "R-48": "нога Bybit AAVEPERP - дубль, c1d2=0 верно",
}

def main():
    rows = list(csv.DictReader(open(os.path.join(L.DATA, "audit_sample.csv"), encoding="utf-8")))
    out = []
    for r in rows:
        truth, note = TRUTH.get(r["id"], (S_, NOTES_SAME.get(r["id"], "")))
        v = r["verdict"]
        if v == "одно":
            err = "" if truth == S_ else "ЛП"
        elif v == "разное":
            err = "ЛР" if truth == S_ else ""
        elif v.startswith("пограничное"):
            err = "ПГ" if truth == S_ else ""
        elif v.startswith("нет данных"):
            err = "НД" if truth == S_ else ("ЯР" if truth == D_ and not v.startswith("класс") else "")
            if truth == D_ and r["cls1"] != "" and r["id"] == "R-03":
                err = "ЯР"
        elif v.startswith("класс не совпал"):
            err = "" if truth == D_ else "ЛР"
        else:
            err = "?"
        out.append(dict(id=r["id"], kind=r["kind"], key=r["key"], leg1="%s:%s" % (r["ex1"], r["sym1"]), leg2="%s:%s" % (r["ex2"], r["sym2"]),
                        px1=r["px1"], px2=r["px2"], ratio=r["ratio"], corr=r["corr"], S=r["S"], n_ret=r["n_ret"], verdict=v, c1d2=r["c1d2"],
                        truth="тот же актив" if truth == S_ else "разные активы", err=err, note=note))
    keys = list(out[0].keys())
    with open(os.path.join(L.DATA, "audit_result.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys); w.writeheader(); w.writerows(out)
    for kind in ("случайная", "пограничная"):
        sub = [o for o in out if o["kind"] == kind]
        c = collections.Counter(o["err"] for o in sub)
        vs = collections.Counter(o["verdict"][:12] for o in sub)
        tr = collections.Counter(o["truth"] for o in sub)
        print(kind, len(sub), "вердикты", dict(vs), "истина", dict(tr), "ошибки", dict(c))
    return out

if __name__ == "__main__":
    main()
