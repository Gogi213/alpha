"""Сравнение двух closes-*.json по сделкам: python3 cmp_closes.py <новый> <эталон> - число и первые расхождения (разбор гейта М5)."""
import json, sys


def flat(p):
    return [(v, per, c, tuple(x)) for v, pp in json.load(open(p, encoding="utf-8")).items() for per, cc in pp.items() for c, l in cc.items() for x in l]


a, b = flat(sys.argv[1]), flat(sys.argv[2])
sa, sb = set(map(str, a)), set(map(str, b))
print(len(a), len(b), "только новый:", sorted(sa - sb)[:5], "только эталон:", sorted(sb - sa)[:5])
