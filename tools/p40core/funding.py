"""TK-522 (П-40 §16.7): фандинг в net_bps — начисления по меткам расчёта каждой ноги между входом и выходом.
Знак в P&L (+ получено, − уплачено): лонг платит ставку r, шорт получает: вклад ноги = −сторона · r · 1e4 bps номинала ноги (сторона: +1 лонг, −1 шорт).
Таблица ставок — CSV `exchange,symbol,ts_ms,rate` без шапки или с ней (ts_ms — метка расчёта; rate — доля за интервал). Источники:
  mark   — из рядов mark_price CHFT (бокс): метка = next_funding_time, ставка = последний funding_rate строго до метки (метка, за которой ряд не продолжается, не берётся);
  bybit  — публичный GET /v5/market/funding/history (fundingRateTimestamp, fundingRate), страницы назад по endTime;
  gate   — публичный GET /api/v4/futures/usdt/funding_rate (t с, r); метка Gate = расчёт + 1 с (как отдаёт API), не округляется.
  binance — публичный GET /fapi/v1/fundingRate (fundingTime мс = расчёт + 1 мс, fundingRate); okx — GET /api/v5/public/funding-rate-history (fundingTime, realizedRate; история ~3 мес.).
  universe <pairs.csv> <с> <по> <биржи через запятую> [потоки] — таблица по всем ногам плана в CSV на stdout, сбои ног — в stderr строкой `FAIL биржа символ причина` (код 1, если были).
Запуск: funding.py fetch bybit|gate <символ> <с ГГГГ-ММ-ДД> <по ГГГГ-ММ-ДД> >> funding.csv   (по утверждённым символам вселенной, 17.09–22.10)
        funding.py mark <биржа> <символ> <каталог с <биржа>_futures/> <с ГГГГ-ММ-ДД> <по ГГГГ-ММ-ДД> >> funding.csv   (CHFT: <каталог>/<биржа>_futures/<сутки>/<ЧЧ>/<символ>_mark_price.parquet; часы без файла пропускаются — дыру покажет покрытие assemble)."""
import calendar, csv, json, sys, time, urllib.request
import numpy as np

BYBIT = "https://api.bybit.com/v5/market/funding/history"
BINANCE = "https://fapi.binance.com/fapi/v1/fundingRate"
OKX = "https://www.okx.com/api/v5/public/funding-rate-history"
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


def fetch_binance(sym, s_ms, e_ms):
    out, start = {}, s_ms
    while start <= e_ms:
        d = _get(f"{BINANCE}?symbol={sym}&startTime={start}&endTime={e_ms}&limit=1000")
        if isinstance(d, dict):
            raise RuntimeError(f"binance {sym}: {d.get('msg')}")
        for x in d:
            out[int(x["fundingTime"])] = float(x["fundingRate"])
        if len(d) < 1000:
            break
        start = max(int(x["fundingTime"]) for x in d) + 1
    return sorted(out.items())


def fetch_okx(inst, s_ms, e_ms):
    """страницы назад по after (записи строго раньше метки); realizedRate — фактически начисленная ставка"""
    out, after = {}, e_ms + 1
    while True:
        d = _get(f"{OKX}?instId={inst}&after={after}&limit=100")
        if d.get("code") != "0":
            raise RuntimeError(f"okx {inst}: {d.get('msg')}")
        lst = d["data"]
        for x in lst:
            if int(x["fundingTime"]) >= s_ms:
                out[int(x["fundingTime"])] = float(x["realizedRate"] or x["fundingRate"])
        if len(lst) < 100 or min(int(x["fundingTime"]) for x in lst) < s_ms:
            break
        after = min(int(x["fundingTime"]) for x in lst)
        time.sleep(0.25)
    return sorted(out.items())


FETCH = {"bybit": fetch_bybit, "gate": fetch_gate, "binance": fetch_binance, "okx": fetch_okx}


def universe(pairs_csv, s_ms, e_ms, exs, workers):
    """ноги плана (биржа, символ) по exA/symA и exB/symB -> {(биржа, символ): строки}; сбой ноги не обрывает остальные"""
    import concurrent.futures as cf
    legs = sorted({(r["ex" + k], r["sym" + k]) for r in csv.DictReader(open(pairs_csv)) for k in "AB"})
    legs = [l for l in legs if l[0] in exs]

    def one(leg):
        try:
            return leg, FETCH[leg[0]](leg[1], s_ms, e_ms), ""
        except Exception as ex:
            return leg, [], f"{type(ex).__name__}: {ex}"
    bad, w = 0, csv.writer(sys.stdout)
    with cf.ThreadPoolExecutor(workers) as pool:
        for (ex, sym), rows, err in pool.map(one, legs):
            if err or not rows:
                bad += 1
                print(f"FAIL {ex} {sym} {err or 'пусто'}", file=sys.stderr)
            for t, r in rows:
                w.writerow([ex, sym, t, r])
    print(f"legs={len(legs)} fail={bad}", file=sys.stderr)
    return bad


def mark_rows(ex, sym, base, d0, d1):
    import pyarrow.parquet as pq
    ts, rs, ns = [], [], []
    for day in range(d0, d1 + 1):
        dd = time.strftime("%Y-%m-%d", time.gmtime(day * 86400))
        for h in range(24):
            try:
                m = pq.read_table(f"{base}/{ex}_futures/{dd}/{h:02d}/{sym}_mark_price.parquet", columns=["event_time", "funding_rate", "next_funding_time"])
            except FileNotFoundError:
                continue
            ts.append(m["event_time"].to_numpy()); rs.append(m["funding_rate"].cast("float64").to_numpy(zero_copy_only=False)); ns.append(m["next_funding_time"].cast("float64").to_numpy(zero_copy_only=False))
    if not ts:
        return []
    T, r = from_mark(np.concatenate(ts), np.concatenate(rs), np.concatenate(ns))
    return [(int(t), float(x)) for t, x in zip(T, r)]


def main():
    a = sys.argv[1:]
    if a and a[0] == "mark" and len(a) == 6:
        d0, d1 = (calendar.timegm(time.strptime(x, "%Y-%m-%d")) // 86400 for x in a[4:6])
        w = csv.writer(sys.stdout)
        for t, r in mark_rows(a[1], a[2], a[3], d0, d1):
            w.writerow([a[1], a[2], t, r])
        return
    if a and a[0] == "universe" and len(a) in (5, 6):
        s, e = (calendar.timegm(time.strptime(x, "%Y-%m-%d")) * 1000 for x in a[2:4])
        sys.exit(1 if universe(a[1], s, e + 86_399_999, set(a[4].split(",")), int(a[5]) if len(a) == 6 else 4) else 0)
    if len(a) != 5 or a[0] != "fetch" or a[1] not in FETCH:
        sys.exit(__doc__)
    s, e = (calendar.timegm(time.strptime(x, "%Y-%m-%d")) * 1000 for x in a[3:5])
    rows = FETCH[a[1]](a[2], s, e + 86_399_999)
    w = csv.writer(sys.stdout)
    for t, r in rows:
        w.writerow([a[1], a[2], t, r])


if __name__ == "__main__":
    main()
