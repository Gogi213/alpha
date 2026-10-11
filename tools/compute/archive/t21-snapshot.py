#!/usr/bin/env python3
"""T-21, шаг «снимок до» (docs/research/reviews/t21-canon-2026-09-27.md §3, план Судьи п.3).

Собирает ОДИН машиночитаемый снимок всех чисел, которые видел владелец или на которых стоит
вердикт, — до перехода на общий модуль `_lib` (T-21). Тот же скрипт (тот же --out с другим именем)
соберёт «после»: числа сравниваются построчно по стабильному `id`.

Источники (только локальные файлы, ничего не считает заново):
  1. `data/titration-dashboard/index-v29.html` — встроенный JSON дашборда (плитки счёта, KPI,
     T-32, П-05, П-02, плечо).
  2. `data/kpi/kpi-roll-2026-09-27c.json` — ранжирование KPI «до перехая».
  3. `data/t32/*.json` (grid, exits, hedge, epcap, retries-all, boot, busy/busy-rules.json).
  4. П-02 блок B / П-07: `docs/findings/p02-r2-read-2026-09-26.json`,
     `docs/findings/p07-stage12-2026-09-27.json` (+ `data/p07/*.json`, если есть).
  5. «Шесть мест без одной позиции на монету» — не пересчитываются, только перечисляются со
     ссылками на находки, где встречается имя скрипта (grep по `docs/findings/*.md`).

Каждая запись: {id, source, producer, metric, period, variant, value, formula_tags}.
`formula_tags` — по каким осям канона (`docs/findings/t21-metrics-canon-2026-09-27.md`) число
посчитано, например: dollars:Ф1, drawdown:П1, day:t1|day_utc, month:epoch, portfolio:one-per-coin|
none, sharpe:merge, funding:no. Теги — по коду продюсера на момент снимка (см. отчёт), не по
канону — иначе снимок «до» уже был бы «после».
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def norm_source(s) -> str:
    """Путь источника одним видом (Судья 09b31f6, условие 3): прямые «/», относительно корня репо, если внутри —
    иначе «до» (Windows) и «после» (дека) не сойдутся по id."""
    s = str(s).replace("\\", "/")
    root = str(ROOT).replace("\\", "/").rstrip("/") + "/"
    return s[len(root):] if s.lower().startswith(root.lower()) else s


def file_meta(p) -> dict:
    """md5 и размер источника (Судья 09b31f6, условие 2: data/ не в git)."""
    p = Path(p)
    if not p.exists():
        return {"path": norm_source(p), "exists": False}
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return {"path": norm_source(p), "exists": True, "bytes": p.stat().st_size, "md5": h.hexdigest()}

PERIOD_KEYS = {"sep", "aug", "augsep", "crash"}
PERIOD_LABEL_RU = {"август": "aug", "сентябрь": "sep", "авг": "aug", "сен": "sep"}

# поля, которые не кладём в снимок как отдельные записи: либо сырые ряды (не "число, которое видел
# владелец" одной строкой), либо служебные списки монет/точек, не метрики.
SKIP_KEYS = {
    "daily_usd", "daily_pct", "periods", "roll_hist", "raw_start", "pool", "drop", "points",
    "main_stages", "p02_changes", "p02_undefined", "generated_utc", "generated",
}


def load_dashboard_json(html_path: Path) -> dict:
    html = html_path.read_text(encoding="utf-8")
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise ValueError(f"не нашёл <script id=data> в {html_path}")
    txt = m.group(1).replace("<\\/", "</")
    return json.loads(txt)


class Recorder:
    def __init__(self):
        self.records = []
        self._seen_ids = set()

    def add(self, source, producer, metric, period, variant, value, tags, extra=None):
        if value is None:
            return
        if isinstance(value, (dict, list)):
            return  # не скаляр — не запись
        source = norm_source(source)
        base = f"{source}::{metric}::{period}::{variant}"
        rid = base
        i = 2
        while rid in self._seen_ids:
            rid = f"{base}#{i}"
            i += 1
        self._seen_ids.add(rid)
        rec = {
            "id": rid,
            "source": source,
            "producer": producer,
            "metric": metric,
            "period": period,
            "variant": variant,
            "value": value,
            "formula_tags": list(tags),
        }
        if extra:
            rec.update(extra)
        self.records.append(rec)


def flatten_scalars(prefix_metric, obj, rec: Recorder, source, producer, period, variant, tags, extra=None):
    """Кладёт каждое скалярное поле словаря как отдельную запись metric=<путь через .>."""
    if not isinstance(obj, dict):
        return
    for k, v in obj.items():
        if k in SKIP_KEYS:
            continue
        metric = f"{prefix_metric}.{k}" if prefix_metric else k
        if isinstance(v, dict):
            flatten_scalars(metric, v, rec, source, producer, period, variant, tags, extra)
        elif isinstance(v, list):
            continue  # списки словарей/чисел — не сюда (см. специализированные обходчики)
        else:
            rec.add(source, producer, metric, period, variant, v, tags, extra)


# ---------------------------------------------------------------------------
# 1. Дашборд v29
# ---------------------------------------------------------------------------

# Судья 09b31f6, условие 1: запасной `kpi` / `D.trades` (без правил счёта, месяц по `day_utc`) дашборд
# показывает владельцу и при наличии счёта — «Причины выхода» (`kpi.reasons` всегда), «Результаты сделок»
# (гистограмма) и «По монетам»; плитки — из `account`. Вердиктов на этих блоках нет → не «ошибка», а пометка.
REASONS_VISIBLE = {"owner_visible": ["Причины выхода"]}
FALLBACK_VISIBLE = {"owner_visible": ["Результаты сделок", "По монетам", "плитки — только если нет account"]}


def extract_dashboard(data: dict, rec: Recorder, html_name: str, missing: list):
    src = f"data/titration-dashboard/{html_name}"
    producer = "tools/compute/titration-dashboard-merge.py"

    # account: period -> variant -> {скаляры}. Из portfolio-sim grid (одна позиция на монету).
    for period, byvar in (data.get("account") or {}).items():
        for variant, fields in (byvar or {}).items():
            flatten_scalars(
                "account", fields, rec, src, producer, period, variant,
                ["dollars:Ф1", "drawdown:П1", "sharpe:merge", "portfolio:one-per-coin",
                 "day:day_utc", "month:epoch", "funding:no"],
            )

    # kpi: запасной KPI дашборда (merge.trade_stats) — без правила "одна позиция на монету".
    for period, byvar in (data.get("kpi") or {}).items():
        for variant, fields in (byvar or {}).items():
            if not isinstance(fields, dict):
                continue
            flatten_scalars(
                "kpi_fallback", fields, rec, src, producer, period, variant,
                ["dollars:Ф1", "drawdown:П2", "portfolio:none", "day:day_utc", "month:epoch",
                 "funding:no"],
                extra=FALLBACK_VISIBLE,
            )
            for reason, rfields in (fields.get("reasons") or {}).items():
                flatten_scalars(
                    f"kpi_fallback.reasons.{reason}", rfields, rec, src, producer, period, variant,
                    ["dollars:Ф1", "portfolio:none", "day:day_utc", "month:epoch"],
                    extra=REASONS_VISIBLE,
                )

    # protections: period -> variant -> [шаги защиты] (label + скаляры, dd_usd_trade/dd_pct_trade = П2)
    for period, byvar in (data.get("protections") or {}).items():
        for variant, steps in (byvar or {}).items():
            if not isinstance(steps, list):
                continue
            for i, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                label = step.get("label", f"step{i}")
                flatten_scalars(
                    f"protections[{label}]", {k: v for k, v in step.items() if k != "label"},
                    rec, src, producer, period, f"{variant}/{label}",
                    ["dollars:Ф1", "drawdown:П1|П2", "sharpe:merge", "portfolio:one-per-coin",
                     "day:day_utc", "month:epoch", "funding:no"],
                )
                for reason, rfields in (step.get("reasons") or {}).items():
                    flatten_scalars(
                        f"protections.reasons.{reason}", rfields, rec, src, producer, period,
                        f"{variant}/{label}",
                        ["dollars:Ф1", "portfolio:one-per-coin", "day:day_utc"],
                    )

    # nh: KPI «до перехая» (kpi-newhigh), рейтинг, T-32, П-05 внутри дашборда, плечо, подходы.
    nh = data.get("nh") or {}
    rec.add(src, producer, "nh.n_rows", "-", "-", nh.get("n_rows"), ["month:epoch"])
    rec.add(src, producer, "nh.n_pass", "-", "-", nh.get("n_pass"), ["month:epoch"])
    for tag, val in (nh.get("status") and {"status": nh["status"]} or {}).items():
        rec.add(src, producer, f"nh.{tag}", "-", "-", val, [])

    for variant, byperiod in (nh.get("by_variant") or {}).items():
        for period, fields in (byperiod or {}).items():
            if period == "kpi07":
                continue
            if not isinstance(fields, dict):
                continue
            flatten_scalars(
                "nh.roll", {k: v for k, v in fields.items() if k not in ("periods", "roll_hist")},
                rec, src, producer, period, variant,
                ["day:t1", "month:epoch", "portfolio:one-per-coin"],
            )
        kpi07 = byperiod.get("kpi07") if isinstance(byperiod, dict) else None
        if isinstance(kpi07, dict):
            for field, perval in kpi07.items():
                if isinstance(perval, dict):
                    for period, v in perval.items():
                        rec.add(src, producer, f"nh.kpi07.{field}", period, variant, v,
                                 ["month:epoch", "portfolio:one-per-coin"])
                else:
                    rec.add(src, producer, f"nh.kpi07.{field}", "-", variant, perval,
                            ["month:epoch"])

    for row in (nh.get("rating") or []):
        if not isinstance(row, dict):
            continue
        variant = row.get("key") or row.get("name")
        base = {k: v for k, v in row.items() if k not in ("aug", "sep", "augsep", "key", "name")}
        flatten_scalars("nh.rating", base, rec, src, producer, "-", variant, ["month:epoch"])
        for period in ("aug", "sep", "augsep"):
            if isinstance(row.get(period), dict):
                flatten_scalars("nh.rating", row[period], rec, src, producer, period, variant,
                                 ["dollars:Ф1", "drawdown:П3'", "month:epoch",
                                  "portfolio:one-per-coin"])

    t32 = nh.get("t32") or {}
    for block_name, rows in t32.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            descriptor = "/".join(
                str(row[k]) for k in ("hedge", "limit", "name") if k in row
            ) or "row"
            for period in ("aug", "sep", "augsep", "crash"):
                sub = row.get(period)
                if isinstance(sub, dict):
                    flatten_scalars(
                        f"nh.t32.{block_name}", sub, rec, src, producer, period, descriptor,
                        ["dollars:Ф6", "day:t1", "month:epoch", "portfolio:one-per-coin"],
                    )

    lev = nh.get("lev") or {}
    rec.add(src, producer, "nh.lev.deposit_usd", "-", "-", lev.get("deposit_usd"), [])
    for period, sizes in (lev.get("rows") or {}).items():
        for size, fields in (sizes or {}).items():
            flatten_scalars("nh.lev", fields, rec, src, producer, period, f"size{size}",
                             ["dollars:Ф1", "drawdown:П1", "month:epoch",
                              "portfolio:one-per-coin"])

    appr = nh.get("approach") or {}
    rec.add(src, producer, "nh.approach.max_approach_number", "-", "-",
             appr.get("max_approach_number"), [])
    rec.add(src, producer, "nh.approach.tail_from", "-", "-", appr.get("tail_from"), [])
    for period, v in (appr.get("n_trades") or {}).items():
        rec.add(src, producer, "nh.approach.n_trades", period, "-", v, ["month:epoch"])
    for row in (appr.get("by_number") or []):
        if not isinstance(row, dict):
            continue
        variant = f"approach{row.get('key')}"
        for period in ("aug", "sep"):
            if isinstance(row.get(period), dict):
                flatten_scalars("nh.approach.by_number", row[period], rec, src, producer, period,
                                 variant, ["dollars:Ф1", "day:day_utc", "month:epoch"])

    for edge in (nh.get("edges_days") or []):
        rec.add(src, producer, "nh.edges_days", "-", "-", edge, [])

    # p05: клетки, счёт по вариантам П-05, просадка от размера
    p05 = data.get("p05") or {}
    rec.add(src, producer, "p05.status", "-", "-", p05.get("status"), [])
    for key, cell in (p05.get("cells") or {}).items():
        base = {k: v for k, v in cell.items() if k != "months"}
        flatten_scalars("p05.cells", base, rec, src, producer, "-", key, [])
        for period, fields in (cell.get("months") or {}).items():
            flatten_scalars("p05.cells.months", fields, rec, src, producer, period, key,
                             ["dollars:Ф1", "drawdown:П1", "day:day_utc", "month:epoch",
                              "portfolio:one-per-coin"])
    for period, byvar in (p05.get("account") or {}).items():
        for variant, fields in (byvar or {}).items():
            flatten_scalars("p05.account", fields, rec, src, producer, period, variant,
                             ["dollars:Ф1", "drawdown:П1", "sharpe:merge", "day:day_utc",
                              "month:epoch", "portfolio:one-per-coin"])
    for period, byvar in (p05.get("size_dd") or {}).items():
        for variant, sizes in (byvar or {}).items():
            for size, v in (sizes or {}).items():
                rec.add(src, producer, "p05.size_dd", period, f"{variant}/x{size}", v,
                         ["drawdown:П7", "month:epoch"])

    # p02: денежные гипотезы (Δ по месяцам, из dashboard §10)
    p02 = data.get("p02") or {}
    rec.add(src, producer, "p02.r2_status", "-", "-", p02.get("r2_status"), [])
    rec.add(src, producer, "p02.coins_status", "-", "-", p02.get("coins_status"), [])
    for row in (p02.get("rows") or []):
        if not isinstance(row, dict):
            continue
        h = row.get("h", "row")
        for k in ("unit", "v10", "r2"):
            if row.get(k) is not None:
                rec.add(src, producer, f"p02.rows.{k}", "-", h, row[k], [])
        for month_ru, fields in (row.get("months") or {}).items():
            period = PERIOD_LABEL_RU.get(month_ru, month_ru)
            flatten_scalars("p02.rows.months", fields, rec, src, producer, period, h,
                             ["day:t0", "month:t0", "portfolio:one-per-coin"])

    if not (data.get("nh") and data.get("p05") and data.get("p02")):
        missing.append({"what": "часть блоков nh/p05/p02 дашборда пуста", "source": src})


# ---------------------------------------------------------------------------
# 2. KPI-ранжирование
# ---------------------------------------------------------------------------

def extract_kpi_roll(path: Path, rec: Recorder, missing: list):
    if not path.exists():
        missing.append({"what": "kpi-roll ranking", "source": str(path.relative_to(ROOT))})
        return
    d = json.loads(path.read_text(encoding="utf-8"))
    src = str(path.relative_to(ROOT))
    producer = "tools/compute/kpi-newhigh.py (+ nightly-read.py по журналу)"
    rec.add(src, producer, "n_rows", "-", "-", d.get("n_rows"), [])
    rec.add(src, producer, "n_pairs", "-", "-", d.get("n_pairs"), [])
    for variant, row in (d.get("results") or {}).items():
        if not isinstance(row, dict):
            continue
        rec.add(src, producer, "group", "-", variant, row.get("group"), [])
        for period in ("aug", "sep", "augsep"):
            fields = row.get(period)
            if isinstance(fields, dict):
                flatten_scalars(
                    "results", {k: v for k, v in fields.items() if k not in ("hours", "days")},
                    rec, src, producer, period, variant,
                    ["dollars:Ф1", "drawdown:П3'", "day:t1", "month:epoch",
                     "portfolio:one-per-coin"],
                )
                for unit in ("hours", "days"):
                    sub = fields.get(unit)
                    if isinstance(sub, dict):
                        flatten_scalars(f"results.{unit}", sub, rec, src, producer, period,
                                         variant, ["month:epoch"])
        for period, roll in (row.get("roll") or {}).items():
            for grp in ("max", "start"):
                if isinstance(roll.get(grp), dict):
                    flatten_scalars(f"roll.{grp}", roll[grp], rec, src, producer, period, variant,
                                     ["month:epoch"])
            for k in ("frac_gt_h", "n_main", "h_hours"):
                if k in roll:
                    rec.add(src, producer, f"roll.{k}", period, variant, roll[k], ["month:epoch"])
        kpi07 = row.get("kpi07")
        if isinstance(kpi07, dict):
            for field, perval in kpi07.items():
                if isinstance(perval, dict):
                    for period, v in perval.items():
                        rec.add(src, producer, f"kpi07.{field}", period, variant, v,
                                 ["month:epoch", "portfolio:one-per-coin"])
                else:
                    rec.add(src, producer, f"kpi07.{field}", "-", variant, perval, [])
    for i, name in enumerate(d.get("rank") or []):
        rec.add(src, producer, "rank", "-", name, i + 1, [])


# ---------------------------------------------------------------------------
# 3+4. Универсальный обходчик "период(aug/sep/augsep/crash) в ключах словаря"
# ---------------------------------------------------------------------------

def generic_extract(path: Path, rec: Recorder, producer: str, tags, missing: list,
                     row_key_fields=("name", "hedge", "limit", "key")):
    if not path.exists():
        missing.append({"what": f"{path.name}", "source": str(path)})
        return
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        missing.append({"what": f"не прочитан {path.name}: {e}", "source": str(path)})
        return
    src = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)

    def walk(obj, metric_prefix, variant):
        if isinstance(obj, dict):
            # период среди ключей?
            period_keys_here = set(obj.keys()) & PERIOD_KEYS
            if period_keys_here:
                for pk in period_keys_here:
                    sub = obj[pk]
                    if isinstance(sub, dict):
                        flatten_scalars(metric_prefix, sub, rec, src, producer, pk, variant, tags)
                    elif not isinstance(sub, list):
                        rec.add(src, producer, metric_prefix, pk, variant, sub, tags)
                for k, v in obj.items():
                    if k in period_keys_here or k in SKIP_KEYS:
                        continue
                    if isinstance(v, (dict, list)):
                        continue
                    rec.add(src, producer, f"{metric_prefix}.{k}" if metric_prefix else k, "-",
                            variant, v, tags)
                return
            desc = None
            for fk in row_key_fields:
                if fk in obj and not isinstance(obj[fk], (dict, list)):
                    desc = str(obj[fk])
                    break
            if desc:
                variant = f"{variant}/{desc}" if variant not in ("-", None) else desc
            for k, v in obj.items():
                if k in SKIP_KEYS or k in row_key_fields:
                    continue
                new_metric = f"{metric_prefix}.{k}" if metric_prefix else k
                if isinstance(v, (dict, list)):
                    walk(v, new_metric, variant)
                else:
                    rec.add(src, producer, new_metric, "-", variant, v, tags)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                walk(item, metric_prefix, variant if desc_from_list(item) is None
                     else f"{variant}/{desc_from_list(item)}" if variant not in ("-", None)
                     else desc_from_list(item))

    def desc_from_list(item):
        if isinstance(item, dict):
            for fk in row_key_fields:
                if fk in item and not isinstance(item[fk], (dict, list)):
                    return str(item[fk])
        return None

    walk(d, "", "-")


# ---------------------------------------------------------------------------
# 5. "Шесть мест без одной позиции на монету" — не пересчитываем, только собираем ссылки
# ---------------------------------------------------------------------------

SIX_PLACES = {
    "merge.trade_stats": "tools/compute/titration-dashboard-merge.py:trade_stats "
                          "(запасной KPI дашборда, когда у варианта нет счёта portfolio-sim)",
    "placebo": "tools/compute/placebo.py",
    "titration-read": "tools/compute/titration-read.py",
    "breakdown": "tools/compute/breakdown.py",
    "equity-report": "tools/compute/equity-report.py",
    "loss-atoms": "tools/compute/loss-atoms.py",
}

SELF_DOCS = {
    "docs/findings/t21-metrics-canon-2026-09-27.md",
    "docs/findings/third-party-backtest-approaches-2026-09-26.md",
}


def scan_six_places(findings_dir: Path):
    out = {}
    for key, note in SIX_PLACES.items():
        needle = "trade_stats" if key == "merge.trade_stats" else key
        hits = []
        for p in sorted(findings_dir.glob("*.md")):
            rel = f"docs/findings/{p.name}"
            if rel in SELF_DOCS:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:  # noqa: BLE001
                continue
            if needle in text:
                hits.append(rel)
        out[key] = {"producer": note, "docs_mentioning": hits,
                    "note": "найдено по совпадению имени скрипта в findings; наличие $ в каждом "
                            "документе — не проверено построчно (см. отчёт), реальный расчёт не "
                            "повторялся"}
    return out


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "docs/findings/t21-snapshot-before-2026-09-27.json.gz"),
                    help="*.json.gz — gzip (в git кладётся сжатый: полный JSON ~34 МБ)")
    ap.add_argument("--dashboard-html", default=str(ROOT / "data/titration-dashboard/index-v29.html"))
    ap.add_argument("--kpi-roll", default=str(ROOT / "data/kpi/kpi-roll-2026-09-27c.json"))
    ap.add_argument("--t32-dir", default=str(ROOT / "data/t32"))
    ap.add_argument("--p02-r2", default=str(ROOT / "docs/findings/p02-r2-read-2026-09-26.json"))
    ap.add_argument("--p07-stage12", default=str(ROOT / "docs/findings/p07-stage12-2026-09-27.json"))
    ap.add_argument("--findings-dir", default=str(ROOT / "docs/findings"))
    args = ap.parse_args()

    rec = Recorder()
    missing = []

    dash_path = Path(args.dashboard_html)
    if dash_path.exists():
        data = load_dashboard_json(dash_path)
        extract_dashboard(data, rec, dash_path.name, missing)
    else:
        missing.append({"what": "дашборд v29", "source": str(dash_path)})

    extract_kpi_roll(Path(args.kpi_roll), rec, missing)

    t32_dir = Path(args.t32_dir)
    t32_files = {
        "grid.json": ("tools/compute/t32-grid.py",
                      ["dollars:Ф6", "day:t1", "month:epoch", "portfolio:one-per-coin"]),
        "exits.json": ("tools/compute/t32-exits.py",
                       ["dollars:Ф6", "day:t1", "month:epoch", "portfolio:one-per-coin"]),
        "hedge.json": ("tools/compute/t32-hedge.py",
                       ["dollars:Ф6", "day:t1", "month:epoch"]),
        "epcap.json": ("tools/compute/t32-epcap.py",
                       ["dollars:Ф6", "day:t1", "month:epoch"]),
        "retries-all.json": ("tools/compute/t32-retries-all.py", ["month:epoch"]),
        "boot.json": ("tools/compute/t32-boot.py", ["month:epoch"]),
    }
    for fname, (producer, tags) in t32_files.items():
        generic_extract(t32_dir / fname, rec, producer, tags, missing)
    generic_extract(t32_dir / "busy" / "busy-rules.json", rec, "tools/compute/t32-busy-stress.py",
                     ["month:epoch"], missing)

    generic_extract(Path(args.p02_r2), rec, "tools/compute/p02-r2-read.py",
                     ["day:t0", "month:t0", "portfolio:one-per-coin"], missing,
                     row_key_fields=("h",))
    generic_extract(Path(args.p07_stage12), rec, "tools/compute/p07-h9h10.py "
                     "(+p07-cells/p07-h1-quantiles, см. docs/findings/p07-stage12-2026-09-27.md)",
                     ["day:t0", "month:t0"], missing, row_key_fields=("name", "key"))

    six = scan_six_places(Path(args.findings_dir))

    by_source = {}
    for r in rec.records:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1

    out = {
        "meta": {
            "generated_for": "T-21 снимок «до» (docs/research/reviews/t21-canon-2026-09-27.md §3)",
            "canon_doc": "docs/findings/t21-metrics-canon-2026-09-27.md",
            "n_records": len(rec.records),
            "records_per_source": by_source,
            "sources": [file_meta(p) for p in (
                [dash_path, Path(args.kpi_roll)] + [t32_dir / f for f in t32_files] +
                [t32_dir / "busy" / "busy-rules.json", Path(args.p02_r2), Path(args.p07_stage12)])],
        },
        "records": rec.records,
        "missing": missing,
        "owner_visible_without_one_per_coin": six,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if out_path.suffix == ".gz":
        with gzip.GzipFile(out_path, "wb", compresslevel=9, mtime=0) as f:  # mtime=0 — побайтно воспроизводимо
            f.write(text.encode("utf-8"))
    else:
        out_path.write_text(text, encoding="utf-8")
    print(f"записей: {len(rec.records)}; источников: {len(by_source)}; пропущено: {len(missing)}")
    for s, n in sorted(by_source.items()):
        print(f"  {s}: {n}")
    if missing:
        print("missing:")
        for m in missing:
            print(f"  {m}")


if __name__ == "__main__":
    main()
