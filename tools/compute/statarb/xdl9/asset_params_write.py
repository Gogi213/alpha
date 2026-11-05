#!/usr/bin/env python3
"""TK-493 F5: записывает результаты калибровки и таблицы «один актив» в docs/research/P-37-params-2026-11-06.csv (строки по ключам; прочие строки не трогает,
построчная замена, формат кавычек прежний: источник всегда в кавычках, прочие поля - по необходимости).
Вход: data/xdl9/calib_summary.json, data/xdl9/calib_pairs.csv, docs/findings/xdl9-asset-map-2026-11-06/manifest.json, table_summary.json.
Запуск: python asset_params_write.py [хеш-коммита-таблицы]   (хеш - коммит скриптов и отчёта F5, на который ссылаются строки)."""
import csv, json, os, sys
import asset_lib as L

PARAMS = os.path.join(L.ROOT, "docs", "research", "P-37-params-2026-11-06.csv")
DOC = os.path.join(L.ROOT, "docs", "findings", "xdl9-asset-map-2026-11-06")
MEAS = "измерено на формировании, подтвердить"
FAIL = "калибровка не удалась (нет зазора), решение CEO/Судьи"
ASSIGN = "назначено без Судьи, подтвердить"
WHEN = "F5 06.11"
NL = chr(10)


