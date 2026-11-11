"""Формат записи на диске (В-133, выбран замером — docs/findings/tk527-binance-ws-2026-11-11.md): Encoder, decode, ключ дедупа.
Делает: запись -> строка (и обратно без потерь). Не знает биржу, файлы, соединения; состояние (предыдущие u/a/l, приём) — на файл: новый файл — новый Encoder.
Текстовые строки с разделителем \t, числа биржи — строками как пришли (В-172), метка приёма на каждой записи (В-137), время и счётчики — разностями (точно, без потерь).
Записи (первый столбец — тип; `dns` — приём в нс минус приём предыдущей записи файла, первая запись — абсолютное значение):
  b dns lag ET sym du b B a A         L1: lag = приём_мс − E, ET = E − T, du = u − u предыдущей записи символа (первая в файле — абсолютное)
  t dns lag ET sym da p q lf f m nq   сделка: da = a − (a предыдущей+1), lf = l − f, f = f − (l предыдущей+1), m = 0/1, nq — только если ≠ q (иначе пусто)
  m dns lag sym p ap P i r T          mark/фандинг: p mark, i индекс, r ставка, T следующее начисление; ap, P — что есть у биржи, иначе пусто
  g dns rtt_us                        пинг WS: время до pong
  c dns what detail                   служебное: connected / subscribed / closed (detail — сессия и адрес)
  r dns <кадр>                        всё, что адаптер не разобрал точно (ответ на SUBSCRIBE, новые поля биржи): кадр как пришёл
Ключ дедупа (`dedup_key`): b — u, t — a, m — E; у каждой биржи по символу растёт. Запись символа в файле поэтому строго упорядочена (писатель снимает повторы).
"""

class Encoder:
    """Запись -> строка формата. Состояние (предыдущие u/a/l, приём) — на файл; новый файл — новый Encoder."""

    def __init__(self):
        self.prev_ns = 0
        self.last_u = {}
        self.last_a = {}
        self.last_l = {}

    def _dns(self, rx_ns):
        d = rx_ns - self.prev_ns
        self.prev_ns = rx_ns
        return d

    def raw(self, rx_ns, frame):
        f = frame if isinstance(frame, str) else frame.decode()
        return f"r\t{self._dns(rx_ns)}\t{f}\n"

    def record(self, rx_ns, rec):
        rms = rx_ns // 1_000_000
        k = rec[0]
        if k == "b":
            _, s, E, T, u, bid, bsz, ask, asz = rec
            du = u - self.last_u.get(s, 0)
            self.last_u[s] = u
            return f"b\t{self._dns(rx_ns)}\t{rms - E}\t{E - T}\t{s}\t{du}\t{bid}\t{bsz}\t{ask}\t{asz}\n"
        if k == "t":
            _, s, E, T, a, p, q, f, l, m, nq = rec
            da = a - self.last_a.get(s, 0)
            df = f - self.last_l.get(s, 0)
            self.last_a[s] = a + 1
            self.last_l[s] = l + 1
            return f"t\t{self._dns(rx_ns)}\t{rms - E}\t{E - T}\t{s}\t{da}\t{p}\t{q}\t{l - f}\t{df}\t{int(m)}\t{'' if nq == q else nq}\n"
        if k == "m":
            _, s, E, p, ap, P, i, r, T = rec
            return f"m\t{self._dns(rx_ns)}\t{rms - E}\t{s}\t{p}\t{ap}\t{P}\t{i}\t{r}\t{T}\n"
        raise ValueError(k)

    def ping(self, rx_ns, rtt_us):
        return f"g\t{self._dns(rx_ns)}\t{rtt_us}\n"

    def control(self, rx_ns, what, detail=""):
        return f"c\t{self._dns(rx_ns)}\t{what}\t{detail}\n"


def decode(text):
    """Строки формата -> записи (dict); обратное к Encoder (проверка без потерь, читатель П-40 делает то же)."""
    prev_ns, last_u, last_a, last_l = 0, {}, {}, {}
    for ln in text.split("\n"):
        if not ln:
            continue
        f = ln.split("\t", 2) if ln[0] == "r" else ln.split("\t")
        prev_ns += int(f[1])
        rx = prev_ns
        rms = rx // 1_000_000
        t = f[0]
        if t == "b":
            E = rms - int(f[2])
            u = last_u.get(f[4], 0) + int(f[5])
            last_u[f[4]] = u
            yield {"t": "b", "rx_ns": rx, "E": E, "T": E - int(f[3]), "s": f[4], "u": u, "b": f[6], "B": f[7], "a": f[8], "A": f[9]}
        elif t == "t":
            E = rms - int(f[2])
            a = last_a.get(f[4], 0) + int(f[5])
            first = last_l.get(f[4], 0) + int(f[9])
            last_a[f[4]] = a + 1
            last_l[f[4]] = first + int(f[8]) + 1
            yield {"t": "t", "rx_ns": rx, "E": E, "T": E - int(f[3]), "s": f[4], "a": a, "p": f[6], "q": f[7], "f": first, "l": first + int(f[8]), "m": f[10] == "1", "nq": f[11] or f[7]}
        elif t == "m":
            yield {"t": "m", "rx_ns": rx, "E": rms - int(f[2]), "s": f[3], "p": f[4], "ap": f[5], "P": f[6], "i": f[7], "r": f[8], "T": int(f[9])}
        elif t == "g":
            yield {"t": "g", "rx_ns": rx, "rtt_us": int(f[2])}
        elif t == "c":
            yield {"t": "c", "rx_ns": rx, "what": f[2], "detail": f[3] if len(f) > 3 else ""}
        else:
            yield {"t": "r", "rx_ns": rx, "frame": f[2]}


def dedup_key(rec):
    """Ключ порядка записи символа: b — u, t — a, m — E (у каждой биржи по символу растёт)."""
    k = rec[0]
    return rec[4] if k in ("b", "t") else rec[2]
