"""П-42 §13.18 п. 0 (TK-592): правки отчёта TK-588 по его проверке — пересчёт таблиц с исправленной мерой разреженности (`zv6h`), контролем на тех же эпизодах,
меткой с глубиной возврата и таблицей расстояний с медианами и p90.

Использование: python3 tk592_fix588.py <episodes.parquet TK-588> <ep_u1.parquet (tk592_build --univ U1)> <ep_u0.parquet> <выход .md/.json в каталог>
Метки U0 (все эпизоды) читаются из таблицы TK-588; контроль для U1 и U0 независимых — из таблиц `tk592_build.py`.
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sigfeat_lib import block_resamples, cluster_resamples, load_b42, sparse_features, auc  # noqa: E402

CACHE = "/data/p42-t566/cache"
B = 1000
RNG = np.random.default_rng(592)
O3 = 3


def add_zv(E: pd.DataFrame) -> pd.DataFrame:
    """zv2h / zv6h по кэшу минуток тем же кодом `sparse_features` (исправление П1)."""
    zv2, zv6 = np.full(len(E), np.nan), np.full(len(E), np.nan)
    E = E.reset_index(drop=True)
    for sym, g in E.groupby("sym"):
        m = load_b42(f"{CACHE}/{sym}.b42")
        fv = int(np.argmax(m["o"] != 0)) if (m["o"] != 0).any() else len(m["o"])
        for i, ms in zip(g.index, g["sig_ms"]):
            f = sparse_features(m, fv, int(ms // 60000))
            zv2[i], zv6[i] = f["zv2h"], f["zv6h"]
    E["zv2h"], E["zv6h"] = zv2, zv6
    return E


def boot(stat, days, coins):
    """Интервалы 95 % статистики `stat(ix)`: блоки суток (4) и монеты целиком."""
    out = {}
    for name, rs in (("day", block_resamples(days, B, 4, RNG)), ("coin", cluster_resamples(coins, B, RNG))):
        v = np.array([stat(ix) for ix in rs])
        v = v[~np.isnan(v)]
        out[name] = (float(np.quantile(v, 0.025)), float(np.quantile(v, 0.975))) if len(v) else (np.nan, np.nan)
    return out


def pp(x):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.1f}"


def pct(x):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.1f}"


def ci(c):
    return f"[{c[0] * 100:+.1f}; {c[1] * 100:+.1f}]"


def diff_row(df, mask, val, label):
    """Доля `val` в группе `mask` против вне группы + интервалы."""
    v = df[val].to_numpy(float)
    m = mask.to_numpy()
    n1, n0 = int(m.sum()), int((~m).sum())
    d = float(v[m].mean() - v[~m].mean()) if n1 and n0 else np.nan

    def st(ix):
        mm, vv = m[ix], v[ix]
        return float(vv[mm].mean() - vv[~mm].mean()) if mm.any() and (~mm).any() else np.nan

    c = boot(st, df["day"].to_numpy(), df["sym"].to_numpy()) if n1 > 1 and n0 > 1 else {"day": (np.nan,) * 2, "coin": (np.nan,) * 2}
    return {"group": label, "n1": n1, "n0": n0, "p1": float(v[m].mean()) if n1 else np.nan, "p0": float(v[~m].mean()) if n0 else np.nan, "diff": d, "ci_day": c["day"], "ci_coin": c["coin"]}


def excess_row(df, mask, label):
    """Превышение над контролем на тех же эпизодах: среднее (y − c) по группе, интервалы."""
    g = df[mask & (df["c_n"] >= 5)].reset_index(drop=True)
    n = len(g)
    if n < 2:
        return {"group": label, "n": n}
    d = g["y"].to_numpy(float) - g["c"].to_numpy(float)
    c = boot(lambda ix: float(d[ix].mean()), g["day"].to_numpy(), g["sym"].to_numpy())
    return {"group": label, "n": n, "y": float(g["y"].mean()), "c": float(g["c"].mean()), "excess": float(d.mean()), "ci_day": c["day"], "ci_coin": c["coin"]}


def main():
    ep_path, u1_path, u0_path, out = sys.argv[1:5]
    os.makedirs(out, exist_ok=True)
    E = pd.read_parquet(ep_path)
    lines = ["# Правки отчёта TK-588 по его проверке — пересчёт (TK-592 §13.18 п. 0)", ""]
    res = {}
    # ── П1: zv6h вместо emp6h ──
    U1 = pd.read_parquet(u1_path)  # эпизоды пула с контролем; zv уже есть
    U0i = pd.read_parquet(u0_path)
    U0all = add_zv(E[E.univ == "U0"][["side", "sym", "sig_ms", "day", "half", "o_x1_t60", "o_x1_t60_f01", "ok_t60", "indep", "tpm60"]].copy())
    U0all = U0all[U0all.ok_t60]
    lines += ["## 1. Разреженность по `zv6h` (доля минут с `qv = 0` за 6 ч до сигнала; было — вырожденная `emp6h`)", "",
              "Группа Z — верхний квинтиль `zv6h` по границе половины A (если граница 0 — `zv6h > 0`); эпизоды с окном 60 мин целиком. «Строгая» — O3 основной метки §13.17, «глубина» — O3 с возвратом глубже 0,1 %. Интервалы — блоки суток [монеты], п.п.; найдено на этих данных.", "",
              "| вселенная | сторона | эпизодов | Z, % эп. | граница | строгая: Z / вне | Δ [сутки] [монеты] | глубина: Z / вне | Δ [сутки] [монеты] | AUC строгая A / B | AUC глубина A / B | объяснённая доля непробоев, строгая |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for uname, df in (("U1 (все эпизоды пула)", U1.assign(ys=U1["y_s"], yd=U1["y"])), ("U0 независимые", U0i.assign(ys=U0i["y_s"], yd=U0i["y"])),
                      ("U0 все эпизоды (точки без интервалов)", U0all.assign(ys=(U0all["o_x1_t60"] == O3).astype(int), yd=(U0all["o_x1_t60_f01"] == O3).astype(int)))):
        for side in ("long", "short"):
            g = df[(df.side == side) & df.zv6h.notna()].reset_index(drop=True)
            q = float(np.quantile(g[g.half == "A"]["zv6h"], 0.8))
            Z = (g.zv6h > q) if q > 0 else (g.zv6h > 0)
            small = len(g) < 3000 or "U0 независимые" in uname
            row_s = diff_row(g, Z, "ys", "Z") if "все эпизоды (точки" not in uname else None
            row_d = diff_row(g, Z, "yd", "Z") if "все эпизоды (точки" not in uname else None
            ps, po = g[Z]["ys"].mean(), g[~Z]["ys"].mean()
            pds, pdo = g[Z]["yd"].mean(), g[~Z]["yd"].mean()
            a_s = [auc(g[g.half == h]["zv6h"].to_numpy(float), g[g.half == h]["ys"].to_numpy()) for h in ("A", "B")]
            a_d = [auc(g[g.half == h]["zv6h"].to_numpy(float), g[g.half == h]["yd"].to_numpy()) for h in ("A", "B")]
            nonb = int((1 - g["ys"]).sum())
            expl = (po - ps) * int(Z.sum()) / nonb if nonb else np.nan
            cs = f"{ci(row_s['ci_day'])} {ci(row_s['ci_coin'])}" if row_s else "—"
            cd = f"{ci(row_d['ci_day'])} {ci(row_d['ci_coin'])}" if row_d else "—"
            lines.append(f"| {uname} | {side} | {len(g)} | {Z.mean() * 100:.1f} | {q:.3f} | {pct(ps)} / {pct(po)} | {pp(ps - po)} {cs} | {pct(pds)} / {pct(pdo)} | {pp(pds - pdo)} {cd} | "
                         f"{a_s[0]:.3f} / {a_s[1]:.3f} | {a_d[0]:.3f} / {a_d[1]:.3f} | {pp(expl)} |")
            res[f"zv6h|{uname}|{side}"] = {"q": q, "z_share": float(Z.mean()), "ps": float(ps), "po": float(po), "pds": float(pds), "pdo": float(pdo), "auc_s": a_s, "auc_d": a_d, "expl": float(expl)}
    # ── П2: контроль на тех же эпизодах (U1) ──
    U1["c"] = U1["c_s"]
    lines += ["", "## 2. Контроль на тех же эпизодах (U1; контроль — тот же генератор §13.17, зерно 588, состояние пула; только эпизоды с ≥ 5 окнами)", "",
              "Строгая метка: `y_s` против `c_s`; метка с глубиной возврата 0,1 %: `y` против `c_f`. Превышение = среднее (доля эпизода − доля контроля эпизода), п.п.; интервалы блоки суток [монеты].", "",
              "| метка | сторона | выборка | эпизодов | доля метки | доля контроля | превышение | [сутки] | [монеты] |", "|---|---|---|---|---|---|---|---|---|"]
    for lab, ycol, ccol in (("строгая", "y_s", "c_s"), ("глубина 0,1 %", "y", "c_f")):
        for side in ("long", "short"):
            base = U1[(U1.side == side)].copy()
            base["c"], base["yy"] = base[ccol], base[ycol]
            base = base.drop(columns=["y"]).rename(columns={"yy": "y"})
            for name, m in (("все", base.day >= 0), ("половина A", base.half == "A"), ("половина B", base.half == "B"), ("независимые подходы", base.indep == 1)):
                r = excess_row(base, m, name)
                if r.get("n", 0) >= 2:
                    lines.append(f"| {lab} | {side} | {name} | {r['n']} | {pct(r['y'])} % | {pct(r['c'])} % | {pp(r['excess'])} | {ci(r['ci_day'])} | {ci(r['ci_coin'])} |")
                    res[f"excess|{lab}|{side}|{name}"] = r
            # без 5 монет с наибольшим числом пробоев
            top = base.groupby("sym")["y"].sum().sort_values(ascending=False).index[:5]
            r = excess_row(base, ~base.sym.isin(top), "без 5 монет с наибольшим числом пробоев")
            lines.append(f"| {lab} | {side} | без 5 монет с наибольшим числом пробоев | {r['n']} | {pct(r['y'])} % | {pct(r['c'])} % | {pp(r['excess'])} | {ci(r['ci_day'])} | {ci(r['ci_coin'])} |")
    # ── П4: темы 1 и 3 на метке с глубиной ──
    lines += ["", "## 3. Зона против чистого и проторговка: метка с глубиной возврата 0,1 % рядом со строгой (U1, на тех же эпизодах с контролем)", "",
              "| тема | сторона | метка | группа A (зона / без проторговки) vs B (чистый / с проторговкой): доля метки | контроль A / B | Δ метки [сутки] [монеты] | половины A / B |", "|---|---|---|---|---|---|---|"]
    for tema, col, ga in (("зона (`pierce_ep ≥ 1`) против чистого", "pierce_ep", lambda d: d.pierce_ep >= 1), ("без проторговки (`cons60 = 0`) против с проторговкой", "cons60", lambda d: d.cons60 == 0)):
        for side in ("long", "short"):
            for lab, ycol, ccol in (("строгая", "y_s", "c_s"), ("глубина 0,1 %", "y", "c_f")):
                g = U1[(U1.side == side) & (U1.c_n >= 5)].reset_index(drop=True)
                g = g.assign(yy=g[ycol], cc=g[ccol])
                mk = ga(g)
                row = diff_row(g.assign(v=g["yy"]), mk, "v", "")
                ha = {h: (g[(g.half == h) & mk]["yy"].mean() - g[(g.half == h) & ~mk]["yy"].mean()) for h in ("A", "B")}
                lines.append(f"| {tema} | {side} | {lab} | {pct(row['p1'])} % против {pct(row['p0'])} % ({row['n1']} / {row['n0']}) | {pct(g[mk]['cc'].mean())} / {pct(g[~mk]['cc'].mean())} % | "
                             f"{pp(row['diff'])} {ci(row['ci_day'])} {ci(row['ci_coin'])} | {pp(ha['A'])} / {pp(ha['B'])} |")
                res[f"theme|{tema}|{side}|{lab}"] = {k: (v if not isinstance(v, tuple) else list(v)) for k, v in row.items()}
    # ── П6: таблица достижения расстояний с медианами и p90 ──
    lines += ["", "## 4. Достижение расстояний до уровня (U1 все эпизоды, окно 60 мин; медианы и p90 — только для времён и расстояний)", "",
              "Время и неблагоприятный ход — по достигшим; «достигло» = рыночный вход по цене на расстоянии d от уровня (на ~20 бп хуже цены сигнала), а не исполнение лимитки. Строка «O3 среди достигших» = безусловная доля пробоев, делённая на долю достижения (среди недостигших O3 = 0 по построению) и не показывает, что вход ближе к уровню отбирает пробои.", "",
              "| сторона | d от уровня | достигло, % | время: медиана / среднее / p90, с | неблагоприятный ход: медиана / среднее / p90, бп | достигло за ≤ 1 с, % | O3 строгая среди достигших, % | O3 глубина среди достигших, % |", "|---|---|---|---|---|---|---|---|"]
    Eu = E[E.univ == "U1"]
    for side in ("long", "short"):
        g = Eu[Eu.side == side]
        for dn, lab in (("0p2", "0,2 %"), ("0p1", "0,1 %"), ("0p05", "0,05 %")):
            t, mae = g[f"reach_{dn}_s"], g[f"mae_{dn}_bp"]
            r = t.notna()
            lines.append(f"| {side} | {lab} | {r.mean() * 100:.1f} | {t.median():.0f} / {t.mean():.0f} / {t.quantile(.9):.0f} | {mae.median():.0f} / {mae.mean():.0f} / {mae.quantile(.9):.0f} | "
                         f"{(t <= 1).mean() * 100:.1f} | {(g[r].o_x1_t60 == O3).mean() * 100:.1f} | {(g[r].o_x1_t60_f01 == O3).mean() * 100:.1f} |")
            res[f"reach|{side}|{dn}"] = {"reached": float(r.mean()), "t_med": float(t.median()), "t_mean": float(t.mean()), "t_p90": float(t.quantile(.9)),
                                         "mae_med": float(mae.median()), "mae_mean": float(mae.mean()), "mae_p90": float(mae.quantile(.9))}
    open(f"{out}/fix588.md", "w", encoding="utf-8").write("\n".join(lines) + "\n")
    json.dump(res, open(f"{out}/fix588.json", "w", ensure_ascii=False), default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
