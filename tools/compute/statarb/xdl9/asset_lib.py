#!/usr/bin/env python3
"""TK-493 F5: общие функции таблицы «один актив» (только формирование 01.10-14.10 UTC; торговое окно не читается нигде).

Загрузка контрактов (instruments.csv + ряды закрытий формирования со стадии 1 `asset_extract.py`), укрупнённый класс (П-37 §4.1),
нормализация имени (приставки множителя, суффиксы-псевдонимы), статистики пары.
Только numpy и stdlib.
"""
import csv, glob, math, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import asset_class as AC                 # единая функция классификации (D-01): пред-листинг, приватные базы, класс other
from asset_class import BIG, OUT_OF_TEST, MULT_RE, norm_key    # noqa: E402,F401  (норма имени - одна, в asset_class)

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
DATA = os.path.join(ROOT, "data", "xdl9")
N_MIN = 14 * 1440                      # минут формирования
EXCH = ["binance", "bybit", "okx", "bingx", "aster", "kraken", "lighter", "bitget"]   # 8 минутных бирж (Hyperliquid 15 м - вне)
# приватные базы и решения по классу other (выводятся из instruments.csv ВСЕХ бирж; заполняет load_contracts)
PRIVATE_BASES = {}
RESOLUTION = {}


def safe_name(sym):
    return "".join(c if c.isalnum() or c in "-_.@" else "_" for c in sym)


def f(x, default=float("nan")):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def load_inst_all(data=DATA):
    """Строки instruments.csv ВСЕХ бирж каталога inst/ (в том числе Hyperliquid: из его пред-IPO берутся приватные базы)."""
    out = []
    for fn in sorted(glob.glob(os.path.join(data, "inst", "*.csv"))):
        with open(fn, encoding="utf-8", newline="") as fh:
            out.extend(csv.DictReader(fh))
    return out


class RowStore:
    """Строки закрытий по контрактам без склейки в один массив: np.memmap по файлам бирж (файловое отображение, память не копируется).
    Поддерживает C[i] (строка float32 [N_MIN]) и .shape; для скриптов, читающих по одной строке (статистики пар)."""

    def __init__(self, parts):
        self.parts = parts                      # [(начало, конец, memmap [n, N_MIN])]
        self.shape = (parts[-1][1], N_MIN)

    def __getitem__(self, i):
        for a, b, m in self.parts:
            if a <= i < b:
                return m[i - a]
        raise IndexError(i)


def load_contracts(data=DATA, lazy=False):
    """-> (list[dict], close float32 [N, N_MIN]); строка контракта = инструмент со свечами формирования.
    lazy=True: вместо массива - RowStore (только C[i]; для машин с малым запасом памяти)."""
    global PRIVATE_BASES, RESOLUTION
    PRIVATE_BASES = AC.derive_private_bases(load_inst_all(data))
    RESOLUTION = AC.load_resolution()
    rows = []
    C = None                              # единый массив: пик памяти = один массив, а не список частей + конкатенация
    n_all = 0
    for ex in EXCH:
        with open(os.path.join(data, "asset", ex + ".idx.csv"), encoding="utf-8", newline="") as fh:
            n_all += sum(1 for _ in csv.DictReader(fh))
    C = None if lazy else np.empty((n_all, N_MIN), dtype="<f4")
    parts = []
    at = 0
    for ex in EXCH:
        inst = {}
        with open(os.path.join(data, "inst", ex + ".csv"), encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                inst[safe_name(r["symbol"])] = r
        with open(os.path.join(data, "asset", ex + ".idx.csv"), encoding="utf-8", newline="") as fh:
            idx = list(csv.DictReader(fh))
        if lazy:
            raw = np.memmap(os.path.join(data, "asset", ex + ".f32"), dtype="<f4", mode="r").reshape(-1, N_MIN)
            parts.append((at, at + raw.shape[0], raw))
        else:
            raw = np.fromfile(os.path.join(data, "asset", ex + ".f32"), dtype="<f4").reshape(-1, N_MIN)
            C[at:at + raw.shape[0]] = raw
        assert raw.shape[0] == len(idx), (ex, raw.shape, len(idx))
        at += raw.shape[0]
        for r in idx:
            stem = r["file"][:-7]
            ins = inst.get(stem)
            if ins is None:
                raise SystemExit("нет в instruments: %s %s" % (ex, stem))
            d = dict(ins)
            d.update(file=r["file"], n_bars=int(r["n_bars"]), n_valid=int(r["n_valid"]), qv_rows=int(r["qv_rows"]),
                     turnover_qv=f(r["turnover_qv"]), turnover_cv=f(r["turnover_cv"]), turnover=f(r["turnover"]),
                     price_med=f(r["price_med"]), first_valid=int(r["first_valid"]), last_valid=int(r["last_valid"]),
                     row=int(r["row"]))
            rows.append(d)
        if not lazy:
            del raw
    for i, d in enumerate(rows):
        d["i"] = i
        d["zero_share"] = 1.0 - d["n_valid"] / N_MIN
    return rows, (RowStore(parts) if lazy else C)


def classify(d):
    """Единая классификация (asset_class.classify) с приватными базами и решениями по other, выведенными load_contracts."""
    return AC.classify(d, PRIVATE_BASES, RESOLUTION)


def big_class(d):
    return classify(d).big


# ---------------------------------------------------------------- статистики пары
def log_returns(c):
    """c: [T] float; -> r [T] (NaN, где нет обоих баров подряд)."""
    lc = np.log(c.astype(np.float64))
    r = np.full(lc.shape, np.nan)
    r[1:] = lc[1:] - lc[:-1]
    return r


def pair_stats(c1, c2, r1=None, r2=None):
    """Статистики пары по ОБЩИМ валидным минутам формирования (обе ноги v>0). Возвращает dict.
    corr   - Пирсон минутных лог-доходностей (минуты, где у обеих ног есть доходность);
    med_ratio - медиана c1/c2 по общим минутам; k - ближайшая целая степень 10; dev - |log10(med_ratio) - k|;
    S      - σ(lr)/σ(ln c1) по общим минутам, lr = ln c1 - ln c2;
    mean_lr - среднее lr; n_common - общих минут; n_ret - общих минут с доходностью."""
    both = np.isfinite(c1) & np.isfinite(c2)
    n = int(both.sum())
    out = dict(n_common=n, n_ret=0, corr=float("nan"), med_ratio=float("nan"), k=0, dev=float("nan"), S=float("nan"),
               mean_lr=float("nan"), sd_lr=float("nan"), sd_l1=float("nan"))
    if n < 2:
        return out
    a = c1[both].astype(np.float64)
    b = c2[both].astype(np.float64)
    ratio = a / b
    med = float(np.median(ratio))
    out["med_ratio"] = med
    lm = math.log10(med)
    k = int(round(lm))
    out["k"] = k
    out["dev"] = abs(lm - k)
    la, lb = np.log(a), np.log(b)
    lr = la - lb
    out["mean_lr"] = float(lr.mean())
    out["sd_lr"] = float(lr.std())
    out["sd_l1"] = float(la.std())
    out["S"] = float(lr.std() / la.std()) if la.std() > 0 else float("nan")
    if r1 is None:
        r1 = log_returns(c1)
    if r2 is None:
        r2 = log_returns(c2)
    m = np.isfinite(r1) & np.isfinite(r2)
    nr = int(m.sum())
    out["n_ret"] = nr
    if nr >= 3:
        x, y = r1[m], r2[m]
        if x.std() > 0 and y.std() > 0:
            out["corr"] = float(np.corrcoef(x, y)[0, 1])
    return out
