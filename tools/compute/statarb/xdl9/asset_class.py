#!/usr/bin/env python3
"""TK-493 w4 (D-01, П-37 r3 §4.1 [Н42]): ОДНА функция классификации контрактов для asset_lib.py (таблица «один актив») и panels_xdl9.py (манифест панелей).

Только stdlib (работает на Steam Deck, Python 3.14, и на ПК). Торгового окна не читает: вход - строка instruments.csv.

Что решается:
  1. пред-листинг по ПЕРВИЧНЫМ полям биржи (не по подстрокам в тексте `note`): Bybit `status = PreLaunch` / `isPreListing=true`,
     Binance `underlyingType=PREMARKET`, OKX `ruleType=pre_market`, Aster `underlyingSubType` содержит `pre-launch`; сверх списка r3
     (назначено без Судьи, подтвердить): Kraken `category=Pre-IPO`, Hyperliquid `perpCategories=preipo` - те же первичные поля;
  2. приватные базы: база (после нормализации имени), помеченная пред-листинговой/приватной хотя бы на одной бирже, исключается на ВСЕХ
     биржах в своей группе («компания»: equity/etf/other; «крипта»: crypto - чтобы токен BP не вычеркнул акцию BP plc);
  3. датированный фьючерс (Kraken `FF_...`);
  4. класс `other`: явное решение «в тест / вне» по файлу `asset_other_resolution.csv` (нет строки - вне теста);
  5. укрупнённый класс П-37 §4.1.
Признак из первичных полей виден отдельными колонками `pre_listing`, `pre_listing_src` (материализует `inst_prelisting_col.py`);
функция считает его из первичных полей строки и не зависит от того, есть колонка или нет (при наличии колонка обязана совпасть).
Откуда берутся поля: tools/xdl/bybit.py:163, binance_aster.py:81,96, okx.py:53, kraken_lighter.py:117, hyperliquid.py:76 - записывают
значение поля API как `ключ=значение` в class_src/note/status; прозу из `note` функция не разбирает.
"""
import csv, os, re
from collections import namedtuple

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_OF_TEST = "вне теста"
# П-37 §4.1 (Н3): укрупнённые классы
BIG = {"crypto": "крипта", "equity": "акции+ETF", "etf": "акции+ETF", "index": "индексы", "metal": "металлы+энергия",
       "energy": "металлы+энергия", "commodity": "металлы+энергия", "fx": "фиат"}
# приватные базы, названные в П-37 r3 §4.1 (семя; остальные выводятся из помеченных контрактов)
PRIVATE_SEED = ("OURA", "OPENAI", "ANTHROPIC", "POLYMARKET", "MOONSHOT")
COMPANY_CLASSES = ("equity", "etf", "other")
RESOLUTION_FILE = os.path.join(HERE, "asset_other_resolution.csv")

Cls = namedtuple("Cls", "big kind reason pre dated")      # pre: 1 = вне теста как пред-листинг/приватная база; dated: 1 = датированный


# ---------------------------------------------------------------- первичные поля
def cs_fields(class_src):
    """`exchange:underlyingType=PREMARKET|sub=Pre-IPO+TradFi` -> {'underlyingType': ['PREMARKET'], 'sub': ['Pre-IPO', 'TradFi']}."""
    out = {}
    for part in (class_src or "").split("|"):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.split(":")[-1].strip()] = [t.strip() for t in v.split("+")]
    return out


_NOTE_KV = re.compile(r"(?:^|[\s;(])([A-Za-z][A-Za-z0-9_]*)=([A-Za-z0-9_\-]+)")


def note_kv(note):
    """Токены `ключ=значение`, записанные скачивателем из полей API (`ruleType=pre_market`, `isPreListing=true`); проза не разбирается."""
    out = {}
    for k, v in _NOTE_KV.findall(note or ""):
        out.setdefault(k, []).append(v)
    return out


# (биржа, источник, ключ, значение): источник cs = class_src, note = токены note, col = колонка instruments.csv
PRELISTING_RULES = (
    ("bybit", "col", "status", "PreLaunch"),
    ("bybit", "note", "isPreListing", "true"),
    ("binance", "cs", "underlyingType", "PREMARKET"),
    ("okx", "note", "ruleType", "pre_market"),
    ("aster", "cs", "underlyingSubType", "pre-launch"),
    ("kraken", "cs", "category", "Pre-IPO"),
    ("hyperliquid", "cs", "perpCategories", "preipo"),
)


