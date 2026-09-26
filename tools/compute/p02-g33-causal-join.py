#!/usr/bin/env python3
"""П-02, Г-33 (H2, декоративная цепочка) — причинная версия по поправке §12 (`dadd428`, утверждена 26.09,
`docs/research/reviews/P-02-2026-09-26-owner-delegated-decisions.md`). Один символ-сутки: сырой
`levels-<SYM>.csv` (`lob levels`) + касания кэша D20 → одна строка на касание.

Цепочка касаемого уровня L — звенья-предшественники: уровень P той же стороны, умерший в кадре рождения
L (`P.death_ms = L.birth_ms`), на соседней цене ровно `|P.tick − L.tick| = 1` (как в движке,
`src/lob/levels.rs:26-29`), с `P.repriced` ∧ `P.traded_lots ≤ 0`; дальше тем же правилом от P назад.
- Кандидат — любой уровень той же стороны с `repriced = true`, умерший в кадре рождения L на `L.tick ± 1`.
- **Неоднозначное звено = разрыв:** у L два кандидата (на `−1` и на `+1`), или в кадре смерти P на той же
  стороне родились уровни и на `P.tick + 1`, и на `P.tick − 1` (P мог уйти к любому).
- Единственный кандидат с `traded_lots > 0` — не звено (цепочка кончается, не неоднозначность).
- Длина — число предшественников без самого L. Корзины (в анализе): ≥ 2 против 0 (основной), ≥ 3 против 0
  (сосед); длины 1 (1–2) — вне корзин.
- Все звенья умерли до рождения L — будущего нет. Край суток: предшественники до начала суток не видны.

Выход (дописывает в `--out`): symbol,day_utc,side,price_tick,touch_index,ended_by_death,chain_len,chain_stop
где chain_stop ∈ {none, nonlink, ambiguous}: чем кончилась цепочка (none — у последнего звена нет
кандидата вовсе).

CLI совместим с обёрткой `p02-wave3-wall-recompute.sh` (лишние `--g33-max-traded`/`--g36-ratio`
принимаются; `--g36-ratio` не используется).
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from typing import Dict, List, Tuple

MAX_CHAIN = 1000  # предохранитель; времена звеньев строго убывают, цикла быть не может


def open_csv(path: str):
    f = open(path, encoding="utf-8", newline="")
    first = f.readline()
    if not first.startswith("#"):
        f.seek(0)
    return f, csv.DictReader(f)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", required=True)
    p.add_argument("--day-utc", required=True)
    p.add_argument("--levels", required=True)
    p.add_argument("--touches", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--g33-max-traded", type=float, default=0.0)
    p.add_argument("--g36-ratio", type=float, default=None)
    args = p.parse_args(argv)

    # (side, death_ms) -> [(tick, repriced, traded, birth_ms)]; (side, birth_ms) -> {tick}
    deaths: Dict[Tuple[str, int], List[tuple]] = defaultdict(list)
    births: Dict[Tuple[str, int], set] = defaultdict(set)
    f, r = open_csv(args.levels)
    with f:
        for row in r:
            side = row["side"]
            tick = int(row["price_tick"])
            b = int(row["birth_ms"])
            d = int(row["death_ms"])
            rep = row["repriced"].strip().lower() == "true"
            traded = float(row["traded_lots"])
            deaths[(side, d)].append((tick, rep, traded, b))
            births[(side, b)].add(tick)

    def predecessor(side: str, tick: int, birth_ms: int):
        """-> ('link', (tick, birth_ms)) | ('nonlink', None) | ('ambiguous', None) | ('none', None)."""
        cands = [c for c in deaths.get((side, birth_ms), ())
                 if c[1] and abs(c[0] - tick) == 1]
        if not cands:
            return "none", None
        if len({c[0] for c in cands}) > 1:
            return "ambiguous", None
        if len(cands) > 1:  # два уровня на одной цене в одном кадре — не различить
            return "ambiguous", None
        ptick, _rep, ptraded, pbirth = cands[0]
        newborn = births.get((side, birth_ms), set())
        if (ptick + 1) in newborn and (ptick - 1) in newborn:
            return "ambiguous", None
        if ptraded > args.g33_max_traded:
            return "nonlink", None
        return "link", (ptick, pbirth)

    fo = open(args.out, "a", encoding="utf-8", newline="")
    f, r = open_csv(args.touches)
    with f, fo:
        for row in r:
            side = row["side"]
            tick = int(row["price_tick"])
            birth = int(row["birth_ms"])
            n = 0
            stop = "none"
            cur = (tick, birth)
            while n < MAX_CHAIN:
                kind, nxt = predecessor(side, cur[0], cur[1])
                if kind != "link":
                    stop = kind
                    break
                n += 1
                cur = nxt
            fo.write(",".join([args.symbol, args.day_utc, side, row["price_tick"], row["touch_index"],
                               row["ended_by_death"], str(n), stop]) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
