import numpy as np
import pandas as pd
import tk461_d2b_cols as C


class A:  # минимальная заглушка баров
    def __init__(self, h, l):
        self.h = pd.Series(h)
        self.l = pd.Series(l)

    def __len__(self):
        return len(self.h)


def test_cols():
    tf = 1
    tm = np.arange(0, 10 * 60000, 60000)
    o = np.full(10, 100.0)
    a = A([101] * 10, [99] * 10)
    ev = pd.DataFrame([
        dict(ev="A5", dir="H", side=-1, tt0=0, ext=101.0, L=100.5, j=1),   # шорт, стоп 101 -> 100 bps
        dict(ev="A5", dir="L", side=1, tt0=0, ext=99.0, L=99.5, j=1),      # лонг, стоп 99 -> 100 bps
        dict(ev="A1", dir="H", side=1, tt0=0, ext=0.0, L=99.5, j=1),       # лонг, уровень 99.5 -> 50 bps
        dict(ev="A6", dir="H", side=-1, tt0=0, ext=0.0, L=0.0, j=1),       # зона l[j]=99 ниже входа шорта -> NaN
        dict(ev="A6", dir="L", side=1, tt0=0, ext=0.0, L=0.0, j=1),        # h[j]=101 выше входа лонга -> NaN
    ])
    r = C.add_cols(ev, tm, o, tf, a)
    assert np.allclose(r.ss_bps.values[:3], [100, 100, 50]) and r.ss_bps.isna().values[3:].all()
    assert (r.ot1_bps.fillna(-1) == r.ss_bps.fillna(-1)).all()
