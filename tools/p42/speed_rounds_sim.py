#!/usr/bin/env python3
"""TK-607: сколько раундов раздачи у процедуры §13.24 и в скольких из них появляется НОВАЯ сигнальная конфигурация движка (SigCfg → фаза 1).
Настоящие сетки (`tk599-grids.json`) и настоящий автомат (`retitr_full.procedure_gens`), серии — синтетика (как в тестах): число раундов зависит от
выборов процедуры, поэтому несколько случайных миров. Без движка. Запуск: PYTHONUTF8=1 python3 tools/p42/speed_rounds_sim.py [seed ...]"""
import hashlib
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import retitr_cells as rc  # noqa: E402
import retitr_full as rf  # noqa: E402

GRIDS = os.path.join(HERE, "..", "..", "docs", "research", "freeze", "tk599-grids.json")
# ключи, входящие в `SigCfg` движка (src/breakout/params.rs, разбор ключей) — только они меняют фазу 1
SIG_KEYS = {"natr_p", "natr_min", "t1h", "t24", "tf", "k", "d", "n_bounce", "tau", "l_days", "pk", "track", "e", "color", "f", "r", "ban", "ap", "pb", "fh", "fk", "fm", "fb"}
NB = 200


def sigcfg(req):
    plain, _ = rc.parse_keys(req.engine_keys)
    return rc.canon(" ".join("%s=%s" % kv for kv in plain if kv[0] in SIG_KEYS))[0]


class World:
    def __init__(self, seed, ncoins=40, per_day=6, share=0.3):
        r = np.random.default_rng(seed)
        self.day = np.repeat(np.arange(rf.NDAYS), per_day)
        self.sym = r.integers(0, ncoins, size=len(self.day))
        self.base = r.normal(0.2, 3.0, len(self.day))
        self.seed, self.share, self.r = seed, share, r
        self.shift = {}

    def eff(self, k, v):
        key = (k, v)
        if key not in self.shift:
            h = int(hashlib.md5(repr((self.seed, key)).encode()).hexdigest()[:8], 16)
            g = np.random.default_rng(h)
            self.shift[key] = g.normal(0.6, 0.5) if g.random() < self.share else 0.0
        return self.shift[key]

    def __call__(self, req):
        plain, feats = rc.parse_keys(req.keys)
        sh = sum(self.eff(k, v) for k, v in plain) + sum(self.eff(k, v) for f in feats for k, v in f.items())
        h = int(hashlib.md5(repr((self.seed, round(sh, 9))).encode()).hexdigest()[:8], 16)
        noise = np.random.default_rng(h).normal(0, 0.3, len(self.day)) if sh != 0 else 0.0
        return rf.make_series(self.day, self.sym, self.base + sh + noise)


class LogEv(rf.Evaluator):
    def __init__(self, fn):
        super().__init__(fn)
        self.rounds = []
        self.seen_sig = set()

    def fulfil(self, reqs):
        miss = self.missing(reqs)
        if miss:
            sigs = {sigcfg(r) for r in miss if isinstance(r, rf.Req)}
            self.rounds.append({"n": len(miss), "sig": len(sigs), "new_sig": len(sigs - self.seen_sig), "ftab": sum(isinstance(r, rf.FtabNeed) for r in miss)})
            self.seen_sig |= sigs
        super().fulfil(reqs)


def provider_for(ev, spec):
    def provider(side, keys, om):
        yield from rf._need(ev, [rf.FtabNeed(side, keys)])
        return {(fa, fo): {10: 1.0, 20: 2.0, 25: 3.0} for fa, fo in spec.features(side)}
    return provider


def main(seeds):
    spec = rf.Spec.load(GRIDS)
    for seed in seeds:
        w = World(seed)
        ev = LogEv(w)
        ev.fn = lambda req, w=w: True if isinstance(req, rf.FtabNeed) else w(req)
        res = rf.drive(rf.procedure_gens(ev, spec, 40, provider=provider_for(ev, spec), nboot=NB), ev)
        rs = ev.rounds
        n_new = sum(1 for r in rs if r["new_sig"])
        print("seed %d: раундов %d, из них с новой сигнальной конфигурацией %d, с таблицей признаков %d; всего прогонов %d, различных сигнальных конфигураций %d; "
              "прогонов за раунд: медиана %d, макс %d" % (seed, len(rs), n_new, sum(1 for r in rs if r["ftab"]), sum(r["n"] for r in rs), len(ev.seen_sig),
                                                            int(np.median([r["n"] for r in rs])), max(r["n"] for r in rs)))
        print("   раунды с новой сигнальной конфигурацией (номер:новых): " + " ".join("%d:%d" % (i + 1, r["new_sig"]) for i, r in enumerate(rs) if r["new_sig"]))
        print("   раунды с FtabNeed (номер): " + " ".join(str(i + 1) for i, r in enumerate(rs) if r["ftab"]))


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]] or [1, 2, 3])
