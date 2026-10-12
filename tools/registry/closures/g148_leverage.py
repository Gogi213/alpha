"""Г-148: плечо ×2–5, стресс. Описание на закрытиях B1 (М5, В-227 пакет F; «описание без Холма», в m не входит). Плечо L = размер позиции
× L при том же депозите: $ каждой сделки × L (издержки пропорциональны размеру — линейно, как в psim). Стресс по закрытиям: максимальная
просадка кумулятивной суммы (в $ и долях депозита DEPOSIT) и худшие сутки UTC. Ограничение честно: внутрисделочный ход против позиции
(маржин-колл) на закрытиях не виден — это Rust-механика; здесь только просадка по закрытым сделкам."""
NAME = "g148"
KIND, SOURCES = "closes", ()
PARAMS = {"L": (1, 2, 3, 4, 5)}
DEPOSIT = 2500.0   # депозит гейта М5 (portfolio-sim --deposit-usd 2500)
DAY_MS = 86_400_000


def run(data, ctx, L):
    return [(ms, u * L) for ms, u in sorted(data)]


def stats(fills):
    cum = peak = dd = 0.0
    days = {}
    for ms, u in sorted(fills):
        cum += u; peak = max(peak, cum); dd = min(dd, cum - peak)
        days[ms // DAY_MS] = days.get(ms // DAY_MS, 0.0) + u
    return {"max_dd_usd": round(dd, 4), "max_dd_frac": round(dd / DEPOSIT, 4), "worst_day_usd": round(min(days.values(), default=0.0), 4)}
