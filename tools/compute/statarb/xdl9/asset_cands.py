#!/usr/bin/env python3
"""TK-493 F5: кандидаты «один актив» по ИМЕНИ (словарь псевдонимов, П-37 §4.2 [Н29]) и измеренные статистики пар - формирование 01.10-14.10.

Стадия A (этот файл): внутри биржи - кластеры одного актива (дубли USDT/USDC/PERP/1000X/HKD), между биржами - пары основных ног.
Решение «одно/разное» принимает asset_rule.py по порогам из calib_summary.json; здесь - только кандидаты и сырые статистики.
"""
import collections, csv, itertools, json, math, os, re
import numpy as np
import asset_lib as L

USD_LIKE = {"USD", "USDT", "USDC", "USD1", "U", ""}

# ---- словарь псевдонимов для классов без общих тикеров (металлы, энергия, индексы, FX): только ПРЕДЛАГАЕТ кандидатов
ALIAS = {
    # металлы
    "GOLD": "XAU", "SILVER": "XAG", "COPPER": "COPPER", "XCU": "COPPER",
    # энергия
    "CL": "WTI", "WTIOIL": "WTI", "OILWTI": "WTI", "BZ": "BRENT", "BRENTOIL": "BRENT", "OILBRENT": "BRENT",
    "NG": "NATGAS",
    # индексы (BingX: display=KR200 у KOSPI; display=GER40 у GER)
    "US500": "SP500", "US100": "NASDAQ100", "NDX100": "NASDAQ100", "JP225": "NIKKEI225", "KOSPI": "KR200", "GER": "GER40",
}


_ALIAS = None


def load_alias():
    """asset_alias.csv: (биржа, base) -> (key, kind)."""
    global _ALIAS
    if _ALIAS is None:
        _ALIAS = {}
        p = os.path.join(L.HERE, "asset_alias.csv")
        if os.path.exists(p):
            for r in csv.DictReader(open(p, encoding="utf-8", newline="")):
                _ALIAS[(r["exchange"], r["base"])] = (r["key"], r["kind"])
    return _ALIAS


def pair_key(d):
    """-> (ключ, метки). Базовая нормализация + словарь псевдонимов + FX/кросс-котировки.
    Побочно: d['keys_extra'] - дополнительные ключи (kind extra)."""
    key, tags = L.norm_key(d)
    d["keys_extra"] = []
    al = load_alias().get((d["exchange"], d["base"]))
    if al:
        if al[1] == "extra":
            d["keys_extra"].append(al[0]); tags.append("alias_extra_" + al[0])
        else:
            key = al[0]; tags.append("alias_dict_" + al[1])
            if al[1] == "hk_leg":
                tags.append("hk_leg")
    cl = d["class"]
    q = d["quote"]
    if cl == "fx":
        if len(key) == 6:
            pass
        elif q in USD_LIKE:
            key = key + "USD"
        else:
            key = key + q
        tags.append("fx_pair")
    elif cl in ("metal", "energy", "commodity", "index"):
        if cl == "metal" and q not in USD_LIKE and q not in ("",):
            key = key + q                       # кросс XAUAUD, отношение XAUXAG: другой актив
            tags.append("cross_quote_" + q)
        if key in ALIAS:
            tags.append("alias_" + key)
            key = ALIAS[key]
    return key, tags


def is_hkd(d):
    return "suffix_HKD" in d["tags"] or "hk_leg" in d["tags"]


def dup_reason(d, m):
    """Причина, по которой контракт d - дубль основного m на той же бирже (по признакам имён, не по измерению)."""
    why = []
    if d["quote"] != m["quote"]:
        why.append("котировка %s вместо %s" % (d["quote"], m["quote"]))
    if d["symbol"].endswith("PERP"):
        why.append("PERP (USDC)")
    if "inverse" in d.get("note", ""):
        why.append("инверсный (в монете)")
    if "AOS2" in d.get("class_src", "") or "AOS2" in d.get("note", ""):
        why.append("AOS2-контракт")
    t_d, t_m = d["tags"], m["tags"]
    md = [t for t in t_d if t.startswith("mult_")]
    mm = [t for t in t_m if t.startswith("mult_")]
    if md != mm:
        why.append("множитель %s вместо %s" % (md[0][5:] if md else "1", mm[0][5:] if mm else "1"))
    if is_hkd(d) and not is_hkd(m):
        why.append("нога в HKD")
    if "suffix_STOCK" in t_d and "suffix_STOCK" not in t_m:
        why.append("суффикс STOCK")
    return "; ".join(why) if why else "то же имя"


def build(rows, C):
    """Добавляет к rows: key, tags, big. Возвращает группы {(ex, key, big): [contracts]}."""
    for d in rows:
        d["big"] = L.big_class(d)
        d["key"], d["tags"] = pair_key(d)
    return rows
