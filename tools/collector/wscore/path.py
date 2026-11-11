"""Качество пути до биржи: окно проверки нового пути и сторож лага живого соединения.
Делает: PathProbe — вердикт «чистый путь» по лагам первых PROBE_S с; PathWatch — сессия с p50 лага выше LAG_LIMIT_MS подряд окна становится degraded.
Не знает соединений: решение «открыть замену» принимает шард по `degraded`. Все пороги — назначаемые, из замеров 11.11 (В-140: потери пути NL→Токио).
"""
from wscore.stats import percentile

PROBE_S = 15  # выбор пути: после подписки окно проверки лага; плохой путь (потери) отбрасывается до записи в «живые»
PROBE_SKIP_S = 4  # снапшот подписки несёт старый E
PROBE_P50_MS = 350  # замер 11.11 (73 принятых пути): 71 с p50 166–348 мс, два граничных 467–468 (один испортился через 17 с); плохой p50 2,6–20 с
PROBE_P99_MS = 1500
PROBE_MIN_SAMPLES = 5
PROBE_MAX_REJECTS = 12  # подряд отбракованных путей (RTT рукопожатия — dial, затем лаг); дальше соединение принимается (сторож лага остаётся). Доля плохих ≈ 40 %: 0,4^12 ≈ 2·10^-5
LAG_LIMIT_MS = 2000  # подтвердить: p50 отставания выше — путь испортился (замер: норма ≈ RTT/2 ≈ 230 мс)
LAG_BAD_WINDOWS = 2  # подряд окон stats.STATS_PERIOD_S выше LAG_LIMIT_MS
LAG_SKIP_WINDOWS = 2  # окна после подключения не считаются: снапшот подписки несёт старый E


def path_verdict(lags):
    """Чистый ли путь по лагам прихода окна проверки: мало выборок — не отбраковываем."""
    if len(lags) < PROBE_MIN_SAMPLES:
        return True
    return percentile(lags, 0.5) <= PROBE_P50_MS and percentile(lags, 0.99) <= PROBE_P99_MS


class PathProbe:
    """Окно проверки нового пути: лаги от PROBE_SKIP_S до PROBE_S секунд после подписки."""

    def __init__(self, t_sub):
        self.t0 = t_sub
        self.lags = []

    def collecting(self, mono):
        return mono - self.t0 >= PROBE_SKIP_S

    def add(self, lag):
        if lag is not None:
            self.lags.append(lag)

    def finished(self, mono):
        return mono - self.t0 >= PROBE_S

    def clean(self):
        return path_verdict(self.lags)

    def summary(self):
        if not self.lags:
            return {"n": 0}
        return {"n": len(self.lags), "p50_ms": round(percentile(self.lags, 0.5)), "p99_ms": round(percentile(self.lags, 0.99))}


class PathWatch:
    """Сторож лага по сессиям шарда: выборки окна, возраст в окнах, подряд плохих окон; degraded — сессии с испорченным путём (и без pong, см. session)."""

    def __init__(self):
        self.lags = {}  # сессия -> лаги окна stats.STATS_PERIOD_S
        self.age = {}  # сессия -> окон с подключения
        self.bad = {}  # сессия -> подряд окон с лагом выше LAG_LIMIT_MS
        self.degraded = set()

    def open(self, seq):
        self.lags[seq], self.age[seq], self.bad[seq] = [], 0, 0

    def close(self, seq):
        for d in (self.lags, self.age, self.bad):
            d.pop(seq, None)
        self.degraded.discard(seq)

    def sample(self, seq, lag):
        self.lags.setdefault(seq, []).append(lag)

    def window_lags(self):
        return [v for xs in self.lags.values() for v in xs]

    def judge(self):
        """Конец окна: сессия с p50 лага выше LAG_LIMIT_MS LAG_BAD_WINDOWS окон подряд — в degraded, выборки окна сбрасываются."""
        for seq, xs in list(self.lags.items()):
            p50 = percentile(xs, 0.5)
            self.age[seq] = self.age.get(seq, 0) + 1
            if self.age[seq] > LAG_SKIP_WINDOWS and p50 is not None and p50 > LAG_LIMIT_MS:
                self.bad[seq] = self.bad.get(seq, 0) + 1
            else:
                self.bad[seq] = 0
            if self.bad[seq] >= LAG_BAD_WINDOWS:
                self.degraded.add(seq)
            self.lags[seq] = []
