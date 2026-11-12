"""Входы линии (G-022, TK-532): список входов на линию в конфиге адаптера, текущий вход, переход на запасной.
Делает: Entrances — входы одной линии по порядку предпочтения; current() — вход, через который идёт линия (None — вход адаптера); switch(seen) — перейти к следующему по кругу; parse_line — «а,б» -> входы линии.
Не знает соединений: когда переходить — решает shard (повторные неудачи подключения FAIL_LIMIT или задержка доставки — испорченная сессия). Объект один на линию шарда: у сессий линии (живой и замены) общий текущий вход.
Вход — (хост, адрес или None), см. dial.parse_entrance: имя хоста (SNI/Host) и адрес/семейство (@ip, @v4, @v6).
"""
from wscore.dial import parse_entrance

FAIL_LIMIT = 3  # подряд коротких сессий (неудачных подключений) на входе, после которых линия переходит на запасной вход (назначаемое: 3 попытки с откатом 1+2+4 с)
SHORT_SESSION_S = 60.0  # сессия короче — неудача подключения (тот же порог, что сброс отката переподключения)


class Entrances:
    def __init__(self, options, line=0):
        self.options = list(options) or [None]  # вход линии по порядку предпочтения; None — вход адаптера
        self.i = 0
        self.line = line

    def current(self):
        return self.options[self.i]

    def multi(self):
        return len(self.options) > 1

    def switch(self, seen):
        """Следующий вход по кругу, если линия ещё на `seen` (другая сессия линии могла уже перейти); (прежний, новый) или None. Возврата на основной нет: линия идёт по новому входу, пока и он не откажет."""
        if not self.multi() or self.current() != seen:
            return None
        self.i = (self.i + 1) % len(self.options)
        return seen, self.current()


def label(entrance):
    host, addr = entrance if entrance is not None else ("адаптер", None)
    return host + (f"@{addr}" if addr else "")


def parse_line(spec):
    """«вход[,запасной…]» или список входов -> [вход]: «-» — вход адаптера (None)."""
    parts = spec.split(",") if isinstance(spec, str) else spec
    return [None if x == "-" else parse_entrance(x) for x in parts]


def build_lines(cli, cfg, lanes):
    """Входы по линиям: аргумент `--entrance` линии k, иначе конфиг адаптера (`Adapter.entrances[k]`), иначе вход адаптера."""
    return [parse_line(cli[k] if k < len(cli) else cfg[k] if k < len(cfg) else "-") for k in range(lanes)]
