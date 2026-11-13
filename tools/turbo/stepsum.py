#!/usr/bin/env python3
"""G-022 turbo: one-line verdict of a step window (`steps.sh`): reads win.json (abwin, run X), cov.json (coverage.py), rest.json (restcheck), cpu.json.

  stepsum.py <window dir> <label> [--no-rest]   -> prints the status line, writes sum.json, exit 1 if the window is not clean.
  --no-rest: the REST check is deferred (RESTPASS=defer, `restpass.sh`): the line carries `rest=deferred` and a missing rest.json is not a failure.
Clean = 0 drops (`error` events), mark coverage 100 % after covered, REST missing after covered 0, 0 shard-seconds without a live session, every shard connected.
"""
import json
import os
import sys


def load(p):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return None


def main():
    d, label = sys.argv[1], sys.argv[2]
    no_rest = "--no-rest" in sys.argv[3:]
    w = (load(os.path.join(d, "win.json")) or {}).get("runs", {}).get("X")
    cov, rest, cpu = load(os.path.join(d, "cov.json")), load(os.path.join(d, "rest.json")), load(os.path.join(d, "cpu.json"))
    reasons = []
    if not w or not cov:
        print(f"{label} FAIL no data (win.json/cov.json missing)")
        sys.exit(1)
    ev = w["events"]
    drops = w.get("drops", ev.get("error", 0))  # a live session that died (abwin); older abwin: all `error` events
    if drops:
        reasons.append(f"drops {drops}")
    if w.get("mark_cov") is not None and w["mark_missing_s"] > 0:
        reasons.append(f"mark gaps {w['mark_missing_s']} s in {w['mark_symbols_with_gap']} symbols (none at all: {w['mark_symbols_without_records']})")
    t = (rest or {}).get("total") or {}
    miss = t.get("missing_after_covered", t.get("missing_in_file"))
    if no_rest and rest is None:
        miss = None
    elif rest is None or not t:
        reasons.append("REST check missing")
    elif miss:
        reasons.append(f"REST missing after covered {miss} of {t.get('rest_after_covered')}")
    if cov["uncovered_after_first_s_total"] > 0:
        reasons.append(f"no live session {cov['uncovered_after_first_s_total']} shard-s")
    if cov["shards_connected"] < cov["shards"]:
        reasons.append(f"shards connected {cov['shards_connected']}/{cov['shards']}")
    s = {
        "label": label, "ok": not reasons, "reasons": reasons, "shards": cov["shards"], "first_connected_max_s": cov["first_connected_s"]["max"], "dials": w["dials"], "fast_share": w["fast_share"],
        "lag_p50": w["lag_p50"], "lag_p99": w["lag_p99"], "lag_max": w["lag_max"], "late": w["late"], "book_n": w["book_n"], "drops": drops, "connect_errors": w.get("connect_errors"), "timeouts": ev.get("timeout", 0),
        "path_switch": ev.get("path_switch", 0), "path_upgrade": ev.get("path_upgrade", 0), "replace_deferred": ev.get("replace_deferred", 0), "mark_cov": w.get("mark_cov"),
        "rest_missing_after_covered": miss, "rest": t.get("rest"), "uncovered_shard_s": cov["uncovered_after_first_s_total"], "cpu_pct": (cpu or {}).get("cpu_pct"),
    }
    json.dump(s, open(os.path.join(d, "sum.json"), "w"), indent=1)
    fs = "—" if w["fast_share"] is None else f"{w['fast_share'] * 100:.0f}% ({w['fast_dials']}/{w['dials']})"
    mc = "—" if s["mark_cov"] is None else f"{s['mark_cov'] * 100:.3f}%"
    line = (f"{label} {'ok' if not reasons else 'FAIL'} shards={s['shards']} fast={fs} lag p50/p99/max={s['lag_p50']}/{s['lag_p99']}/{s['lag_max']} ms late={s['late']} "
            f"drops={drops} connect_errors={s['connect_errors']} mark_cov={mc} {'rest=deferred' if no_rest and rest is None else f'rest_missing_after_covered={miss}/' + str(t.get('rest_after_covered'))} uncovered={s['uncovered_shard_s']} shard-s cpu={s['cpu_pct']}% core "
            f"switch/upgrade/deferred={s['path_switch']}/{s['path_upgrade']}/{s['replace_deferred']}")
    print(line + ("" if not reasons else " | " + "; ".join(reasons)))
    sys.exit(1 if reasons else 0)


if __name__ == "__main__":
    main()