def prelisting(row):
    """-> (0|1, источник правила) по первичным полям биржи; источник '' если признака нет."""
    ex = row.get("exchange", "")
    cs = None
    nk = None
    for rex, src, key, val in PRELISTING_RULES:
        if rex != ex:
            continue
        if src == "col":
            hit = row.get(key, "") == val
        elif src == "cs":
            cs = cs_fields(row.get("class_src", "")) if cs is None else cs
            hit = val in cs.get(key, [])
        else:
            nk = note_kv(row.get("note", "")) if nk is None else nk
            hit = val in nk.get(key, [])
        if hit:
            return 1, "%s %s=%s" % (ex, key, val)
    return 0, ""


# ---------------------------------------------------------------- нормализация имени (одна на asset_lib и классификацию)
MULT_RE = re.compile(r"^(1000000|100000|10000|1000|1M)(?=[A-Z])")


def norm_key(d):
    """-> (ключ актива, список меток нормализации). Ключ только ПРЕДЛАГАЕТ кандидатов (словарь псевдонимов, П-37 §4.2 [Н29]);
    решение «один актив» принимает измеренное правило."""
    tags = []
    b = d["base"]
    ex = d["exchange"]
    cl = d["class"]
    xstock = ex == "kraken" and (cl in ("equity", "etf") or "xStocks" in cs_fields(d.get("class_src", "")).get("category", []))
    if xstock and b.endswith("x") and len(b) > 2:                 # Kraken xStocks: класс other у SPCXx не мешает снять суффикс (аудит 20 other)
        b = b[:-1]
        tags.append("kraken_xstock")
    if ex == "lighter" and re.match(r"^rh[A-Z]", b):
        b = b[2:]
        tags.append("lighter_rh")
    b = b.upper()
    if ex == "lighter" and cl in ("equity", "etf") and b.endswith("USD") and len(b) > 4:
        b = b[:-3]
        tags.append("lighter_usd")
    if cl in ("equity", "etf") and b.endswith("STOCK") and len(b) > 5:
        b = b[:-5]
        tags.append("suffix_STOCK")
    if b.endswith("HKD") and len(b) > 5 and cl in ("equity", "etf"):
        b = b[:-3]
        tags.append("suffix_HKD")
    m = MULT_RE.match(b)
    if m:
        b = b[len(m.group(1)):]
        tags.append("mult_" + m.group(1))
    m2 = re.match(r"^([A-Z]{2,})(1000)$", b)           # суффикс множителя (Bybit SHIB1000)
    if m2:
        b = m2.group(1)
        tags.append("mult_" + m2.group(2))
    if d["quote"] == "BTC" and not b.endswith("BTC"):
        b = b + "BTC"
        tags.append("quote_BTC")
    return b, tags


# ---------------------------------------------------------------- метки региона биржи (П-37 §9, r3.2 D6 [Н56])
_REGION_BYBIT = re.compile(r"region=(\w*) ticker")
_REGION_BITGET = re.compile(r"листинг ([A-Z]+)")
_REGION_BINANCE = re.compile(r"(HK|KR|CN)_EQUITY")


def region_labels(rows):
    """Метки региона листинга базового актива из ПЕРВИЧНЫХ полей бирж (не из данных): Bybit `region=XX` (note), Bitget `листинг XX` (note),
    Binance `HK|KR|CN_EQUITY` (class_src). Hyperliquid не берётся. -> {ключ актива (norm_key): {'биржа:XX'}}. Метка `XX = US` — «США»,
    любая другая — «не-США». Правило США/не-США (sel_catalog.py): метка биржи приоритетна, доля дисперсии в кассе — только запасное (D6)."""
    lab = {}
    for r in rows:
        ex = r.get("exchange", "")
        if ex == "hyperliquid":
            continue
        found = []
        if ex == "bybit":
            m = _REGION_BYBIT.search(r.get("note", ""))
            if m and m.group(1):
                found.append("bybit:" + m.group(1))
        elif ex == "bitget":
            m = _REGION_BITGET.search(r.get("note", ""))
            if m:
                found.append("bitget:" + m.group(1))
        elif ex == "binance":
            m = _REGION_BINANCE.search(r.get("class_src", ""))
            if m:
                found.append("binance:" + m.group(1))
        if found:
            k, _ = norm_key(r)
            lab.setdefault(k, set()).update(found)
    return lab


