# pumponset: слой `run` / `verdict` — интерфейс между кодом (TK-492) и счётом П-34 (TK-491) — 05.11.2026

Решение по форме (CEO-сессия 05.11): ядро (`cache`/`feat`/`episodes`/`control`) уже даёт куб, эпизоды, лаги, C2a/C2b. Недостающий слой —
**`run`** (проход по контрактам, потоковая агрегация в суточные ячейки, таблицы) и **`verdict`** (бутстрэп по суткам, Холм, отбор K, вердикты,
net). Связь между ними — только csv-файлы ниже: можно писать и проверять независимо. Чисел кроме параметров нет (`P-34-params.json`),
издержки — `crate::lob::costs`, расстояния в bps, зерно контролей — аргумент `--seed`, зерно бутстрэпа — `verdict.seed` файла параметров.

## Порядок (П-34 §5, §8)
1. `alpha pumponset run --stage fit …` — считает ТОЛЬКО минуты фита (2025); X_D из фита; пишет таблицы фита. Минуты 2026 не читаются в агрегаты.
2. `alpha pumponset verdict --stage fit …` — ранг правил, отбор K (≤ 10), пишет `k.json` и строку для `JOURNAL.md`. После этого 2026 открывается.
3. `alpha pumponset run --stage test --rules k.json …` — только замороженные K правил, минуты теста; X_D те же (из фита).
4. `alpha pumponset verdict --stage test --k k.json …` — вердикты подтв./оспорено/не различимо, net, «встать рано можно/не доказано», `report-tables.md`.

## Схемы csv (запятая, заголовок есть, числа с точкой, строки отсортированы по ключам — вывод детерминирован)
Ключ правила: `fam,wi,ti` — индексы `Rule` (`feat::rules`), `sign` = +1 памп / −1 дамп (знак `r_w` окна правила), `hi` — индекс в `label_forward.H_min`,
`di` — индекс в `episodes.D_min`, `day` — сутки UTC от 1970-01-01 (по `t_f`), `month` = yyyymm.

**`daycells-<stage>.csv`** — одна строка на (правило, sign, hi, day) с `n > 0 или nret > 0`:
`fam,wi,ti,sign,hi,day,month,n,hit,bn,bhit,nret,gross_bps,fund_bps`
- `n` — первые срабатывания с меткой Hit/Miss (NoData не считается), `hit` — из них Hit (`control::Label`); `bn`/`bhit` — то же по моментам C2a (пулом);
- `nret` — срабатывания, у которых определены `c[t_f+1]` и `c[t_f+H]`; `gross_bps` = Σ `sign·ln(c[t_f+H]/c[t_f+1])·1e4`;
- `fund_bps` = Σ фандинга, уплаченного позицией стороны `sign` за удержание (вход c[t_f+1], выход c[t_f+H]) по реальным ставкам файла (`Contract::funding`);
  положительное = издержка (лонг платит при rate > 0, шорт получает), в bps номинала.
- издержки входа/выхода НЕ вычитаются в таблице (они константы `lob::costs`) — их вычитает `verdict`.

**`lags-<stage>.csv`** — лаг-таблица §4 и полнота §7.3 (эпизоды и нулевая линия C2b):
`kind,stratum,fam,wi,ti,sign,di,month,n_ep,n_fired,n_lt0,n_le0,n_le30,n_le60,p10,p50,p90`
- `kind` = `ep` (эпизоды) | `c2b` (псевдо-моменты, `L_post'` = медиана длины фазы эпизодов страты); `month` — месяц `t0` (или `t'`);
- `stratum` = `all` (эпизоды без FLAG_WICK и без FLAG_OUTLIER) | `market` | `nonmarket` (разбиение `all` по FLAG_MARKET) | `outlier` (FLAG_OUTLIER, без FLAG_WICK) | `wick`;
- `n_ep` — эпизодов; `n_fired` — с лагом; `n_lt0` — лаг < 0; `n_le0/30/60` — лаг ≤ δ (recall при δ = 0/30/60); `p10/p50/p90` — квантиль лага среди сработавших
  (точный, по отсортированным значениям, метод нижнего ранга `floor(q·(n−1))`); пусто, если `n_fired = 0`.

**`first-<stage>.csv`** — «какой признак первым»: для каждого эпизода и пары (wi, ti) — одиночное семейство (fam 0–8) с наименьшим лагом (ничья — меньший fam):
`kind,stratum,wi,ti,sign,di,fam,n_ep,n_first` (`n_ep` — эпизодов, у которых хоть одно из 9 семейств сработало, `n_first` — где первым был `fam`).

**`progress-<stage>.csv`** — «сколько хода уже прошло / осталось» (§7.1–7.2), по эпизодам и правилам с лагом:
`stratum,fam,wi,ti,sign,di,month,n_fired,done_bps_p10,done_bps_p50,done_bps_p90,left_share_p10,left_share_p50,left_share_p90,to_peak_bps_p10,to_peak_bps_p50,to_peak_bps_p90`
- `done_bps` = `sign·ln(c[t_f+1]/c[t0])·1e4`; `left_share` = `ln(c[t_end]/c[t_f+1])·sign / (ln(c[t_end]/c[t0])·sign)` (доля хода, оставшаяся после входа; может быть < 0 и > 1 — не обрезать);
  `to_peak_bps` = `sign·ln(c[t_end]/c[t_f+1])·1e4` (ход до пика с момента входа; это ВЕРХНЯЯ граница, пик известен задним числом — в вердикт не входит).

**`episodes-<stage>.csv`** — все эпизоды: `sym,di,sign,t0,t_end,mv,flags,x_d` (t0/t_end — минуты, `mv` — ln-ход) — для проверки по v11 и ответов «где был памп».
**`xd.csv`** — `di,sign,x_d,n_windows` (порог из фита).
**`run-meta.json`** — `stage, params_sha256, seed, oi_lag_slots, contracts, symbol_days, episodes, triggers, secs_total, secs_by_phase, peak_rss_mb, git_head`.

## `verdict` (читает только csv + `P-34-params.json`)
- Ранг фита (§5, Р-3): для каждого правила — нижняя граница 95 % lift точности при H = 360 (бутстрэп по суткам, B = `verdict.bootstrap_B`, seed = `verdict.seed`), среднее по двум знакам;
  в семействе — лучшее правило (ничья: меньший θ, меньший w), из 11 семейств — 10 лучших → `k.json` (`[{fam,wi,ti,rank_lo,...}]`) и строка для JOURNAL. Правило замораживается на оба знака.
- Тест: на каждое (правило ∈ K, sign, H ∈ `label_forward.H_min`) — precision, база C2a, lift, интервал, p (односторонний, доля реплик lift ≤ 1), Холм по m = `trials.test_lift_m` (60);
  доля месяцев теста с lift > 1; вердикт подтв./оспорено/не различимо по §5. Net (H = 360 основной, 90 и 1440 описательно): `gross − 2·leg_fee_bps(true) − fund`, второй вариант `ROUNDTRIP_FEES_BPS`,
  нижняя граница 95 % (бутстрэп по суткам), Холм по m = `trials.test_net_m` (20), безубыточное проскальзывание = средний net при нулевом проскальзывании (в bps, число не назначается).
  Итог «встать рано можно»/«не доказано» — правило §7.6.
- Чувствительность: кластер «монета×сутки» — описательно (`verdict.cluster_sensitivity`). Описательно: recall из `lags` (δ = 0/30/60), доля `n_lt0`, «какой признак первым» как РАЗНОСТЬ долей `ep` − `c2b`.