def main():
    commit = sys.argv[1] if len(sys.argv) > 1 else "(хеш подставить)"
    cal = json.load(open(os.path.join(L.DATA, "calib_summary.json"), encoding="utf-8"))
    man = json.load(open(os.path.join(DOC, "manifest.json"), encoding="utf-8"))
    ts = json.load(open(os.path.join(DOC, "table_summary.json"), encoding="utf-8"))
    calp = list(csv.DictReader(open(os.path.join(L.DATA, "calib_pairs.csv"), encoding="utf-8")))
    same = [r for r in calp if r["kind"] == "одно"]
    diff = [r for r in calp if r["kind"] == "разное"]
    c, d, s = cal["corr"], cal["dev"], cal["S"]
    diffB = [r for r in diff if not r["group"].startswith("XAU")]       # вариант B отчёта: без яруса XAU против XAUT/PAXG
    cB = (min(float(r["corr"]) for r in same) + max(float(r["corr"]) for r in diffB)) / 2
    dB = (max(float(r["dev"]) for r in same) + min(float(r["dev"]) for r in diffB)) / 2
    th = ts["th"]

    raw = open(PARAMS, encoding="utf-8", newline="").read().split(NL)
    trailing = raw[-1] == ""
    lines = raw[:-1] if trailing else raw
    idx = {}
    for i, ln in enumerate(lines):
        if i == 0 or not ln:
            continue
        idx[next(csv.reader([ln]))[0]] = i

    def q(x, always=False):
        x = str(x)
        if always or any(ch in x for ch in (",", '"', NL)):
            return '"' + x.replace('"', '""') + '"'
        return x

    def put(key, value, status, when, source):
        line = ",".join([q(key), q(value), q(status), q(when), q(source, always=True)])
        if key in idx:
            lines[idx[key]] = line
        else:
            idx[key] = len(lines)
            lines.append(line)

    ref = "docs/findings/xdl9-asset-map-2026-11-06.md; калибровка calib_summary.json; коммит F5 %s" % commit
    put("stab_threshold", "%.8f" % s["mid"], MEAS, WHEN,
        "P-37 §4.2 [Н5, Н29]: середина зазора: худший S «одно» %.8f (BTC Bitget-Kraken) | лучший S «разное» %.8f (XAU-XAUT, нога Kraken); "
        "применён на ОБОИХ путях (М и К) - отступление от r2 (отчёт §2.4); %s" % (s["worst_same"], s["best_diff"], ref))
    put("stab_threshold_upper_edge", "%.8f" % s["best_diff"], MEAS, WHEN,
        "наименьший S среди заведомо «разных» основного яруса; ворота «дубль внутри биржи»: S < этого значения и n_ret >= n_ret_min; "
        "пары между биржами с S между серединой и этим значением - «пограничное» (в C1/D2 не идут); %s" % ref)
    put("corr_threshold", "не определён", FAIL, WHEN,
        "P-37 §4.2 [Н5]: нет зазора - худший corr «одно» %.5f (BTC Lighter-Aster) < лучший corr «разное» %.5f (XAU-PAXG Bitget-Binance); "
        "справочно середина без яруса XAU/XAUT/PAXG: %.5f (в R2' corr воротами не служит); %s" % (c["worst_same"], c["best_diff"], cB, ref))
    put("ratio_band", "не определена", FAIL, WHEN,
        "P-37 §4.2 [Н5]: нет зазора - худшее отклонение «одно» %.3e (BTC Bitget-Kraken) > лучшее «разное» %.3e (XAU-PAXG); справочно середина без яруса "
        "XAU: %.5f (выше допуска Н4 0.02 не берётся); в R2' полоса воротами не служит, dev выводится; %s" % (d["worst_same"], d["best_diff"], dB, ref))
    put("n_ret_min", str(th["n_ret_min"]), ASSIGN, WHEN,
        "минимум общих минут с доходностью у пары (обе ноги валидны в t и t-1) для вердикта; ниже - «нет данных»; число назначено в F5, источника нет; "
        "распределение пар ниже порога - отчёт Т7")
    put("verdict_rule_r2prime",
        "одно: n_ret >= n_ret_min и S <= stab_threshold (оба пути); пограничное: stab_threshold < S < stab_threshold_upper_edge; разное: S >= stab_threshold_upper_edge; "
        "класс не совпал - отдельный вердикт; дубль внутри биржи: S < stab_threshold_upper_edge (или политика HKD)",
        ASSIGN, WHEN,
        "отступление от r2 §4.2: по r2 буквально калибровка corr и ratio_band не даёт зазора (XAU против XAUT/PAXG), работает только S; поправка R2': S-ворота на обоих "
        "путях, corr и dev выводятся; отчёт §2.4; предложить в P-37 §17; " + ref)
    put("calibration_symbols",
        "BTC и ETH: binance *USDT, bybit *USDT, okx *-USDT-SWAP, bingx *-USDT, aster *USDT, kraken PF_XBTUSD/PF_ETHUSD, lighter BTC/ETH, bitget *USDT (28+28 пар); "
        "DASH: bingx NCSKDASH2USD-USDT против DASH-крипты 8 бирж (bitget DASHSTOCKUSDT - 0 валидных минут, не использован); MEME: aster MEMEUSDT против binance/bybit/okx/bingx/bitget; "
        "AI: okx AI-USDT-SWAP против aster AIUSDT и lighter AI; ETHBTC: binance ETHBTC и bybit ETHBTCUSDT против ETH 8 бирж; XAU против XAUT/PAXG: 8 x (5+7) комбинаций; "
        "всего пар %d (одно %d, разное %d); полный список - calib_pairs.csv" % (len(calp), len(same), len(diff)),
        MEAS, WHEN, "P-37 §4.2 [Н30]; пропущено по n_ret < 300 или нет данных: %d пар (calib_summary.json)" % len(cal["skipped"]))
    put("alias_dictionary",
        "tools/compute/statarb/xdl9/asset_alias.csv: 50 записей (hk_leg 6, rename 43, extra 1) + встроенные словари металлов, энергии, индексов, FX (asset_cands.py) "
        "+ суффиксы STOCK/HKD/x/rh/USD и приставки множителя (asset_lib.norm_key); словарь только предлагает кандидатов, решает измеренное правило",
        ASSIGN, WHEN,
        "P-37 §4.2 [Н29]; 43 переименования найдены сканом «сирот» по корреляции (data-informed) и просмотрены глазами; не подтверждены источником биржи")
    put("table_asset_map",
        "contracts=%d, main=4939, dup=249, excluded=372; asset_map.csv sha256=%s" % (man["asset_map.csv"]["rows"], man["asset_map.csv"]["sha256"]),
        "из источника", WHEN, "данные F5 (data/xdl9/, gitignored); " + ref)
    put("table_asset_pairs",
        "pairs=%d (между биржами 12549, внутри биржи 286); годных для C1/D2 (вердикт «одно», обе ноги основные или HKD-ноги, класс совпал) = 7985; asset_pairs.csv sha256=%s" % (
            man["asset_pairs.csv"]["rows"], man["asset_pairs.csv"]["sha256"]),
        "из источника", WHEN, "данные F5 (data/xdl9/, gitignored); " + ref)
    put("table_audit_g4",
        "случайные 50: без ошибки 37, нет данных при том же активе 11, пограничное при том же активе 1, ложное «разное» 1, ложных пар 0; пограничные 30: все 30 - один актив, "
        "«одно» 14, «пограничное» 16",
        "из источника", WHEN, "G4; docs/findings/xdl9-asset-map-2026-11-06/audit_result.csv")
    with open(PARAMS, "w", encoding="utf-8", newline="") as fh:
        fh.write(NL.join(lines) + (NL if trailing else ""))
    print("params: %d строк" % (len(lines) - 1))


if __name__ == "__main__":
    main()
