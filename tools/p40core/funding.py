"""TK-522 (П-40 §16.7): фандинг в net_bps — начисления по меткам расчёта каждой ноги между входом и выходом.
Знак в P&L (+ получено, − уплачено): лонг платит ставку r, шорт получает: вклад ноги = −сторона · r · 1e4 bps номинала ноги (сторона: +1 лонг, −1 шорт).
Таблица ставок — CSV `exchange,symbol,ts_ms,rate` без шапки или с ней (ts_ms — метка расчёта; rate — доля за интервал). Источники:
  mark   — из рядов mark_price CHFT (бокс): метка = next_funding_time, ставка = последний funding_rate строго до метки (метка, за которой ряд не продолжается, не берётся);
  bybit  — публичный GET /v5/market/funding/history (fundingRateTimestamp, fundingRate), страницы назад по endTime;
  gate   — публичный GET /api/v4/futures/usdt/funding_rate (t с, r); метка Gate = расчёт + 1 с (как отдаёт API), не округляется.
Запуск: funding.py fetch bybit|gate <символ> <с ГГГГ-ММ-ДД> <по ГГГГ-ММ-ДД> >> funding.csv   (по утверждённым символам вселенной, 17.09–22.10)."""
import calendar, csv, json, sys, time, urllib.request
import numpy as np

BYBIT = "https://api.bybit.com/v5/market/funding/history"
GATE = "https://api.gateio.ws/api/v4/futures/usdt/funding_rate"


def from_mark(t_ms, rate, next_ms):
    """ряд mark_price (t, funding_rate, next_funding_time; мс) -> (метки T мс, ставки) по отсортированным T"""
    t, r, n = (np.asarray(x, dtype="float64") for x in (t_ms, rate, next_ms))
    k = np.isfinite(r) & np.isfinite(n) & np.isfinite(t)
    t, r, n = t[k], r[k], n[k]
    if not len(t):
        return np.empty(0), np.empty(0)
    o = np.argsort(t, kind="stable")
    t, r, n = t[o], r[o], n[o]
    T = np.unique(n[n > 0])
    T = T[T <= t[-1]]
    i = np.searchsorted(t, T, side="left") - 1
    ok = i >= 0
    return T[ok], r[i[ok]]


def load_csv(path):
    """-> {(биржа, символ): (T, r)}, отсортировано по T, дубликаты меток — последняя строка"""
    acc = {}
    for x in csv.reader(open(path)):
        if x and x[0] != "exchange":        # строка-шапка допустима (и при склейке выходов fetch)
            acc.setdefault((x[0], x[1]), {})[int(x[2])] = float(x[3])
    return {k: (np.array(sorted(v), dtype="float64"), np.array([v[t] for t in sorted(v)])) for k, v in acc.items()}


def accrue_bps(T, r, t_in, t_out, side):
    """вклад ноги в P&L, bps: −side·Σ r·1e4 по меткам T в (t_in, t_out]; side = +1 лонг, −1 шорт. Позиции нет (t_in/t_out NaN или t_out <= t_in) -> 0."""
    if not (t_in == t_in and t_out == t_out and t_out > t_in) or not len(T):
        return 0.0
    m = (T > t_in) & (T <= t_out)
    return float(-side * r[m].sum() * 1e4)


def episode_bps(legs, sell_leg, t_in, t_out):
    """легs = (таблица A, таблица B), каждая (T, r); sell_leg 0|1 — какая нога шорт; t_in = (вход A, вход B) мс (NaN — нога не исполнена); t_out — выход эпизода мс.
    -> (фандинг суммой, bps; по ногам [A, B])"""
    per = [accrue_bps(T, r, t_in[i], t_out, -1 if i == sell_leg else 1) for i, (T, r) in enumerate(legs)]
    return sum(per), per


def gaps(T, t_lo, t_hi, max_step_ms):
    """покрытие: список интервалов (T_i, T_{i+1}) в [t_lo, t_hi] с шагом больше max_step_ms (включая края); пусто = покрыто"""
    pts = [t_lo] + [float(x) for x in T if t_lo < x < t_hi] + [t_hi]
    return [(a, b) for a, b in zip(pts, pts[1:]) if b - a > max_step_ms]


def _get(url):
    for k in range(4):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "p40"}), timeout=20))
        except Exception:
            if k == 3:
                raise
            time.sleep(1 + k)


def fetch_bybit(sym, s_ms, e_ms):
    out, end = {}, e_ms
    while end >= s_ms:
        d = _get(f"{BYBIT}?category=linear&symbol={sym}&startTime={s_ms}&endTime={end}&limit=200")
        if d.get("retCode") != 0:
            raise RuntimeError(f"bybit {sym}: {d.get('retMsg')}")
        lst = d["result"]["list"]
        for x in lst:
            out[int(x["fundingRateTimestamp"])] = float(x["fundingRate"])
        if len(lst) < 200:
            break
        end = min(int(x["fundingRateTimestamp"]) for x in lst) - 1
    return sorted(out.items())


def fetch_gate(contract, s_ms, e_ms):
    d = _get(f"{GATE}?contract={contract}&limit=1000&from={s_ms // 1000}&to={e_ms // 1000}")
    if isinstance(d, dict):
        raise RuntimeError(f"gate {contract}: {d.get('message')}")
    if len(d) >= 1000:
        raise RuntimeError(f"gate {contract}: 1000 строк — окно режется, сузить")
    return sorted((int(x["t"]) * 1000, float(x["r"])) for x in d)


def main():
    a = sys.argv[1:]
    if len(a) != 5 or a[0] != "fetch" or a[1] not in ("bybit", "gate"):
        sys.exit(__doc__)
    s, e = (calendar.timegm(time.strptime(x, "%Y-%m-%d")) * 1000 for x in a[3:5])
    rows = (fetch_bybit if a[1] == "bybit" else fetch_gate)(a[2], s, e + 86_399_999)
    w = csv.writer(sys.stdout)
    for t, r in rows:
        w.writerow([a[1], a[2], t, r])


if __name__ == "__main__":
    main()
