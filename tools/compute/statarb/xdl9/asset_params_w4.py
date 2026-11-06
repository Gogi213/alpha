#!/usr/bin/env python3
"""TK-493 w4 (D-01): записывает в docs/research/P-37-params-2026-11-06.csv ТОЛЬКО значения строк table_asset_map и table_asset_pairs (новые хеши таблиц) и
добавляет строки решений w4. Прочие строки и их текст не трогает (построчная замена, формат кавычек прежний: источник всегда в кавычках).
Вход: data/xdl9/asset_map.csv, asset_pairs.csv, table_summary.json, tools/.../asset_other_resolution.csv, data/xdl9/inst/*.csv.
Запуск: python asset_params_w4.py <хеш-коммита-кода-w4>   (хеш - коммит скриптов и отчёта, на который ссылаются строки)."""
import csv, glob, hashlib, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
DATA = os.path.join(ROOT, "data", "xdl9")
PARAMS = os.path.join(ROOT, "docs", "research", "P-37-params-2026-11-06.csv")
NL = chr(10)
ASSIGN = "назначено без Судьи, подтвердить"
MEAS = "измерено на формировании, подтвердить"
SRC = "из источника"
REPORT = "docs/findings/xdl9-asset-map-prelisting-2026-11-06.md"
OLD_MAP = "2c0503ba9e8ff752456573e421c03fee8e6e61341fe3bc5d6de892684b62df06"       # F5 (строка params до w4)
OLD_PAIRS = "1163d0607208e454e2057ceb7a98930c68dede8b4be606e3f7250a0cc3824f07"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def main():
    commit = sys.argv[1] if len(sys.argv) > 1 else "(хеш подставить)"
    ts = json.load(open(os.path.join(DATA, "table_summary.json"), encoding="utf-8"))
    cnt = ts["counts"]
    between = sum(v for k, v in cnt.items() if k.startswith("между биржами"))
    within = sum(v for k, v in cnt.items() if k.startswith("внутри биржи"))
    with open(os.path.join(DATA, "asset_map.csv"), encoding="utf-8", newline="") as fh:
        am = list(csv.DictReader(fh))
    with open(os.path.join(DATA, "asset_pairs.csv"), encoding="utf-8", newline="") as fh:
        ap = list(csv.DictReader(fh))
    roles = {r: sum(1 for x in am if x["role"] == r) for r in ("main", "dup", "excluded")}
    usable = sum(int(x["c1d2"]) for x in ap)
    h_map, h_pairs = sha(os.path.join(DATA, "asset_map.csv")), sha(os.path.join(DATA, "asset_pairs.csv"))
    h_res = sha(os.path.join(HERE, "asset_other_resolution.csv"))
    h_inst = hashlib.sha256()
    for fn in sorted(glob.glob(os.path.join(DATA, "inst", "*.csv"))):
        with open(fn, "rb") as fh:
            h_inst.update(fh.read())

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

    when = "w4 06.11"
    ref = "данные F5, пересобраны w4 (data/xdl9/, gitignored); docs/findings/xdl9-asset-map-2026-11-06.md; %s; калибровка calib_summary.json не менялась; коммит w4 %s" % (REPORT, commit)
    put("table_asset_map", "contracts=%d, main=%d, dup=%d, excluded=%d; asset_map.csv sha256=%s; было в F5: main=4939, excluded=372, sha256=%s"
        % (len(am), roles["main"], roles["dup"], roles["excluded"], h_map, OLD_MAP), SRC, "F5 06.11; пересобрано w4 06.11", ref)
    put("table_asset_pairs", "pairs=%d (между биржами %d, внутри биржи %d); годных для C1/D2 (вердикт «одно», обе ноги основные или HKD-ноги, класс совпал) = %d; "
        "asset_pairs.csv sha256=%s; было в F5: pairs=12835, годных 7985, sha256=%s" % (len(ap), between, within, usable, h_pairs, OLD_PAIRS),
        SRC, "F5 06.11; пересобрано w4 06.11", ref)
    put("other_class_rule",
        "класс other (8 минутных бирж, 74 контракта): пред-листинг | приватная база | запись asset_other_resolution.csv; «в тест», если у контракта с тем же ключом имени "
        "на другой бирже вердикт «одно» (n_ret >= n_ret_min и S <= stab_threshold) и у таких контрагентов один укрупнённый класс П-37 (класс наследуется), иначе «вне»; "
        "итог: в тест 22 (акции+ETF 21, крипта 1), вне по измерению 24, вне без валидных минут 5, приватная база 8, пред-листинг 15; asset_other_resolution.csv sha256=%s" % h_res,
        ASSIGN, when,
        "P-37 §4.1 [Н3]: other вне теста; w4 (хвост F5): решение явно по контракту; аудит 20 случайных (seed 37 = partition_seed): суждение совпало в 15 из 16, расхождение Bitget ZCSH "
        "(короткое перекрытие, S 0.10-0.12), 4 без суждения; %s; asset_other_resolve.py, asset_other_audit.py; коммит w4 %s" % (REPORT, commit))
    put("prelisting_fields_ext",
        "расширение prelisting_fields: kraken category=Pre-IPO | hyperliquid perpCategories=preipo (те же первичные поля; Hyperliquid в минутный тест не входит, его пред-IPO дают только ключи баз)",
        ASSIGN, when, "P-37 §4.1 [Н42], D-01; asset_class.PRELISTING_RULES; %s; коммит w4 %s" % (REPORT, commit))
    put("private_bases_groups",
        "ключ приватной базы действует в группе: компания (equity|etf|other) или крипта (crypto); токен BP (Bybit PreLaunch) не вычёркивает акцию BP plc; ключи из помеченных контрактов: "
        "компания ANT ANTH ANTHROPIC MOONSHOT OAI OPENAI OURA POLYMARKET SPACEX, крипта BP PAPER PREDICT XTEN; BPUSDT вне теста по первичному полю Bybit",
        ASSIGN, when, "P-37 §4.1 [Н42], D-01: «решение о BP отдельно»; asset_class.derive_private_bases; %s; коммит w4 %s" % (REPORT, commit))
    put("prelisting_excluded_counts",
        "вне теста как пред-листинг / приватная база на 8 минутных биржах: 44 (первичное поле 30: aster 10, binance 5, bybit 7, kraken 4, okx 4; ключ базы 14: bingx 5, bitget 5, lighter 4); "
        "было в тесте как main 7 (bybit ANTHROPIC MOONSHOT OPENAI OURA POLYMARKET BP, bingx NCSKOURA2USD-USDT); прежние счётчики F4 18, F5 25, аудит 32",
        MEAS, when, "P-37 §4.1, D-01; asset_d01_diff.py; docs/findings/xdl9-asset-map-prelisting-2026-11-06/excluded_new.csv; %s" % REPORT)
    put("inst_prelisting_columns",
        "колонки pre_listing, pre_listing_src в instruments.csv: data/xdl9/inst/<биржа>.csv (ПК) и ~/xdl9/inst/<биржа>.csv (Deck), побайтно одинаковы; сырьё ~/xdl/out/<биржа>/instruments.csv не менялось; "
        "sha256 конкатенации 9 файлов по алфавиту = %s" % h_inst.hexdigest(),
        SRC, when, "inst_prelisting_col.py (--check сверяет с asset_class.prelisting); %s; коммит w4 %s" % (REPORT, commit))
    put("us_rule_accuracy",
        "правило США/не-США F7 (sel_us_nonus_rule): выборка 40 активов (seed 37; 20 'не-США' + 20 'США'): совпало 31 (78 %); 'не-США' верно у 12 из 20, 'США' верно у 19 из 20; "
        "все 66 'не-США': по метке биржи верно 27 из 27, по доле дисперсии в кассе верно 11 из 39 (американских тикеров 23, АДР 3, частных 2); метки бирж не-США региона пропущены у 19 из 54 активов",
        MEAS, when, "P-37 §9 [Н22]; истина - суждение по тикеру/названию (asset_us_rule_truth.csv); asset_us_rule_audit.py; docs/findings/xdl9-asset-map-prelisting-2026-11-06/us_rule_*.csv; правило не менялось")
    with open(PARAMS, "w", encoding="utf-8", newline="") as fh:
        fh.write(NL.join(lines) + (NL if trailing else ""))
    print("params: table_asset_map/table_asset_pairs обновлены, строк теперь %d" % (len(lines) - 1))


if __name__ == "__main__":
    main()
