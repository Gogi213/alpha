#!/usr/bin/env python3
"""Дашборд v17, справочно (НЕ вердикт, не новое испытание): варианты-фильтры гипотез П-02
(Г-28/Г-08/Г-33/Г-07/Г-46/Г-36) поверх сделок главного варианта (В-104, `t-bid-btc4h-q1`,
форма `ladder3x2..20w2-pct2-tr1x1-14400-ttl1800`) — «если бы торговали только сделки с флагом».

Статус (2026-09-26, Исследователь): **остановлено на шаге 1** (правило задачи: «если связь не
находится у заметной доли (> 10 %) — остановись и доложи, не подбирай»). Связь
сделка -> подход -> касание найдена (`link` ниже), но связь подход -> **касание** рвётся у
большинства сделок: в главном варианте вход идёт на ВЗВОДЕ подхода (`--signal approach`, F6/В-73:
«вход ставится на взводе, а не на касании»), а не на самом касании — многие подходы разряжаются
(`disarm_reason` = `level_death`/`price_left`) раньше, чем цена вообще коснулась уровня, и тогда
у сделки просто нет строки в `touches-<SYM>.csv` (`touch_start_ms` пуст). Замер (`link` mode,
2026-09-26): доля сделок со связью к касанию — август 66/490 = 13,5 %, история сентября (01–15)
34/131 = 26,0 %, запись сентября (16–23) 10/29 = 34,5 %; итого 110/650 ≈ 16,9 % (см. отчёт
Исследователю/CEO, коммит с этим файлом). Признаки Г-28/Г-08/Г-33/Г-07/Г-46/Г-36 (кроме Г-08 —
у него `round_zeros` тоже только в touches) все посчитаны на КАСАНИИ — для оставшихся ~83 % сделок
без строки в touches их взять неоткуда (нужны были бы версии признаков «на взводе», их П-02 не
считал). Шаги 2/3 (флаги, запись `v17-filt/*/rounds.csv`) не запускались — не на чем.

## Связь сделка -> подход (`link`)

Правило (измерено на данных, не придумано): подход тот же символ/сутки, `side=bid`,
`arm_ms == t0_ns // 1_000_000` (точное совпадение — расхождений не было ни разу в замере: t0_ns
сделки строится как `t.start_ms.saturating_mul(1_000_000)` от подхода-как-касания,
`src/commands/lob/bounce_grid/drive.rs:106`); при нескольких подходах с одним `arm_ms` (разные
`price_tick` того же символа/секунды — стена армит несколько соседних уровней разом) —
единственный, что проходит фильтр главного варианта `min_age_secs=2700` (В-104: возраст стены
≥ 45 мин) — `age_ms >= 2_700_000`. По всем трём домам это разрешает неоднозначность почти всегда
(16/506 неоднозначных в августе, 0 в сентябре — «остановка» здесь не сработала, доля мала).
Дальше, если у подхода `disarm_reason == "touch"` и `touch_start_ms` не пуст — ключ
`(side, price_tick, birth_ms, touch_start_ms)` ищется в `touches-<SYM>.csv` как
`(side, price_tick, birth_ms, start_ms)` -> `touch_index`.

Использование (на Steam Deck, пути — как у `p02-wall.py`/`p02-wall2.py`):
    python3 p02-variant-filter.py link --home epochs/e-aug --rounds-sub b5/titrc-u500r \
        --appr-root study/approaches/D20 --day-from 2026-08-01 --day-to 2026-08-31 \
        --out study/v17-filt-link-aug.json
    # для записи 16-23.09 (rounds — в tmp-t20/p2/rec, кэш D20 — в ~/alpha напрямую):
    python3 p02-variant-filter.py link --home tmp-t20/p2/rec --rounds-sub b5/titrc-u500r \
        --appr-home . --appr-root study/approaches/D20 --day-from 2026-09-16 --day-to 2026-09-23 \
        --out study/v17-filt-link-rec.json

Следующий шаг (не сделан, решение — владельцу/Судье): либо (а) считать флаги только на ~17 %
сделок со связью и явно писать в дашборде «покрытие ~17 %, не все сделки», либо (б) заводить
отдельный протокол «признаки на взводе» (по полям approaches: `size_at_arm`,
`strength_w10/20/50_pct`, `flow_1h_lots` — это не то же самое, что П-02 морозил для Г-28 и др.,
метод сравнения пришлось бы согласовывать заново), либо (в) считать вариант не по сделкам, а по
исходам подхода в целом (как блок A) — но тогда это не «сделки главного варианта», а другая
метрика, дашборду не подходит без пересчёта денег отдельным протоколом.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from datetime import date, timedelta
from typing import Dict, List

MAIN_FORM = "ladder3x2..20w2-pct2-tr1x1-14400-ttl1800"
MIN_AGE_MS = 2700 * 1000  # В-104: возраст стены >= 45 мин, min_age_secs=2700 главного варианта
EXCLUDE_SYMBOLS = {"TRXUSDT"}  # В-105


def daterange(day_from: str, day_to: str) -> List[str]:
    d, e = date.fromisoformat(day_from), date.fromisoformat(day_to)
    out = []
    while d <= e:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def cmd_link(args: argparse.Namespace) -> int:
    appr_home = args.appr_home if args.appr_home is not None else args.home
    tot: Dict[str, int] = defaultdict(int)
    disarm_counts: Dict[str, int] = defaultdict(int)
    examples_ambig = []
    examples_no_touch = []

    for day in daterange(args.day_from, args.day_to):
        rounds_path = os.path.join(args.home, args.rounds_sub, day, "t-bid-btc4h-q1", "rounds.csv")
        appr_dir = os.path.join(appr_home, args.appr_root, day)
        if not os.path.exists(rounds_path) or not os.path.isdir(appr_dir):
            continue
        rounds_by_sym: Dict[str, list] = defaultdict(list)
        with open(rounds_path, newline="", encoding="utf-8") as f:
            first = f.readline()
            if not first.startswith("#"):
                f.seek(0)
            for row in csv.DictReader(f):
                if row["form"] != MAIN_FORM or row["symbol"] in EXCLUDE_SYMBOLS:
                    continue
                rounds_by_sym[row["symbol"]].append(row)

        for sym, rows in rounds_by_sym.items():
            appr_path = os.path.join(appr_dir, f"approaches-{sym}.csv")
            touch_path = os.path.join(appr_dir, f"touches-{sym}.csv")
            if not os.path.exists(appr_path):
                tot["no_appr_file"] += len(rows)
                continue
            approaches_by_arm: Dict[int, list] = defaultdict(list)
            with open(appr_path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row["side"] != "bid":
                        continue
                    approaches_by_arm[int(row["arm_ms"])].append(row)
            touches_idx = set()
            if os.path.exists(touch_path):
                with open(touch_path, newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        touches_idx.add((row["side"], row["price_tick"], row["birth_ms"], row["start_ms"]))

            for row in rows:
                tot["total"] += 1
                t0_ms = int(row["t0_ns"]) // 1_000_000
                cands = approaches_by_arm.get(t0_ms, [])
                admitted = [c for c in cands if int(c["age_ms"]) >= MIN_AGE_MS]
                if len(admitted) == 0:
                    tot["appr_zero"] += 1
                    continue
                if len(admitted) > 1:
                    tot["appr_ambig"] += 1
                    if len(examples_ambig) < 5:
                        examples_ambig.append([sym, day, t0_ms, len(admitted)])
                    continue
                tot["appr_unique"] += 1
                appr = admitted[0]
                disarm_counts[appr["disarm_reason"]] += 1
                if appr["disarm_reason"] == "touch" and appr["touch_start_ms"]:
                    key = (appr["side"], appr["price_tick"], appr["birth_ms"], appr["touch_start_ms"])
                    if key in touches_idx:
                        tot["touch_linked"] += 1
                    else:
                        tot["touch_key_missing"] += 1
                        if len(examples_no_touch) < 5:
                            examples_no_touch.append([sym, day, "touch-key-missing"])
                else:
                    tot["no_touch"] += 1
                    if len(examples_no_touch) < 5:
                        examples_no_touch.append([sym, day, appr["disarm_reason"]])

    out = {
        "home": args.home, "appr_home": appr_home, "day_from": args.day_from, "day_to": args.day_to,
        "counts": dict(tot), "disarm_reason_of_matched": dict(disarm_counts),
        "examples_ambig": examples_ambig, "examples_no_touch": examples_no_touch,
        "share_touch_linked": (tot["touch_linked"] / tot["total"]) if tot["total"] else None,
    }
    print(json.dumps(out, ensure_ascii=False))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_link = sub.add_parser("link", help="шаг 1: покрытие связи сделка -> подход -> касание")
    p_link.add_argument("--home", required=True, help="дом rounds.csv (epochs/e-aug, epochs/e-archive, tmp-t20/p2/rec)")
    p_link.add_argument("--rounds-sub", default="b5/titrc-u500r")
    p_link.add_argument("--appr-home", default=None,
                        help="дом кэша D20, если отличается от --home (запись 16-23.09: кэш в ~/alpha, rounds в tmp-t20/p2/rec)")
    p_link.add_argument("--appr-root", default="study/approaches/D20")
    p_link.add_argument("--day-from", required=True)
    p_link.add_argument("--day-to", required=True)
    p_link.add_argument("--out", default=None)
    p_link.set_defaults(func=cmd_link)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
