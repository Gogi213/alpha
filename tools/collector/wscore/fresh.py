"""Свежесть записей (TK-532, мера приёмки): сколько записей пришло с лагом rx − E больше LATE_MS и каков максимальный лаг — по видам, за 1 ч и 24 ч.
Делает: Fresh.add — на каждую записанную (после снятия дублей) запись; fragment — вклад шарда в health; merge — сложение вкладов шардов. Лаг считается по каждой записи, не выборкой.
Не знает биржу и соединения: запись — кортеж codec (вид — rec[0], время биржи E — rec[2]). Максимум хранится минутными корзинами: границы часа/суток — с точностью до минуты (в сторону большего окна).
"""
from collections import deque

LATE_MS = 2000  # запись «поздняя» при rx − E больше порога — назначение координатора (TK-532, п. 1)
GRACE_S = 5.0  # первые секунды после подписки не считаются: начальные снимки (tickers) несут устаревший ts, это не задержка доставки
BUCKET_MS = 60_000
HOUR_MS = 3600_000
DAY_MS = 24 * HOUR_MS
KINDS = {"b": "book", "t": "trade", "m": "mark", "r": "raw"}  # вид записи codec -> имя вида потока в health; raw — кадры данных, которые адаптер не разобрал (пишутся как пришли), лаг — по ts кадра


class Fresh:
    def __init__(self):
        self.late = {}  # вид -> ts_ms (приём) поздних записей за сутки
        self.cur = {}  # вид -> [минута, макс. лаг] незавершённой корзины
        self.buckets = {}  # вид -> завершённые корзины (минута, макс. лаг) за сутки

    def add(self, kind, rx_ms, lag_ms):
        if lag_ms > LATE_MS:
            self.late.setdefault(kind, deque()).append(rx_ms)
        minute = rx_ms // BUCKET_MS
        cur = self.cur.get(kind)
        if cur is None:
            self.cur[kind] = [minute, lag_ms]
        elif cur[0] == minute:
            if lag_ms > cur[1]:
                cur[1] = lag_ms
        else:
            self.buckets.setdefault(kind, deque()).append((cur[0], cur[1]))
            cur[0], cur[1] = minute, lag_ms

    def fragment(self, now_ms):
        """{имя вида: {late_1h, late_24h, max_1h_ms, max_24h_ms}} на момент now_ms; устаревшее за сутки выбрасывается."""
        hour, day = now_ms - HOUR_MS, now_ms - DAY_MS
        out = {}
        for kind in sorted(set(self.cur) | set(self.late)):
            late = self.late.get(kind, deque())
            while late and late[0] < day:
                late.popleft()
            bk = self.buckets.get(kind, deque())
            while bk and (bk[0][0] + 1) * BUCKET_MS <= day:
                bk.popleft()
            cur = self.cur.get(kind)
            allb = list(bk) + ([tuple(cur)] if cur else [])
            out[KINDS.get(kind, kind)] = {
                "late_1h": sum(t > hour for t in late), "late_24h": len(late),
                "max_1h_ms": max((v for m, v in allb if (m + 1) * BUCKET_MS > hour), default=None),
                "max_24h_ms": max((v for _, v in allb), default=None),
            }
        return out


def merge(frags):
    """Вклады шардов -> {имя вида: {...}}: числа поздних складываются, максимумы — максимум."""
    out = {}
    for fr in frags:
        for name, v in fr.items():
            o = out.setdefault(name, {"late_1h": 0, "late_24h": 0, "max_1h_ms": None, "max_24h_ms": None})
            o["late_1h"] += v["late_1h"]
            o["late_24h"] += v["late_24h"]
            for k in ("max_1h_ms", "max_24h_ms"):
                if v[k] is not None and (o[k] is None or v[k] > o[k]):
                    o[k] = v[k]
    return out