def region_verdict(labels):
    """Решение по меткам актива: ('не-США' | 'США' | None, текст причины). Любая метка не-США побеждает метку США; нет меток - None (запасное правило по данным)."""
    non = sorted(x for x in labels if not x.endswith(":US"))
    us = sorted(x for x in labels if x.endswith(":US"))
    if non:
        return "не-США", "метка биржи: " + ",".join(non)
    if us:
        return "США", "метка биржи: " + ",".join(us)
    return None, ""


# ---------------------------------------------------------------- приватные базы
def group_of(cls):
    """Группа для ключа приватной базы: компания (equity/etf/other) или сам класс (crypto)."""
    return "company" if cls in COMPANY_CLASSES else cls


def derive_private_bases(rows):
    """rows: строки instruments.csv ВСЕХ бирж (в том числе Hyperliquid). -> {(ключ базы, группа): [источники правил по биржам]}.
    Семя П-37 r3 §4.1 (группа «компания») + база каждого контракта с признаком пред-листинга (группа - по его классу)."""
    pb = {(k, "company"): ["П-37 r3 §4.1 (список)"] for k in PRIVATE_SEED}
    for r in rows:
        pre, src = prelisting(r)
        if not pre:
            continue
        key, _ = norm_key(r)
        lst = pb.setdefault((key, group_of(r.get("class", ""))), [])
        if src not in lst:
            lst.append(src)
    return pb


# ---------------------------------------------------------------- класс other: явное решение
def load_resolution(path=RESOLUTION_FILE):
    """-> {(биржа, символ): (решение 'в тест'|'вне', класс_новый, основание)}."""
    out = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                out[(r["exchange"], r["symbol"])] = (r["decision"], r["class_new"], r["basis"])
    return out


def is_dated(row):
    """Датированный фьючерс: Kraken `FF_...` (перпетуалы `PF_...`)."""
    return int(row.get("exchange") == "kraken" and row.get("symbol", "").startswith("FF_"))


# ---------------------------------------------------------------- единая функция
def classify(row, private_bases=None, resolution=None):
    """Единая классификация контракта -> Cls(big, kind, reason, pre, dated).
    kind: pre_listing | private_base | dated | other_out | other_in | ok | unknown_class."""
    pb = private_bases if private_bases is not None else {(k, "company"): ["П-37 r3 §4.1 (список)"] for k in PRIVATE_SEED}
    res = resolution if resolution is not None else {}
    pre, src = prelisting(row)
    if pre:
        return Cls(OUT_OF_TEST, "pre_listing", "вне теста: пред-листинг, первичное поле (%s)" % src, 1, 0)
    cls = row.get("class", "")
    key, _ = norm_key(row) if row.get("base") is not None and row.get("quote") is not None else ("", [])
    hit = pb.get((key, group_of(cls))) if key else None
    if hit:
        return Cls(OUT_OF_TEST, "private_base", "вне теста: приватная база %s (помечена: %s)" % (key, "; ".join(hit)), 1, 0)
    if is_dated(row):
        return Cls(OUT_OF_TEST, "dated", "вне теста: датированный фьючерс", 0, 1)
    if cls == "other":
        dec = res.get((row.get("exchange", ""), row.get("symbol", "")))
        if dec and dec[0] == "в тест" and dec[1] in BIG:
            return Cls(BIG[dec[1]], "other_in", "класс other -> %s: %s" % (dec[1], dec[2]), 0, 0)
        return Cls(OUT_OF_TEST, "other_out", "вне теста: класс other (%s)" % (dec[2] if dec else "решение не записано"), 0, 0)
    if cls in BIG:
        return Cls(BIG[cls], "ok", "", 0, 0)
    return Cls(OUT_OF_TEST, "unknown_class", "вне теста: класс %r не из П-37 §4.1" % cls, 0, 0)
