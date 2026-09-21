//! `lob bounce-grid` — вся сетка форм отскока (В-58 → В-62) по символам и суткам
//! **одним процессом** (B6 плана: S1, S2, S4; аудит 18.09: K1, K2, K4).
//!
//! Что было (`tools/b5_grid.py`): отдельный процесс `lob backtest --touches`
//! на каждую пару «форма × символ» — 48 × 100 процессов, каждый декодировал
//! запись дважды (события для `hftbacktest` и повторный реплей для касаний),
//! гонял трекер уровней и держал в памяти всю сессию (64 Б × ~17 млн событий
//! на 20 ч ≈ 1.1 ГБ). Замер `docs/findings/backtest-speed-2026-09-18.md`:
//! форма на ZEC за 20 ч — 30 с, из них трекер 14.6 с и второй декод 2.7 с
//! повторялись 48 раз ради одних и тех же касаний.
//!
//! Что здесь: касания считаются **один раз** на символ (`replay_symbol`, S1);
//! события `hftbacktest` строятся **посуточно** (S4: память — одни сутки, не
//! сессия), и по одному потоку событий гонятся **все формы** потоками
//! (`std::thread::scope`, S2): события — `&[Event]` только на чтение, у каждой
//! формы свой `Backtest` над **тем же буфером** (`with_backtest_over`: крейт
//! читает срез напрямую, без копии на форму — с копиями восемь потоков × сутки
//! ZEC × 64 Б роняли процесс нехваткой памяти, 2026-09-18). Модель исполнения
//! та же, что у `lob backtest --touches` (`build_backtest`, `drive_bounce`):
//! менять её ради скорости запрещено.
//!
//! Fail-closed (K1): символ без маркера `verify-<SYMBOL>.status == ok` в
//! корне **пропускается** с строкой stderr и счётчиком в сводке; снять
//! требование можно только явным `--allow-unverified` (отладочные данные,
//! такой прогон в `runs.csv` не идёт).
//!
//! Артефакты (`--out-dir`, обычно `data/b5/<метка>/`):
//! - `rounds.csv` — каждый круг сделки с `symbol`, `day_utc`, `form` (K2:
//!   интервал по суткам и стратификация по инструменту считаются из этого) и
//!   фактическим исполнением круга (F4, В-78): `fill_frac` (доля исполненного
//!   от заказанного), `entry_vwap` (средняя цена исполненного входа),
//!   `legs_filled`, `legs_rejected` (ног, которых биржа не поставила, В-72);
//! - `forms.csv` — строка на (символ, сутки, форма): сигналы, круги, промахи,
//!   причины выхода, сумма `net_bps`, счётчик ног, не поставленных биржей
//!   (`n_rejected_postonly`);
//! - `manifest.txt` — аргументы прогона.
//!
//! Вход сделки-отскока — **пост-онли по умолчанию** (В-72: тейкерский вход в
//! момент касания — не наша сделка; заявка, пересёкшая спред, биржей
//! отклоняется и считается не поставленной). `--no-post-only` возвращает
//! обычный лимит `GTC` — это режим гейта «те же круги»: прежние
//! `rounds.csv`/`forms.csv` сняты до F4.
//!
//! Сетка (В-62) — `--stop-sigma × --take-sigma × DEADLINE_SECS` в этом порядке:
//! стоп и тейк — множители `σ_H` (реализованная волатильность середины за
//! окно дедлайна, `lob::sigma`), полы — тик за плотностью и `--take-floor-fees`
//! круговых комиссий (`backtest::bounce_plan`). Множители — числа владельца,
//! умолчаний нет; имя формы `s<a>-t<b>-<H>` то же, что читает вердикт
//! (`bounce_verdict::form_label`/`parse_form`). Досрочный выход В-58 п. 5 в
//! сетку не входит (измерен сеткой в тиках; вернуть — отдельной
//! предрегистрацией). Касание без `σ` (окно упирается в начало записи)
//! сигнала у формы не даёт и считается в `n_skipped`. Ось стороны
//! (`--side bid|ask`, этап 1 дороги к альфе, `side-asymmetry-2026-09-19.md`):
//! касания другой стороны выбывают до форм и считаются там же.
//!
//! Касания — из реплея книги (как было) или из кэша `--touches-from <dir>`:
//! CSV ночного H3 (`study/touches/<сутки>/touches-<SYMBOL>.csv`,
//! `nightly-grid.sh`). Реплей — 83 % времени сетки (замер 19.09: 29 монет ×
//! 3 суток — 1176 с из 1419), трекер уровней в обоих суточный, так что
//! касания те же побайтово (`touches::read_touches_csv`, тест обратимости;
//! гейт «те же `rounds.csv`/`forms.csv`» на монетах — `docs/COMMANDS.md`).
//! σ-формы (`s<a>`/`t<b>`) из кэша не считаются: `σ` в CSV — суточный ряд с
//! шестью знаками, а не ряд всей записи.
//!
//! Несколько семей флоров одним процессом — `--set <имя>:<фильтры>` (20.09):
//! события суток декодируются и окна `SignalWindows` строятся **один раз**,
//! дальше формы гонятся для каждого набора фильтров касаний (возраст, сила,
//! сторона, фронтран, съедание стены `eaten=`, ход до касания и режим
//! `ret1h_min=…`/`pool4h_max=…`/`btc4h_min=…` — S4 плана по сторонам, режим
//! из `--regime-from study/regime`) над теми же событиями; артефакты — `<out-dir>/<имя>/`
//! (тот же `rounds.csv`/`forms.csv`/`manifest.txt`, вердикт читает
//! подкаталог). Без `--set` — один набор из флагов командной строки в
//! `<out-dir>`, байт в байт как раньше; с `--set` флаги фильтров у команды
//! запрещены (набор — единственный источник). Ночь из девяти сеток базы
//! стоила девять декодов тех же суток на монету.
//!
//! Модель очереди и исполнения — `--queue-model risk-adverse|prob:<n>` (F3
//! этапа F, 20.09): **обязательный флаг, умолчания в коде нет**; `risk-adverse`
//! — прежний движок (числа прежних прогонов не меняются, гейт «те же круги»),
//! `prob:<n>` — модель очереди по объёму с частичным исполнением (`n` — число
//! предрегистрации, у крейта в примерах 3.0 — не наше умолчание). Три пути
//! исполнения крейта и их оптимизм — в doc-комментарии
//! `lob::backtest::build_backtest` и в шапке `forms.csv`/`manifest.txt`
//! (`queue=…`, `paths=…`); счётчик кругов по пути (3) — колонка
//! `n_fill_by_cross` в `forms.csv` (крейт пути не отдаёт: детектор
//! `lob::backtest` по буферу последних сделок, отсрочка вердикта на шаг —
//! локальная метка сделки отстаёт от биржевой).
//!
//! Форма входа и сигнал — F6 этапа F (В-73): `--signal touch|approach`
//! (умолчание `touch` — гейт «те же круги») выбирает, от чего строится план
//! (`bounce_plan`/`approach_plan`): от касания или от записи подхода F1
//! (`approaches-<SYMBOL>.csv` того же кэша `--touches-from`; `t0` — `arm_ms`,
//! снимок книги на взводе). `--entry-form` (повторяемый — ось сетки) —
//! `single@fr` (умолчание: прежняя одиночная нога у фронтранера; имя формы
//! остаётся трёхпольным) или `ladder<N>x<from>..<to>[w<k>]`: `N` ног целыми
//! тиками от `from` до `to` bps **над стеной** (для аска — под), равными
//! долями либо весом нижней ноги `k`. Числа — из имени формы
//! (предрегистрация), умолчаний нет; нога, пересёкшая лучший аск, биржей не
//! ставится (пост-онли `GTX`, `n_rejected_postonly`, F4). Имя формы —
//! `<вход>-<стоп>-<тейк>-<H>` с хвостовыми `-ttl<режим>` (F5) и `-<выход>`
//! (F7), читает `bounce_verdict::parse_form_fields`.

use std::collections::BTreeMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::Instant;

use clap::Args;
use hftbacktest::types::Event as HbtEvent;

use super::backtest::{
    approach_plan, bounce_plan, count_feed_events, deadline_ns_from_secs, early_exit_ns_from_secs,
    exit_reason_label, feed_events_into, open_replay_feed, pool_order_qty, pool_order_qty_usd,
    read_tick_step, BounceForm, EntryForm, EntryTtl, PlanShape, StopForm, TakeForm,
};
use super::bounce_verdict::{form_label_with_entry, DEADLINE_SECS, DEADLINE_SECS_ALLOWED};
use super::profiles::read_verify_marker;
use super::{
    replay_symbol_touches_and_second_mids, resolve_h3_mode_full, session_parts_for, H3Args,
    DEFAULT_REPEAT_WINDOW_MS, DEFAULT_WARMUP_MS,
};
use crate::book::Side;
use crate::lob::backtest::{
    drive_bounce, drive_bounce_windowed, roundtrip_net_bps, with_backtest_over, BounceRun,
    BounceSignal, DriveConfig, ExecLatency, QueueModelKind, SignalWindows,
};
use crate::lob::levels::{H3Mode, LevelsConfig, TouchRecord};
use crate::lob::sigma::SigmaSeries;

/// Доля съедания стены к касанию, %: `100 × (1 − size_at_touch /
/// size_max_before)`; стена без истории размера (`size_max_before ≤ 0`) —
/// ноль, чтобы ключ `eaten=` её не выбивал (нечего было съесть).
#[allow(clippy::cast_precision_loss)]
fn eaten_pct(t: &TouchRecord) -> f64 {
    if t.size_max_before <= 0 {
        return 0.0;
    }
    100.0 * (1.0 - t.size_at_touch as f64 / t.size_max_before as f64)
}

/// Касания одних суток символа — из реплея или из кэша, форме всё равно.
pub(crate) struct DayTouches {
    pub(crate) day: String,
    pub(crate) touches: Vec<TouchRecord>,
    /// Записи подхода (F6, `--signal approach`) — те же сутки и тот же порядок,
    /// что `touches` (это их вид как касания, собранный
    /// `backtest::touch_view_of_approach`); `None` — сигнал по касаниям.
    pub(crate) approaches: Option<Vec<crate::lob::levels::ApproachRecord>>,
    /// Ход до касания за `PRE_TOUCH_MS` на каждое касание (S2), для контекста наборов.
    pub(crate) rets: Vec<[Option<f64>; 3]>,
}

/// Касания символа из кэша `--touches-from` для суток `days` (сутки корня с
/// частями): на сутки — `<dir>/<сутки>/touches-<SYMBOL>.csv`, иначе общий
/// `<dir>/touches-<SYMBOL>.csv`, из которого берутся строки этих суток.
/// Нет файла на какие-то сутки или в суточном файле чужие сутки — `Err`
/// (вызывающий идёт реплеем и пишет причину). Порядок строк — порядок файла,
/// он же порядок выдачи трекера.
pub(crate) fn cached_touches<'a>(
    dir: &Path,
    symbol: &str,
    days: impl Iterator<Item = &'a String>,
    need_ret: bool,
) -> anyhow::Result<Vec<DayTouches>> {
    let flat = dir.join(format!("touches-{symbol}.csv"));
    let mut flat_rows: Option<Vec<super::touches::TouchRow>> = None;
    let mut out = Vec::new();
    for day in days {
        let per_day = dir.join(day).join(format!("touches-{symbol}.csv"));
        let rows: Vec<super::touches::TouchRow> = if per_day.is_file() {
            let rows = super::touches::read_touches_csv(&per_day)?;
            if let Some(bad) = rows.iter().find(|r| r.day != *day) {
                anyhow::bail!(
                    "{}: строка суток {} в файле суток {day}",
                    per_day.display(),
                    bad.day
                );
            }
            rows
        } else if flat.is_file() {
            if flat_rows.is_none() {
                flat_rows = Some(super::touches::read_touches_csv(&flat)?);
            }
            flat_rows
                .as_ref()
                .expect("только что прочитан")
                .iter()
                .filter(|r| r.day == *day)
                .cloned()
                .collect()
        } else {
            anyhow::bail!("нет {} и нет {}", per_day.display(), flat.display());
        };
        if need_ret {
            if let Some(bad) = rows.iter().find(|r| r.ret_bps.is_none()) {
                anyhow::bail!(
                    "{}: кэш без колонок ret_* (касание {} {}), нужен пересчёт касаний",
                    per_day.display(),
                    bad.day,
                    bad.touch.start_ms
                );
            }
        }
        let rets = rows
            .iter()
            .map(|r| r.ret_bps.unwrap_or([None; 3]))
            .collect();
        out.push(DayTouches {
            day: day.clone(),
            touches: rows.into_iter().map(|r| r.touch).collect(),
            approaches: None,
            rets,
        });
    }
    Ok(out)
}

/// Записи подхода символа из кэша (F1, F6): `<dir>/<сутки>/approaches-<SYMBOL>.csv`,
/// иначе общий `<dir>/approaches-<SYMBOL>.csv`, из которого берутся строки этих
/// суток. Рядом с записями — их **вид как касания**
/// (`backtest::touch_view_of_approach`): фильтры (`TouchFilter`), порог В-66 и
/// окна `SignalWindows` читают касание, а `arm_ms` становится `start_ms` —
/// снимок книги берётся на взводе, как у касания на касании. Нет файла на
/// какие-то сутки или в суточном файле чужие сутки — `Err` (реплея подходов у
/// сетки нет: полосу `D` задаёт прогон `lob touches --approach-bps`).
pub(crate) fn cached_approaches<'a>(
    dir: &Path,
    symbol: &str,
    days: impl Iterator<Item = &'a String>,
) -> anyhow::Result<Vec<DayTouches>> {
    let flat = dir.join(format!("approaches-{symbol}.csv"));
    let mut flat_rows: Option<Vec<super::touches::ApproachRow>> = None;
    let mut out = Vec::new();
    for day in days {
        let per_day = dir.join(day).join(format!("approaches-{symbol}.csv"));
        let rows: Vec<super::touches::ApproachRow> = if per_day.is_file() {
            let rows = super::touches::read_approaches_csv(&per_day)?;
            if let Some(bad) = rows.iter().find(|r| r.day != *day) {
                anyhow::bail!(
                    "{}: строка суток {} в файле суток {day}",
                    per_day.display(),
                    bad.day
                );
            }
            rows
        } else if flat.is_file() {
            if flat_rows.is_none() {
                flat_rows = Some(super::touches::read_approaches_csv(&flat)?);
            }
            flat_rows
                .as_ref()
                .expect("только что прочитан")
                .iter()
                .filter(|r| r.day == *day)
                .cloned()
                .collect()
        } else {
            anyhow::bail!("нет {} и нет {}", per_day.display(), flat.display());
        };
        let approaches: Vec<crate::lob::levels::ApproachRecord> =
            rows.into_iter().map(|r| r.approach).collect();
        let n = approaches.len();
        out.push(DayTouches {
            day: day.clone(),
            touches: approaches
                .iter()
                .map(super::backtest::touch_view_of_approach)
                .collect(),
            approaches: Some(approaches),
            // Кэш подходов хода до взвода не несёт (как у F2): контекстные
            // ключи наборов с `--signal approach` отвергает `run_bounce_grid`,
            // но длины рядов обязаны сходиться (`touch_contexts`).
            rets: vec![[None; 3]; n],
        });
    }
    Ok(out)
}

/// Одна форма сетки (В-65): имя колонки, форма сделки, дедлайн (он же окно `σ`)
/// и режим срока жизни входа (F5, В-74).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct GridForm {
    pub label: &'static str,
    pub form: BounceForm,
    pub deadline_secs: i64,
    /// Режим срока жизни входа (F5, В-74): `touch` — прежний, секунды/`wall` —
    /// условия рынка плюс потолок. Имя формы у нетронутого режима не меняется
    /// (`form_label`), у остальных несёт суффикс `-ttl<значение>` — имена
    /// обязаны различаться, иначе `run_bounce_grid` отказывает на повторе.
    pub entry_ttl: EntryTtl,
    /// Форма входа (F6, В-73): `single@fr` — прежнее имя формы без поля входа
    /// (гейт «те же круги»), лестница — поле `<вход>` первым в имени.
    pub entry_form: EntryForm,
}

/// Формы сетки в порядке `stops × takes × DEADLINE_SECS`; имена —
/// `bounce_verdict::form_label`, их же читает вердикт. Повторы имён отвергает
/// `run_bounce_grid`. Это — прежний режим входа (F5, В-74): `touch`.
pub fn grid_forms(
    stops: &[StopForm],
    takes: &[TakeForm],
    take_floor_fees: Option<f64>,
    deadlines: &[u64],
) -> Vec<GridForm> {
    grid_forms_with_entry_ttl(stops, takes, take_floor_fees, deadlines, &[EntryTtl::Touch])
}

/// Та же сетка, но с осью срока жизни входа (F5, В-74): значение `entry_ttl`
/// — **самый внутренний** множитель, поэтому при `&[EntryTtl::Touch]` порядок
/// и имена форм те же, что у `grid_forms` (гейт «те же круги»).
pub fn grid_forms_with_entry_ttl(
    stops: &[StopForm],
    takes: &[TakeForm],
    take_floor_fees: Option<f64>,
    deadlines: &[u64],
    ttls: &[EntryTtl],
) -> Vec<GridForm> {
    grid_forms_with_axes(
        stops,
        takes,
        take_floor_fees,
        deadlines,
        ttls,
        &[EntryForm::SingleFrontrun],
    )
}

/// Полная сетка F6 (В-73): к осям F5 добавлена ось **формы входа**
/// (`--entry-form`, повторяемый). Форма входа — внешний множитель, поэтому
/// при `&[EntryForm::SingleFrontrun]` порядок и имена форм те же, что у
/// `grid_forms_with_entry_ttl` (гейт «те же круги»): у `single@fr` поле входа в
/// имени не пишется (`form_label_with_entry`).
pub fn grid_forms_with_axes(
    stops: &[StopForm],
    takes: &[TakeForm],
    take_floor_fees: Option<f64>,
    deadlines: &[u64],
    ttls: &[EntryTtl],
    entries: &[EntryForm],
) -> Vec<GridForm> {
    let mut out = Vec::with_capacity(
        entries.len() * stops.len() * takes.len() * deadlines.len() * ttls.len(),
    );
    for &entry_form in entries {
        for &stop in stops {
            for &take in takes {
                for &deadline in deadlines {
                    let base = form_label_with_entry(
                        &entry_form.label(),
                        &stop.label(),
                        &take.label(),
                        deadline,
                    );
                    for &entry_ttl in ttls {
                        let label: &'static str = match entry_ttl {
                            // Прежний режим — прежнее имя: вердикт и «золото» F3/F4
                            // читают `form_label` как есть.
                            EntryTtl::Touch => Box::leak(base.clone().into_boxed_str()),
                            other => {
                                Box::leak(format!("{base}-ttl{}", other.label()).into_boxed_str())
                            }
                        };
                        out.push(GridForm {
                            label,
                            form: BounceForm {
                                stop,
                                take,
                                take_floor_fees,
                            },
                            deadline_secs: deadline as i64,
                            entry_ttl,
                            entry_form,
                        });
                    }
                }
            }
        }
    }
    out
}

/// Разбор повторяемого `--entry-ttl-secs` (F5, В-74): пусто — прежний режим
/// `touch`; значения — `touch` | `wall` | секунды из сетки замера В-74.
/// Порядок детерминирован (как у дедлайнов), повторы свёрнуты — иначе формы
/// сетки получили бы одинаковые имена и `run_bounce_grid` отказал бы.
pub(crate) fn parse_entry_ttls(specs: &[String]) -> anyhow::Result<Vec<EntryTtl>> {
    if specs.is_empty() {
        return Ok(vec![EntryTtl::Touch]);
    }
    let mut out = Vec::with_capacity(specs.len());
    for spec in specs {
        out.push(EntryTtl::parse(spec)?);
    }
    out.sort_by_key(|t| t.sort_key());
    out.dedup();
    Ok(out)
}

/// Разбор повторяемого `--entry-form` (F6, В-73): пусто — прежний вход
/// `single@fr` (гейт «те же круги»); иначе `single@fr` и/или
/// `ladder<N>x<from>..<to>[w<k>]`. Порядок — порядок флагов (он же порядок
/// осей сетки), повторы свёрнуты: иначе формы получили бы одинаковые имена и
/// `run_bounce_grid` отказал бы. Числа форм — из имён (предрегистрация),
/// умолчаний нет; пустой список — единственное исключение и оно прежнее
/// поведение команды.
pub(crate) fn parse_entry_forms(specs: &[String]) -> anyhow::Result<Vec<EntryForm>> {
    if specs.is_empty() {
        return Ok(vec![EntryForm::SingleFrontrun]);
    }
    let mut out: Vec<EntryForm> = Vec::with_capacity(specs.len());
    for spec in specs {
        let form = EntryForm::parse(spec)?;
        if !out.contains(&form) {
            out.push(form);
        }
    }
    Ok(out)
}

/// Обязательные спутники условий F5 (В-74): «стена снята» без порога В-66
/// (`--h3-usd`) не проверить, а «цена ушла из полосы» без числа замера
/// (`--band-exit-bps`) — изобретённое число. Отказ, не молчаливый пропуск.
fn ensure_entry_conditions_args(
    active: bool,
    h3_usd: Option<f64>,
    band_exit_bps: Option<f64>,
) -> anyhow::Result<()> {
    if !active {
        return Ok(());
    }
    anyhow::ensure!(
        h3_usd.is_some(),
        "условие «стена снята» (F5, В-74) требует --h3-usd: порог В-66 в лотах крейта — --h3-usd / цена"
    );
    anyhow::ensure!(
        band_exit_bps.is_some(),
        "условие «цена ушла из полосы» (F5, В-74) требует --band-exit-bps: полоса — число замера, умолчания нет"
    );
    Ok(())
}

#[derive(Debug, Args)]
pub struct BounceGridArgs {
    /// Корень записи (`<SYMBOL>-<день>[-pN].binlog`, `instruments.csv`,
    /// маркеры `verify-<SYMBOL>.status`).
    #[arg(long)]
    pub root: PathBuf,
    /// Символы (повторяемый флаг); пусто — весь пул `instruments.csv` корня.
    #[arg(long = "symbol")]
    pub symbols: Vec<String>,
    /// Сутки UTC `YYYY-MM-DD` (повторяемый флаг); пусто — все сутки записи.
    #[arg(long = "day")]
    pub days: Vec<String>,
    /// Задержка исполнения, нс: одно число (В-37, `assumed`) или тройка
    /// `place=..,cancel=..,taker=..` (В-68, измерено `lob latency`); обязателен.
    #[arg(long)]
    pub median_rtt_ns: ExecLatency,
    #[arg(long)]
    pub p95_rtt_ns: ExecLatency,
    /// Модель очереди и исполнения (F3 плана 2026-09-20, В-78):
    /// `risk-adverse` — прежний движок (`RiskAdverseQueueModel` +
    /// `NoPartialFillExchange`, числа прогонов не меняются) или `prob:<n>` —
    /// модель очереди по объёму (`ProbQueueModel<PowerProbQueueFunc(n)>` +
    /// `PartialFillExchange`, вход исполняется частично). **Обязательный
    /// флаг, умолчания в коде нет**: `n` — число предрегистрации, у крейта в
    /// примерах 3.0 — это не наше умолчание. Модель идёт в шапку
    /// `forms.csv`/`manifest.txt` (`queue=…`).
    #[arg(long = "queue-model")]
    pub queue_model: QueueModelKind,
    /// Лот в e9 — либо он, либо `--order-qty-from-pool`.
    #[arg(long)]
    pub order_qty_e9: Option<i64>,
    /// Лот — `order_size_22a` от полей пула и цены последнего касания.
    #[arg(long, default_value_t = false)]
    pub order_qty_from_pool: bool,
    /// Лот под номинал в долларах (владелец 20.09): `floor(usd / цена касания /
    /// шаг) × шаг`, не меньше лота 22а — модель очереди исполняет реальный
    /// размер; взаимоисключающе с `--order-qty-e9` и `--order-qty-from-pool`.
    #[arg(long)]
    pub order_usd: Option<f64>,
    /// Множитель лота (E7, дробный выход): позиция в `N` лотов пула, чтобы
    /// половину было чем выходить — с одним минимальным лотом половина
    /// округляется в ноль и формы `half1to1`/`eat<h>x<a>` выходят целиком.
    /// `1` — как было.
    #[arg(long, default_value_t = 1)]
    pub order_qty_mult: u32,
    /// Вход пост-онли (`GTX`) — решение владельца 20.09 (В-72): тейкерский
    /// вход в момент касания не наша сделка, поэтому **умолчание —
    /// включён**, и явный флаг нужен только для полноты записи в
    /// `manifest.txt`. Выключить можно лишь `--no-post-only` — это режим гейта
    /// «те же круги»: прежние `rounds.csv`/`forms.csv` сняты до F4, когда
    /// вход был обычным лимитом (`GTC`).
    #[arg(long, overrides_with = "no_post_only", default_value_t = false)]
    pub post_only: bool,
    /// Выключить пост-онли входа (режим гейта «те же круги», В-72) — вход
    /// обычным лимитом `GTC`, тейкером там, где пересекает спред. Флаги
    /// взаимоисключающие: применённый последним выигрывает.
    #[arg(
        long = "no-post-only",
        overrides_with = "post_only",
        default_value_t = false
    )]
    pub no_post_only: bool,
    /// Формы стопа базы (повторяемый флаг; В-65, умолчаний нет):
    /// `before|at|behind|midfr|stack2|pct<x>|s<a>`.
    #[arg(long = "stop-form", required = true)]
    pub stop_form: Vec<String>,
    /// Формы тейка (повторяемый флаг): `1to1` и/или `t<b>`.
    #[arg(long = "take-form", required = true)]
    pub take_form: Vec<String>,
    /// Пол σ-тейка в кругах комиссий (`costs::ROUNDTRIP_FEES_BPS`); нужен
    /// только формам `t<b>`.
    #[arg(long)]
    pub take_floor_fees: Option<f64>,
    /// Режим срока жизни входа (F5, В-74) — повторяемый флаг, формы сетки —
    /// декартово произведение: `touch` — прежний «до конца касания» (условия
    /// F5 выключены, гейт «те же круги»), `wall` — только условия рынка без
    /// потолка, либо секунды из сетки замера В-74 {60, 300, 1800}. Пусто —
    /// `touch`. С условиями (кроме `touch`) обязательны `--h3-usd` (порог
    /// «стена снята») и `--band-exit-bps`.
    #[arg(long = "entry-ttl-secs")]
    pub entry_ttl_secs: Vec<String>,
    /// Полоса ухода цены, bps (F5, В-74): лучшая цена нашей стороны дальше
    /// дальней ноги лестницы больше чем на это число — вход снимается. Число
    /// замера (`2·D`, полосу `D` выбирает предрегистрация F10), **умолчания в
    /// коде нет**: с `--entry-ttl-secs` кроме `touch` флаг обязателен.
    #[arg(long = "band-exit-bps")]
    pub band_exit_bps: Option<f64>,
    /// E3 базы: входить только от фронтрана — касания без `frontrun_tick`
    /// пропускаются у всех форм («впритык — редко» [S 07:37; D 05:18]).
    #[arg(long, default_value_t = false)]
    pub frontrun_only: bool,
    /// Главный фильтр базы (владелец 19.09): плотность стоит не меньше `N` с к
    /// моменту касания («стоит ≥ 1 ч» [D 01:57; K 14:35]). Число — владельца.
    #[arg(long)]
    pub min_age_secs: Option<i64>,
    /// Сила «×поток» не ниже `S` % в момент касания: размер плотности /
    /// оборот монеты за последний час (определение владельца 19.09, как у
    /// сторонних сканеров). Без оборота за час — пропуск.
    #[arg(long)]
    pub min_flow_pct: Option<f64>,
    /// Ось стороны (этап 1 `alpha-roadmap-2026-09-19.md`, намёк
    /// `side-asymmetry-2026-09-19.md`: лонги от бид-стен +12…+17 bps на
    /// круг, шорты от аск-стен −16…−19): гнать только касания бид- (`bid`,
    /// лонг) или аск-стен (`ask`, шорт). Без флага — обе стороны, как прежде.
    #[arg(long, value_enum)]
    pub side: Option<SideArg>,
    /// Касания из CSV вместо реплея книги: `<dir>/<сутки>/touches-<SYMBOL>.csv`
    /// (раскладка ночного H3, `study/touches/`) или `<dir>/touches-<SYMBOL>.csv`
    /// (одна папка на всю запись, строки по `day_utc`). Порог плотности и
    /// трекер обязаны быть теми же, что у `lob touches` (проверить нечем —
    /// шапки у CSV нет; стандарт — `--h3-mode notional --h3-usd 10000`,
    /// умолчания прогрева/окна повтора). Символ, у которого в кэше нет всех
    /// суток корня, идёт реплеем (со счётчиком и строкой в stderr).
    /// С `--signal approach` здесь читаются `approaches-<SYMBOL>.csv` (F1).
    #[arg(long)]
    pub touches_from: Option<PathBuf>,
    /// Сигнал входа (F6 этапа F, В-73): `touch` — касание (умолчание — гейт
    /// «те же круги»), `approach` — запись подхода F1: лимитка ставится на
    /// взводе (`t0 = arm_ms`) и живёт до касания/снятия/потолка, а не с
    /// касания. `approach` требует кэша `--touches-from` с
    /// `approaches-<SYMBOL>.csv`: реплей подходов сетка не считает, полосу `D`
    /// задаёт прогон `lob touches --approach-bps`.
    #[arg(long, value_enum, default_value_t = SignalArg::Touch)]
    pub signal: SignalArg,
    /// Форма входа (F6 этапа F, В-73) — повторяемый флаг, ось сетки:
    /// `single@fr` (умолчание — прежний вход у фронтранера, гейт «те же
    /// круги») или `ladder<N>x<from>..<to>[w<k>]` — `N` ног от `from` до `to`
    /// bps над стеной (для аска — под), равными долями либо весом нижней ноги
    /// `k`. Числа — из имени формы (предрегистрация), умолчаний нет.
    #[arg(long = "entry-form")]
    pub entry_form: Vec<String>,
    /// Набор фильтров касаний одним процессом (повторяемый): `<имя>:<k=v,…>`,
    /// ключи `age=<с>` (возраст ≥, как `--min-age-secs`), `flow=<%>` (сила
    /// ×поток ≥, как `--min-flow-pct`), `side=bid|ask`, `frontrun` (только
    /// от фронтрана); пустой список ключей — без фильтров (`name:`). Артефакты
    /// набора — `<out-dir>/<имя>/`; имя — буквы, цифры, `.`, `_`, `-`. С
    /// `--set` флаги `--min-age-secs/--min-flow-pct/--side/--frontrun-only`
    /// у команды — отказ.
    #[arg(long = "set")]
    pub sets: Vec<String>,
    /// Режим по минутам для ключей `pool*`/`btc*` наборов (S3/S4 плана по
    /// сторонам): каталог `study/regime` с `<сутки>.csv` от `regime.py`.
    /// Без него эти ключи — отказ; сутки без файла — отказ.
    #[arg(long)]
    pub regime_from: Option<PathBuf>,
    /// Дедлайны сетки, секунды (повторяемый; умолчание — В-58 `60 600 3600
    /// 7200`); разрешены ещё `1800` и `14400` (S8, предрегистрация 20.09) —
    /// иное число отказ.
    #[arg(long = "deadline-secs")]
    pub deadline_secs: Vec<u64>,
    #[command(flatten)]
    pub h3: H3Args,
    #[arg(long)]
    pub h3_k: Option<f64>,
    #[arg(long)]
    pub warmup_ms: Option<i64>,
    #[arg(long)]
    pub repeat_window_ms: Option<i64>,
    /// Потоков на сетку (умолчание — число ядер).
    #[arg(long)]
    pub threads: Option<usize>,
    /// Драйвер: `setups` — биржа только от касания до конца круга (умолчание,
    /// решение владельца 2026-09-18); `full` — сплошной прогон суток на форму,
    /// эталон для гейта «побайтово те же круги».
    #[arg(long, value_enum, default_value_t = DriverArg::Setups)]
    pub driver: DriverArg,
    /// Каталог артефактов (`rounds.csv`, `forms.csv`, `manifest.txt`).
    #[arg(long)]
    pub out_dir: PathBuf,
    /// Снять требование маркера сверки (отладочные данные; в `runs.csv` не идёт).
    #[arg(long, default_value_t = false)]
    pub allow_unverified: bool,
}

impl BounceGridArgs {
    /// Пост-онли входа у планов прогона (В-72): умолчание — **включён**,
    /// `--no-post-only` — режим гейта «те же круги». Разрешение флагов здесь,
    /// а не в `run_bounce_grid`: `clap` уже свёл пару `--post-only` /
    /// `--no-post-only` к «последний выигрывает».
    pub fn entry_post_only(&self) -> bool {
        !self.no_post_only
    }
}

/// Сторона стены для `--side`: `bid` — лонг от бид-стены, `ask` — шорт от аск-стены.
#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum SideArg {
    Bid,
    Ask,
}

impl SideArg {
    pub fn label(self) -> &'static str {
        match self {
            SideArg::Bid => "bid",
            SideArg::Ask => "ask",
        }
    }
}

impl From<SideArg> for Side {
    fn from(s: SideArg) -> Self {
        match s {
            SideArg::Bid => Side::Bid,
            SideArg::Ask => Side::Ask,
        }
    }
}

/// Как гонять форму над сутками: сплошным прогоном или по сетапам.
#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum DriverArg {
    Full,
    Setups,
}

/// По какому сигналу входит сделка (F6 этапа F, В-73): касание (прежнее,
/// гейт) или запись подхода F1 (лимитка ставится заранее, на взводе).
#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum SignalArg {
    Touch,
    Approach,
}

impl SignalArg {
    pub fn label(self) -> &'static str {
        match self {
            Self::Touch => "touch",
            Self::Approach => "approach",
        }
    }
}

impl DriverArg {
    pub fn label(self) -> &'static str {
        match self {
            DriverArg::Full => "full",
            DriverArg::Setups => "setups",
        }
    }
}

/// Итог прогона — то, что печатает `lob bounce-grid`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct BounceGridSummary {
    pub forms: usize,
    pub symbols_done: usize,
    pub symbols_skipped_unverified: usize,
    pub symbols_without_touches: usize,
    /// Символов, чьи касания пришли из кэша `--touches-from` (остальные — реплей).
    pub symbols_from_cache: usize,
    pub symbol_days: usize,
    pub rounds: u64,
    /// Пути первого (или единственного) набора — как раньше.
    pub rounds_path: PathBuf,
    pub forms_path: PathBuf,
    /// Пути каждого набора `--set` (имя, `rounds.csv`, `forms.csv`); без
    /// `--set` — один элемент с пустым именем.
    pub sets: Vec<SetPaths>,
}

/// Артефакты одного набора фильтров.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SetPaths {
    pub name: String,
    pub rounds_path: PathBuf,
    pub forms_path: PathBuf,
}

/// Набор фильтров касаний одной сетки (`--set`, либо флаги команды).
#[derive(Debug, Clone, PartialEq)]
pub struct FilterSet {
    /// Имя подкаталога; пустое — артефакты в `<out-dir>` (без `--set`).
    pub name: String,
    pub frontrun_only: bool,
    pub min_age_secs: Option<i64>,
    pub min_flow_pct: Option<f64>,
    pub side: Option<SideArg>,
    /// Состояние стены при касании (S1 плана по сторонам, [D 33:57] «не
    /// заходить в разъедания»): доля съедания к касанию `100 × (1 −
    /// size_at_touch / size_max_before)` не больше этого процента. Только
    /// ключ набора `eaten=<%>`, флага у команды нет.
    pub eaten_max_pct: Option<f64>,
    /// Номинал стены при касании не меньше стольких долларов (`usd_min=`):
    /// ось «размер стены» поверх пола `--h3-usd` (стены ≥ пола — надмножество,
    /// так что ключ — подмножество тех же касаний).
    pub usd_min: Option<f64>,
    /// Контекст касания (S4): границы в bps по осям `CTX_AXES` — ход монеты
    /// до касания за 10 мин / 1 ч / 4 ч (`ret10m`, `ret1h`, `ret4h`; знак
    /// абсолютный), медиана пула за 1 ч / 4 ч (`pool1h`, `pool4h`) и биток
    /// (`btc1h`, `btc4h`); ключи `<ось>_min=` / `<ось>_max=`. Касание без
    /// значения оси при заданной границе выбывает.
    pub ctx: [Range; CTX_AXES.len()],
}

/// Оси контекста касания — порядок общий для `FilterSet::ctx` и `TouchContext`.
pub const CTX_AXES: [&str; 7] = [
    "ret10m", "ret1h", "ret4h", "pool1h", "pool4h", "btc1h", "btc4h",
];

/// Границы одной оси контекста, bps; `None` — не задана.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct Range {
    pub min: Option<f64>,
    pub max: Option<f64>,
}

impl Range {
    pub(crate) fn is_set(self) -> bool {
        self.min.is_some() || self.max.is_some()
    }

    fn holds(self, v: Option<f64>) -> bool {
        if !self.is_set() {
            return true;
        }
        match v {
            None => false,
            Some(x) => self.min.is_none_or(|m| x >= m) && self.max.is_none_or(|m| x <= m),
        }
    }
}

/// Контекст одного касания в порядке `CTX_AXES`; `None` — значения нет
/// (окно за началом записи, нет режима на минуту).
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct TouchContext {
    pub axes: [Option<f64>; CTX_AXES.len()],
}

/// Режим суток по минутам из `regime.py`: `minute_ms → (pool1h, pool4h, btc1h, btc4h)`.
pub(crate) type RegimeDay = BTreeMap<i64, [Option<f64>; 4]>;

pub(crate) fn read_regime_day(dir: &Path, day: &str) -> anyhow::Result<RegimeDay> {
    let path = dir.join(format!("{day}.csv"));
    let mut r = csv::ReaderBuilder::new()
        .from_path(&path)
        .map_err(|e| anyhow::anyhow!("--regime-from: {}: {e}", path.display()))?;
    let header = r.headers()?.clone();
    let idx = |name: &str| -> anyhow::Result<usize> {
        header
            .iter()
            .position(|h| h == name)
            .ok_or_else(|| anyhow::anyhow!("{}: нет колонки {name}", path.display()))
    };
    let cols = [
        idx("minute_ms")?,
        idx("pool_ret_1h_bps")?,
        idx("pool_ret_4h_bps")?,
        idx("btc_ret_1h_bps")?,
        idx("btc_ret_4h_bps")?,
    ];
    let mut out = RegimeDay::new();
    for rec in r.records() {
        let rec = rec?;
        let minute: i64 = rec
            .get(cols[0])
            .ok_or_else(|| anyhow::anyhow!("{}: короткая строка", path.display()))?
            .parse()?;
        let mut vals = [None; 4];
        for (k, c) in cols[1..].iter().enumerate() {
            let v = rec.get(*c).unwrap_or("");
            vals[k] = if v.is_empty() {
                None
            } else {
                Some(v.parse::<f64>()?)
            };
        }
        out.insert(minute, vals);
    }
    Ok(out)
}

/// Контекст касаний суток: ход монеты — из кэша (`TouchRow::ret_bps`) или из
/// реплея (`pre_touch_return_bps_csv` — те же шесть знаков), режим — по
/// минуте `start_ms` из `RegimeDay` (нет режима — `None`).
pub(crate) fn touch_contexts(
    rets: &[[Option<f64>; 3]],
    touches: &[TouchRecord],
    regime: Option<&RegimeDay>,
) -> Vec<TouchContext> {
    debug_assert_eq!(rets.len(), touches.len());
    touches
        .iter()
        .zip(rets)
        .map(|(t, r)| {
            let minute = t.start_ms - t.start_ms.rem_euclid(60_000);
            let m = regime
                .and_then(|g| g.get(&minute))
                .copied()
                .unwrap_or([None; 4]);
            TouchContext {
                axes: [r[0], r[1], r[2], m[0], m[1], m[2], m[3]],
            }
        })
        .collect()
}

impl FilterSet {
    /// Хоть один ключ контекста задан.
    fn uses_ctx(&self) -> bool {
        self.ctx.iter().any(|r| r.is_set())
    }

    /// Хоть один ключ режима (`pool*`/`btc*`) задан — нужен `--regime-from`.
    pub(crate) fn uses_regime(&self) -> bool {
        self.ctx[3..].iter().any(|r| r.is_set())
    }

    /// Ключи контекста набора для шапки: `ret1h_min=… pool4h_max=…`.
    fn ctx_label(&self) -> String {
        let mut parts = Vec::new();
        for (name, r) in CTX_AXES.iter().zip(&self.ctx) {
            if let Some(v) = r.min {
                parts.push(format!("{name}_min={v}"));
            }
            if let Some(v) = r.max {
                parts.push(format!("{name}_max={v}"));
            }
        }
        if parts.is_empty() {
            "none".to_string()
        } else {
            parts.join(",")
        }
    }

    fn from_args(args: &BounceGridArgs) -> Self {
        Self {
            name: String::new(),
            frontrun_only: args.frontrun_only,
            min_age_secs: args.min_age_secs,
            min_flow_pct: args.min_flow_pct,
            side: args.side,
            eaten_max_pct: None,
            usd_min: None,
            ctx: [Range::default(); CTX_AXES.len()],
        }
    }

    /// `<имя>:<k=v,…>` — см. `BounceGridArgs::sets`. Повтор ключа и чужой
    /// ключ — отказ: набор пишется один раз и читается людьми.
    pub fn parse(spec: &str) -> anyhow::Result<Self> {
        let (name, rest) = spec
            .split_once(':')
            .ok_or_else(|| anyhow::anyhow!("--set {spec:?}: ожидается <имя>:<k=v,…>"))?;
        anyhow::ensure!(
            !name.is_empty()
                && name
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-'))
                && name != "."
                && name != "..",
            "--set {spec:?}: имя набора — буквы, цифры, `.`, `_`, `-`"
        );
        let mut set = Self {
            name: name.to_string(),
            frontrun_only: false,
            min_age_secs: None,
            min_flow_pct: None,
            side: None,
            eaten_max_pct: None,
            usd_min: None,
            ctx: [Range::default(); CTX_AXES.len()],
        };
        let mut seen = std::collections::BTreeSet::new();
        for kv in rest.split(',').filter(|s| !s.is_empty()) {
            let (k, v) = kv.split_once('=').unwrap_or((kv, ""));
            anyhow::ensure!(
                seen.insert(k.to_string()),
                "--set {spec:?}: ключ {k} повторяется"
            );
            match k {
                "age" => {
                    set.min_age_secs = Some(
                        v.parse()
                            .map_err(|e| anyhow::anyhow!("--set {spec:?}: age={v:?}: {e}"))?,
                    );
                }
                "flow" => {
                    set.min_flow_pct = Some(
                        v.parse()
                            .map_err(|e| anyhow::anyhow!("--set {spec:?}: flow={v:?}: {e}"))?,
                    );
                }
                "side" => {
                    set.side = Some(match v {
                        "bid" => SideArg::Bid,
                        "ask" => SideArg::Ask,
                        _ => anyhow::bail!("--set {spec:?}: side={v:?}, ожидается bid|ask"),
                    });
                }
                "usd_min" => {
                    let usd: f64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: usd_min={v:?}: {e}"))?;
                    anyhow::ensure!(
                        usd.is_finite() && usd >= 0.0,
                        "--set {spec:?}: usd_min={v:?} — доллары обязаны быть неотрицательным числом"
                    );
                    set.usd_min = Some(usd);
                }
                "eaten" => {
                    let pct: f64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: eaten={v:?}: {e}"))?;
                    anyhow::ensure!(
                        pct.is_finite(),
                        "--set {spec:?}: eaten={v:?} — процент обязан быть числом"
                    );
                    set.eaten_max_pct = Some(pct);
                }
                "frontrun" => {
                    anyhow::ensure!(
                        v.is_empty() || v == "1" || v == "true",
                        "--set {spec:?}: frontrun без значения (или =1)"
                    );
                    set.frontrun_only = true;
                }
                _ => {
                    let (axis, bound) = k
                        .rsplit_once('_')
                        .ok_or_else(|| anyhow::anyhow!("--set {spec:?}: неизвестный ключ {k:?} (age|flow|side|frontrun|eaten|<ось>_min|<ось>_max)"))?;
                    let i = CTX_AXES.iter().position(|a| *a == axis).ok_or_else(|| {
                        anyhow::anyhow!("--set {spec:?}: неизвестная ось {axis:?} (ret10m|ret1h|ret4h|pool1h|pool4h|btc1h|btc4h)")
                    })?;
                    let v: f64 = v
                        .parse()
                        .map_err(|e| anyhow::anyhow!("--set {spec:?}: {k}={v:?}: {e}"))?;
                    anyhow::ensure!(
                        v.is_finite(),
                        "--set {spec:?}: {k}={v:?} — bps обязаны быть числом"
                    );
                    match bound {
                        "min" => set.ctx[i].min = Some(v),
                        "max" => set.ctx[i].max = Some(v),
                        _ => anyhow::bail!(
                            "--set {spec:?}: {k:?} — ожидается <ось>_min или <ось>_max"
                        ),
                    }
                }
            }
        }
        Ok(set)
    }
}

/// Фильтр касаний набора в момент касания — один и тот же для сигналов сетки
/// (`signals_for`) и для замера ёмкости (`lob fill-capacity`, S10): порог
/// плотности, фронтран, возраст, сила «×поток», сторона, съедание, номинал,
/// контекст. Касание, не прошедшее фильтр, — «пропуск» у вызывающего.
pub(crate) struct TouchFilter<'a> {
    pub(crate) tick: f64,
    pub(crate) lot: f64,
    pub(crate) frontrun_only: bool,
    pub(crate) mode: H3Mode,
    pub(crate) min_age_ms: Option<i64>,
    pub(crate) min_flow_pct: Option<f64>,
    pub(crate) side: Option<Side>,
    pub(crate) eaten_max_pct: Option<f64>,
    pub(crate) usd_min: Option<f64>,
    pub(crate) ctx: Option<&'a [TouchContext]>,
    pub(crate) ctx_ranges: [Range; CTX_AXES.len()],
}

impl<'a> TouchFilter<'a> {
    fn from_day(p: &DayParams<'a>) -> Self {
        Self {
            tick: p.tick,
            lot: p.lot,
            frontrun_only: p.frontrun_only,
            mode: p.mode,
            min_age_ms: p.min_age_ms,
            min_flow_pct: p.min_flow_pct,
            side: p.side,
            eaten_max_pct: p.eaten_max_pct,
            usd_min: p.usd_min,
            ctx: p.ctx,
            ctx_ranges: p.ctx_ranges,
        }
    }

    /// Фильтр набора над касаниями суток с готовым контекстом (`touch_contexts`):
    /// контекст подаётся, только если у набора есть ключи контекста.
    pub(crate) fn from_set(
        set: &FilterSet,
        mode: H3Mode,
        tick: f64,
        lot: f64,
        ctx: &'a [TouchContext],
    ) -> Self {
        Self {
            tick,
            lot,
            frontrun_only: set.frontrun_only,
            mode,
            min_age_ms: set.min_age_secs.map(|s| s.saturating_mul(1_000)),
            min_flow_pct: set.min_flow_pct,
            side: set.side.map(Side::from),
            eaten_max_pct: set.eaten_max_pct,
            usd_min: set.usd_min,
            ctx: if set.uses_ctx() { Some(ctx) } else { None },
            ctx_ranges: set.ctx,
        }
    }

    /// Проходит ли касание `t` с индексом `ti` (индекс — в контекст суток).
    #[allow(clippy::cast_precision_loss)]
    pub(crate) fn admits(&self, ti: usize, t: &TouchRecord) -> bool {
        if self.frontrun_only && t.frontrun_tick.is_none() {
            return false;
        }
        // База E1 (В-66): плотность обязана держать порог при подходе цены,
        // а не только при рождении — иначе в сетку идут касания
        // «бывших» плотностей. Окно проверено при старте прогона.
        if self.mode.holds_at_touch(t) != Some(true) {
            return false;
        }
        if self.min_age_ms.is_some_and(|n| t.age_ms() < n) {
            return false;
        }
        if let Some(s_min) = self.min_flow_pct {
            let flow_ok = t.flow_1h_lots > 0
                && t.size_at_touch as f64 / t.flow_1h_lots as f64 * 100.0 >= s_min;
            if !flow_ok {
                return false;
            }
        }
        // Ось стороны: другая сторона выбывает до форм (как возраст и сила).
        if self.side.is_some_and(|s| t.side != s) {
            return false;
        }
        // Состояние стены: съедена к касанию сильнее порога — не вход.
        if self.eaten_max_pct.is_some_and(|m| eaten_pct(t) > m) {
            return false;
        }
        // Размер стены: номинал при касании ниже порога набора — не вход.
        if self.usd_min.is_some_and(|m| {
            t.price_tick as f64 * self.tick * t.size_at_touch as f64 * self.lot < m
        }) {
            return false;
        }
        // Контекст (S4): ход до касания и режим — вне границ набора или
        // без значения при заданной границе — не вход.
        if let Some(ctx) = self.ctx {
            let c = &ctx[ti];
            if !self
                .ctx_ranges
                .iter()
                .zip(&c.axes)
                .all(|(r, v)| r.holds(*v))
            {
                return false;
            }
        }
        true
    }
}

/// Результат одной формы на одних сутках одного символа.
struct FormDayResult {
    form: usize,
    run: BounceRun,
    /// Касаний, для которых форму не построить (нет σ / фронтрана / второй
    /// плотности, `--frontrun-only`) — сигнала у них нет.
    skipped: u64,
}

/// Колонки `rounds.csv`. F4 (план 2026-09-20, В-78) добавила четыре в конец:
/// `fill_frac` (доля исполненного от заказанного), `entry_vwap` (средняя цена
/// исполненного входа — по ней и стоп/тейк), `legs_filled`, `legs_rejected`
/// (ног, которых биржа не поставила, В-72). Прежние колонки остались на своих
/// местах и с теми же значениями — на этом стоит гейт «те же круги».
const ROUNDS_HEADER: [&str; 16] = [
    "symbol",
    "day_utc",
    "form",
    "signal_index",
    "t0_ns",
    "dir",
    "entry_px",
    "exit_px",
    "qty",
    "net_bps",
    "reason",
    "exit_ns",
    "fill_frac",
    "entry_vwap",
    "legs_filled",
    "legs_rejected",
];

const FORMS_HEADER: [&str; 27] = [
    "symbol",
    "day_utc",
    "form",
    "n_signals",
    "n_submitted",
    "n_fills",
    "n_busy",
    "entry_rejected",
    "entry_crossed",
    "sum_net_bps",
    "n_stop",
    "n_take",
    "n_trail",
    "n_deadline",
    "n_early",
    "n_eaten",
    "n_partial",
    "n_horizon",
    "incomplete",
    "n_skipped",
    "n_residual_flattened",
    "n_fill_by_cross",
    "n_rejected_postonly",
    "signals_by_hour",
    // F5 (В-74): снятия неисполненного входа по причинам — «стена снята»,
    // «цена ушла из полосы», потолок. Добавлены в конец: прежние колонки
    // остались на местах, на этом стоит гейт «те же круги».
    "n_entry_cancelled_ttl",
    "n_entry_cancelled_wall_dead",
    "n_entry_cancelled_price_left",
];

pub(crate) fn pool_symbols(root: &Path) -> anyhow::Result<Vec<String>> {
    let path = root.join("instruments.csv");
    let mut r = super::pick::instruments_csv_reader(&path)
        .map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
    let mut out = Vec::new();
    for rec in r.records() {
        let rec = rec.map_err(|e| anyhow::anyhow!("{}: {e}", path.display()))?;
        if let Some(s) = rec.get(0) {
            if !s.is_empty() && !s.starts_with('#') {
                out.push(s.to_string());
            }
        }
    }
    anyhow::ensure!(!out.is_empty(), "{}: пул пуст", path.display());
    Ok(out)
}

/// Сигналы формы: план базы на каждое касание (`σ_H` за окно дедлайна — только
/// σ-формам); касания, для которых форму не построить, пропускаются и
/// считаются (второе значение). С `--signal approach` `touches` — это **вид**
/// записей подхода как касаний (`cached_approaches`/`touch_view_of_approach`:
/// `start_ms` = `arm_ms`), а план строится `approach_plan` от самой записи
/// подхода: вход ставится на взводе, а не на касании (F6, В-73).
#[allow(clippy::cast_precision_loss)]
fn signals_for(
    touches: &[TouchRecord],
    approaches: Option<&[crate::lob::levels::ApproachRecord]>,
    sigma: &SigmaSeries,
    form: &GridForm,
    p: &DayParams<'_>,
) -> anyhow::Result<(Vec<BounceSignal>, u64)> {
    let deadline_ns = deadline_ns_from_secs(form.deadline_secs)?;
    let early_exit_ns = early_exit_ns_from_secs(None)?;
    let mut skipped: u64 = 0;
    if let Some(ctx) = p.ctx {
        anyhow::ensure!(
            ctx.len() == touches.len(),
            "контекст касаний ({}) не совпадает с касаниями ({})",
            ctx.len(),
            touches.len()
        );
    }
    if let Some(ap) = approaches {
        anyhow::ensure!(
            ap.len() == touches.len(),
            "записи подхода ({}) не совпадают с их видом как касаний ({})",
            ap.len(),
            touches.len()
        );
    }
    let filter = TouchFilter::from_day(p);
    let mut signals: Vec<BounceSignal> = touches
        .iter()
        .enumerate()
        .filter_map(|(ti, t)| {
            if !filter.admits(ti, t) {
                skipped += 1;
                return None;
            }
            let sigma_bps = if form.form.needs_sigma() {
                sigma.sigma_bps(t.start_ms, form.deadline_secs)
            } else {
                None
            };
            let shape = PlanShape {
                lot: p.lot,
                post_only: p.post_only,
                trail_bps: 0.0,
                trail_activate_bps: 0.0,
                grid_legs: 1,
                grid_step_ticks: 0,
                // F6 (В-73): форма входа — ось сетки (`--entry-form`):
                // `single@fr` — прежняя нога, лестница — ноги от `from` до `to`
                // bps над стеной.
                entry_form: form.entry_form,
                deadline_ns,
                early_exit_ns,
                // F5 (В-74): режим срока жизни входа — из формы сетки
                // (`--entry-ttl-secs`), а условия «стена снята»/«цена ушла»
                // читают `--h3-usd` и `--band-exit-bps`.
                entry_ttl: form.entry_ttl,
                h3_usd: p.h3_usd,
                band_exit_bps: p.band_exit_bps,
            };
            let built = match approaches {
                Some(ap) => approach_plan(&ap[ti], p.tick, form.form, sigma_bps, shape),
                None => bounce_plan(t, p.tick, form.form, sigma_bps, shape),
            };
            let Some((dir, plan)) = built else {
                skipped += 1;
                return None;
            };
            Some(BounceSignal {
                t0_ns: t.start_ms.saturating_mul(1_000_000),
                sigma: dir,
                plan,
                profile: 0,
            })
        })
        .collect();
    signals.sort_by_key(|s| s.t0_ns);
    Ok((signals, skipped))
}

/// События суток крейта из всех частей дня — в `Vec` **точного** размера:
/// сначала части считаются (`count_feed_events`), потом декодируются в
/// буфер с готовой ёмкостью. Рост удвоением держал старый и новый буфер
/// вместе (пик до 3× итога) и ронял сетку на сервере по OOM на сутках в
/// ~20 млн событий (2026-09-18); второй декод дешевле памяти.
fn day_events(parts: &[PathBuf]) -> anyhow::Result<Vec<HbtEvent>> {
    let started = Instant::now();
    let mut total = 0usize;
    let mut counted_parts = 0usize;
    for path in parts {
        total += match cached_event_count(path) {
            Some(n) => n,
            None => {
                let mut feed = open_replay_feed(path)?;
                let n = count_feed_events(&mut feed);
                store_event_count(path, n);
                counted_parts += 1;
                n
            }
        };
    }
    let counted = started.elapsed().as_secs_f64();
    let mut events: Vec<HbtEvent> = Vec::with_capacity(total);
    for path in parts {
        let mut feed = open_replay_feed(path)?;
        feed_events_into(&mut feed, &mut events);
    }
    // Кэш числа событий — только ёмкость буфера: разошёлся — буфер просто
    // вырос, круги те же; сайдкары переписываются честным пересчётом.
    if events.len() != total {
        eprintln!(
            "bounce-grid:   события: кэш числа врал ({total} против {}), сайдкары пересчитаны",
            events.len()
        );
        for path in parts {
            let mut feed = open_replay_feed(path)?;
            let n = count_feed_events(&mut feed);
            store_event_count(path, n);
        }
    }
    eprintln!(
        "bounce-grid:   события: счёт {counted:.2}s ({counted_parts} из {} частей считано, остальные из кэша) · декод {:.2}s · {}",
        parts.len(),
        started.elapsed().as_secs_f64() - counted,
        events.len()
    );
    Ok(events)
}

/// Сайдкар `<бинлог>.events` — число событий крейта в части: `размер мтайм число`.
/// Счёт — второй полный декод части (2.8 с из 8.2 с на сутках NEAR, замер
/// 20.09) ради буфера точного размера; число части не меняется, пока не
/// меняется сам файл, так что оно кэшируется рядом с ним по размеру и
/// мтайму. Суффикс не бинлоговый — резолверы частей сайдкар не видят.
fn event_count_sidecar(path: &Path) -> PathBuf {
    let mut name = path.as_os_str().to_owned();
    name.push(".events");
    PathBuf::from(name)
}

fn file_stamp(path: &Path) -> Option<(u64, u64)> {
    let meta = std::fs::metadata(path).ok()?;
    let mtime = meta
        .modified()
        .ok()?
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_secs();
    Some((meta.len(), mtime))
}

fn cached_event_count(path: &Path) -> Option<usize> {
    let (len, mtime) = file_stamp(path)?;
    let text = std::fs::read_to_string(event_count_sidecar(path)).ok()?;
    let mut it = text.split_whitespace();
    let (l, m, n) = (
        it.next()?.parse::<u64>().ok()?,
        it.next()?.parse::<u64>().ok()?,
        it.next()?.parse::<usize>().ok()?,
    );
    (l == len && m == mtime).then_some(n)
}

/// Не смог записать — не беда: следующий прогон снова посчитает.
fn store_event_count(path: &Path, n: usize) {
    if let Some((len, mtime)) = file_stamp(path) {
        let _ = std::fs::write(event_count_sidecar(path), format!("{len} {mtime} {n}\n"));
    }
}

/// Параметры прогона суток одной структурой (clippy держит предел семи аргументов).
#[derive(Debug, Clone, Copy)]
struct DayParams<'a> {
    tick: f64,
    lot: f64,
    rtt_ns: ExecLatency,
    /// Модель очереди/исполнения суток (`--queue-model`, F3) — одна на процесс.
    queue_model: QueueModelKind,
    order_qty: f64,
    threads: usize,
    /// Вход пост-онли (В-72): у плана `post_only`, у сетки умолчание —
    /// включён (`BounceGridArgs::entry_post_only`).
    post_only: bool,
    /// E3: входить только от фронтрана (`--frontrun-only`).
    frontrun_only: bool,
    /// Порог уровня — проверяется и **в момент касания** (`H3Mode::holds_at_touch`).
    mode: H3Mode,
    /// Номинал порога В-66 в долларах (`--h3-usd`): из него план считает
    /// `level_floor_qty` — порог «стена снята» (F5, В-74). `None` — условия
    /// F5 выключены (режим `touch`), иначе `run_bounce_grid` отказал бы.
    h3_usd: Option<f64>,
    /// Полоса ухода цены, bps (F5, В-74, `--band-exit-bps`); `0` — условие
    /// выключено (режим `touch`).
    band_exit_bps: f64,
    /// Фильтры базы в момент касания: возраст плотности и сила «×поток».
    min_age_ms: Option<i64>,
    min_flow_pct: Option<f64>,
    /// Ось стороны (`--side`): `None` — обе стороны.
    side: Option<Side>,
    /// Доля съедания стены к касанию не больше этого процента (`eaten=`).
    eaten_max_pct: Option<f64>,
    /// Номинал стены при касании ≥ (`usd_min=`), доллары.
    usd_min: Option<f64>,
    /// Контекст касаний суток (тот же порядок, что `touches`) — только когда
    /// у набора есть ключи контекста; границы — `ctx_ranges`.
    ctx: Option<&'a [TouchContext]>,
    ctx_ranges: [Range; CTX_AXES.len()],
    /// Ряд `σ` символа (все сутки записи подряд).
    sigma: &'a SigmaSeries,
}

/// Готовые результаты форм уходят в `sink` **по порядку форм**: форма,
/// закончившая раньше соседей с меньшим индексом, ждёт в буфере (не больше
/// числа потоков), чтобы дамп не зависел от числа потоков.
struct FormOrder<'a> {
    pending: BTreeMap<usize, (BounceRun, Vec<BounceSignal>, u64)>,
    next_form: usize,
    done: usize,
    sink: &'a mut (dyn FnMut(FormDayResult, &[BounceSignal]) -> anyhow::Result<()> + Send),
}

/// Все формы над одними сутками: потоки берут формы по счётчику. `Setups`
/// — один проход книги на сутки (`SignalWindows`, общий для форм), дальше у
/// каждой формы движок только внутри кругов; `Full` — у каждой формы свой
/// `Backtest` над всеми событиями суток (эталон гейта).
///
/// Память (сервер, 2026-09-18): сигналы формы строятся **в потоке, когда
/// форма взята** (например, 48 форм × 100 тыс. касаний × 128 Б заранее — 600 МБ), а
/// результат формы отдаётся `sink` сразу и до конца суток не копится
/// (`FormOrder`). Возвращает число форм, отданных в `sink`.
///
/// `approaches` — записи подхода (F6, `--signal approach`): те же сутки и тот
/// же порядок, что `touches` (их вид как касания); `None` — сигнал по касаниям.
fn drive_day(
    events: &[HbtEvent],
    windows: Option<&SignalWindows>,
    touches: &[TouchRecord],
    approaches: Option<&[crate::lob::levels::ApproachRecord]>,
    forms: &[GridForm],
    p: DayParams<'_>,
    sink: &mut (dyn FnMut(FormDayResult, &[BounceSignal]) -> anyhow::Result<()> + Send),
) -> anyhow::Result<usize> {
    let next = AtomicUsize::new(0);
    let failure: Mutex<Option<anyhow::Error>> = Mutex::new(None);
    let order = Mutex::new(FormOrder {
        pending: BTreeMap::new(),
        next_form: 0,
        done: 0,
        sink,
    });
    std::thread::scope(|scope| {
        for _ in 0..p.threads.max(1) {
            scope.spawn(|| loop {
                let i = next.fetch_add(1, Ordering::Relaxed);
                if i >= forms.len() {
                    break;
                }
                if failure.lock().map(|f| f.is_some()).unwrap_or(true) {
                    break;
                }
                let cfg = DriveConfig {
                    order_qty: p.order_qty,
                    first_order_id: 1,
                    queue_model: p.queue_model,
                };
                let step = signals_for(touches, approaches, p.sigma, &forms[i], &p).and_then(
                    |(signals, skipped)| {
                        let driven = match windows {
                            Some(w) => drive_bounce_windowed(events, w, &signals, &cfg, p.rtt_ns),
                            None => with_backtest_over(
                                events,
                                p.tick,
                                p.lot,
                                p.rtt_ns,
                                p.queue_model,
                                |bt| drive_bounce(bt, 0, &signals, &cfg),
                            ),
                        };
                        driven
                            .map(|run| (run, signals, skipped))
                            .map_err(|e| anyhow::anyhow!("форма #{i}: {e}"))
                    },
                );
                let flushed = match step {
                    Ok((run, signals, skipped)) => match order.lock() {
                        Ok(mut o) => {
                            o.pending.insert(i, (run, signals, skipped));
                            let mut res = Ok(());
                            loop {
                                let form = o.next_form;
                                let Some((run, signals, skipped)) = o.pending.remove(&form) else {
                                    break;
                                };
                                res = (o.sink)(FormDayResult { form, run, skipped }, &signals);
                                o.next_form += 1;
                                o.done += 1;
                                if res.is_err() {
                                    break;
                                }
                            }
                            res
                        }
                        Err(_) => Err(anyhow::anyhow!("результаты форм: мьютекс")),
                    },
                    Err(e) => Err(e),
                };
                if let Err(e) = flushed {
                    if let Ok(mut f) = failure.lock() {
                        if f.is_none() {
                            *f = Some(e);
                        }
                    }
                    break;
                }
            });
        }
    });
    if let Some(e) = failure.into_inner().ok().flatten() {
        return Err(e);
    }
    let o = order
        .into_inner()
        .map_err(|_| anyhow::anyhow!("результаты форм: мьютекс"))?;
    anyhow::ensure!(
        o.pending.is_empty(),
        "формы без записи в дамп: {}",
        o.pending.len()
    );
    Ok(o.done)
}

/// Окна сетапов суток (`--driver setups`): снимок книги на каждый `t0`
/// касания — один раз на сутки, общий для всех форм **и наборов** (`--set`):
/// касания те же, фильтры наборов только выбирают из них сигналы.
fn day_windows(
    events: &[HbtEvent],
    touches: &[TouchRecord],
    driver: DriverArg,
    tick: f64,
    lot: f64,
) -> Option<SignalWindows> {
    match driver {
        DriverArg::Full => None,
        DriverArg::Setups => {
            let t0s: Vec<i64> = touches
                .iter()
                .map(|t| t.start_ms.saturating_mul(1_000_000))
                .collect();
            let started = Instant::now();
            let w = SignalWindows::build(events, &t0s, tick, lot);
            eprintln!(
                "bounce-grid:   окна: снимков {} · уровней всего {} (в среднем {:.0} на снимок) · {:.2}s",
                w.len(),
                w.levels_total(),
                w.levels_total() as f64 / w.len().max(1) as f64,
                started.elapsed().as_secs_f64()
            );
            Some(w)
        }
    }
}

struct Outputs {
    rounds: csv::Writer<std::fs::File>,
    forms: csv::Writer<std::fs::File>,
    rounds_path: PathBuf,
    forms_path: PathBuf,
}

/// Сигналы формы по часам UTC суток, `h0:h1:…:h23` (В-60): вердикт по одним
/// суткам кластеризует интервал `net_fill` по часам, и промахи (у них в
/// `rounds.csv` нет времени) раскладываются по часам из этого столбца.
fn signals_by_hour(signals: &[BounceSignal]) -> String {
    let mut by_hour = [0u64; 24];
    for s in signals {
        let secs = s.t0_ns.div_euclid(1_000_000_000).rem_euclid(86_400);
        by_hour[(secs / 3_600) as usize] += 1;
    }
    by_hour
        .iter()
        .map(u64::to_string)
        .collect::<Vec<_>>()
        .join(":")
}

impl Outputs {
    fn create(out_dir: &Path, header: &str) -> anyhow::Result<Self> {
        std::fs::create_dir_all(out_dir)?;
        let rounds_path = out_dir.join("rounds.csv");
        let forms_path = out_dir.join("forms.csv");
        let mut rf = std::fs::File::create(&rounds_path)?;
        writeln!(rf, "{header}")?;
        let mut ff = std::fs::File::create(&forms_path)?;
        writeln!(ff, "{header}")?;
        let mut rounds = csv::WriterBuilder::new().has_headers(false).from_writer(rf);
        rounds.write_record(ROUNDS_HEADER)?;
        let mut forms = csv::WriterBuilder::new().has_headers(false).from_writer(ff);
        forms.write_record(FORMS_HEADER)?;
        Ok(Self {
            rounds,
            forms,
            rounds_path,
            forms_path,
        })
    }

    fn write_form(
        &mut self,
        symbol: &str,
        day: &str,
        form: GridForm,
        signals: &[BounceSignal],
        run: &BounceRun,
        skipped: u64,
    ) -> anyhow::Result<u64> {
        anyhow::ensure!(
            run.fill_reason.len() == run.fills.len()
                && run.fill_signal.len() == run.fills.len()
                && run.fill_exit_ns.len() == run.fills.len(),
            "{symbol} {day} {}: кругов {}, причин {}, сигналов {} — дамп не пишется",
            form.label,
            run.fills.len(),
            run.fill_reason.len(),
            run.fill_signal.len()
        );
        // `fill_signal` нумерует сигналы в порядке **по t0** (драйвер сортирует
        // копию), а `signals` идут в порядке трекера (по концу касания) — до
        // 2026-09-18 `t0_ns` брался по индексу из несортированного списка и у
        // части кругов был чужим (поймал вердикт по часам: кругов в часе
        // больше сигналов). Сортировка устойчивая, равные t0 взаимозаменяемы.
        let mut t0s: Vec<i64> = signals.iter().map(|s| s.t0_ns).collect();
        t0s.sort_unstable();
        let mut sum_net = 0.0_f64;
        for (i, fill) in run.fills.iter().enumerate() {
            let net = roundtrip_net_bps(fill);
            if let Some(v) = net {
                sum_net += v;
            }
            let sig = run.fill_signal[i];
            let t0 = t0s.get(sig).copied().unwrap_or(0);
            self.rounds.write_record([
                symbol.to_string(),
                day.to_string(),
                form.label.to_string(),
                sig.to_string(),
                t0.to_string(),
                fill.dir.to_string(),
                format!("{:.10}", fill.entry_px),
                format!("{:.10}", fill.exit_px),
                format!("{:.10}", fill.qty),
                net.map(|v| format!("{v:.6}"))
                    .unwrap_or_else(|| "not_measured".to_string()),
                exit_reason_label(run.fill_reason[i]).to_string(),
                run.fill_exit_ns[i].to_string(),
                // F4 (В-78): фактическое исполнение круга — доля и средняя
                // цена входа (по ней считает `roundtrip_net_bps`), число
                // исполнившихся ног и ног, которых биржа не поставила (В-72).
                format!("{:.6}", fill.fill_frac),
                format!("{:.10}", fill.entry_vwap),
                fill.legs_filled.to_string(),
                fill.legs_rejected.to_string(),
            ])?;
        }
        self.forms.write_record([
            symbol.to_string(),
            day.to_string(),
            form.label.to_string(),
            signals.len().to_string(),
            run.submitted_signal.len().to_string(),
            run.fills.len().to_string(),
            run.busy_signal.len().to_string(),
            run.entry_rejected.to_string(),
            run.entry_crossed.to_string(),
            format!("{sum_net:.6}"),
            run.exits.stop.to_string(),
            run.exits.take.to_string(),
            run.exits.trail.to_string(),
            run.exits.deadline.to_string(),
            run.exits.early.to_string(),
            run.exits.eaten.to_string(),
            run.exits.partial.to_string(),
            run.exits.horizon.to_string(),
            run.incomplete.to_string(),
            skipped.to_string(),
            run.residual_flattened.to_string(),
            // Путь исполнения (3) (F3): крейт исполнил ногу обновлением
            // лучшей цены, а не сделкой, — счётчик по кругам формы.
            run.fills
                .iter()
                .filter(|f| f.fill_by_cross)
                .count()
                .to_string(),
            // Ног входа, которых биржа не поставила (пост-онли `Expired` или
            // `Rejected`, В-72) — по всем кругам формы за сутки.
            run.rejected_postonly.to_string(),
            signals_by_hour(signals),
            // Снятия неисполненного входа по причинам (F5, В-74).
            run.entry_cancelled_ttl.to_string(),
            run.entry_cancelled_wall_dead.to_string(),
            run.entry_cancelled_price_left.to_string(),
        ])?;
        // Инвариант вердикта по часам (В-60): кругов в часе не больше сигналов.
        debug_assert!({
            let mut fills_by_hour = [0u64; 24];
            for &sig in &run.fill_signal {
                let secs = t0s[sig].div_euclid(1_000_000_000).rem_euclid(86_400);
                fills_by_hour[(secs / 3_600) as usize] += 1;
            }
            let sig_by_hour: Vec<u64> = signals_by_hour(signals)
                .split(':')
                .map(|v| v.parse().unwrap_or(0))
                .collect();
            (0..24).all(|h| fills_by_hour[h] <= sig_by_hour[h])
        });
        self.rounds.flush()?;
        self.forms.flush()?;
        Ok(run.fills.len() as u64)
    }
}

/// Окно силы порога обязано быть одним из окон оси `strength_e2`, иначе
/// порог в момент касания (`H3Mode::holds_at_touch`) не проверить — отказ,
/// не молчаливый пропуск. Общая проверка сетки и замера ёмкости.
pub(crate) fn ensure_holds_at_touch_checkable(mode: &H3Mode, symbol: &str) -> anyhow::Result<()> {
    let probe = TouchRecord {
        side: Side::Bid,
        price_tick: 1,
        touch_index: 0,
        start_ms: 0,
        end_ms: 0,
        duration_ms: 0,
        level_birth_ms: 0,
        size_at_touch: i64::MAX / 4,
        size_max_before: 0,
        traded_during: 0,
        frontrun_lots: 0,
        frontrun_tick: None,
        swept_lots: 0,
        round_zeros: 0,
        ended_by_death: false,
        stack_levels: 0,
        stack_next_tick: None,
        traded_first_s: [0; 3],
        flow_1h_lots: 0,
        strength_e2: [i64::MAX; 3],
        strength_held_e2: [-1; 4],
        repeat_count: 0,
    };
    anyhow::ensure!(
        mode.holds_at_touch(&probe).is_some(),
        "{symbol}: окно силы порога не из STRENGTH_WINDOWS_BPS {:?} — порог в момент касания не проверить",
        crate::lob::levels::STRENGTH_WINDOWS_BPS
    );
    Ok(())
}

pub fn run_bounce_grid(args: &BounceGridArgs) -> anyhow::Result<BounceGridSummary> {
    anyhow::ensure!(
        args.root.is_dir(),
        "{}: корень записи не каталог",
        args.root.display()
    );
    let lot_sources = usize::from(args.order_qty_e9.is_some())
        + usize::from(args.order_qty_from_pool)
        + usize::from(args.order_usd.is_some());
    anyhow::ensure!(
        lot_sources == 1,
        "лот задаётся ровно одним способом: --order-qty-e9 | --order-qty-from-pool | --order-usd (изобретённого умолчания нет, §9 плана)"
    );
    // Модель очереди/исполнения (F3): обязательный флаг, разбирается один раз
    // на процесс — она не часть фильтров набора (`--set`), а движок.
    let queue_model = args.queue_model;
    let threads = args
        .threads
        .unwrap_or_else(|| {
            std::thread::available_parallelism()
                .map(|n| n.get())
                .unwrap_or(1)
        })
        .max(1);
    // Формы базы (В-65) — имена владельца: каждая разбирается и проверяется,
    // повтор имени — отказ (это было бы лишнее «испытание» с тем же именем).
    let stops = args
        .stop_form
        .iter()
        .map(|s| StopForm::parse(s))
        .collect::<anyhow::Result<Vec<_>>>()?;
    let takes = args
        .take_form
        .iter()
        .map(|s| TakeForm::parse(s))
        .collect::<anyhow::Result<Vec<_>>>()?;
    for &stop in &stops {
        for &take in &takes {
            BounceForm {
                stop,
                take,
                take_floor_fees: args.take_floor_fees,
            }
            .validate()?;
        }
    }
    let deadlines: Vec<u64> = if args.deadline_secs.is_empty() {
        DEADLINE_SECS.to_vec()
    } else {
        for &d in &args.deadline_secs {
            anyhow::ensure!(
                DEADLINE_SECS_ALLOWED.contains(&d),
                "--deadline-secs {d}: не из разрешённых {DEADLINE_SECS_ALLOWED:?}"
            );
        }
        let mut d = args.deadline_secs.clone();
        d.sort_unstable();
        d.dedup();
        d
    };
    // Срок жизни входа (F5, В-74): сетка режимов из повторяемого
    // `--entry-ttl-secs`; условия рынка требуют порога В-66 в деньгах
    // (`--h3-usd`) и полосы ухода (`--band-exit-bps`) — числа замера, не
    // умолчания (`ensure_entry_conditions_args`).
    let entry_ttls = parse_entry_ttls(&args.entry_ttl_secs)?;
    let entry_conditions = entry_ttls.iter().any(|t| *t != EntryTtl::Touch);
    ensure_entry_conditions_args(entry_conditions, args.h3.h3_usd, args.band_exit_bps)?;
    let band_exit_bps = match args.band_exit_bps {
        Some(v) => {
            anyhow::ensure!(
                v.is_finite() && v > 0.0,
                "--band-exit-bps {v}: полоса ухода — конечное число > 0"
            );
            v
        }
        // Прежний режим (`touch`): условие выключено, число не читается.
        None => 0.0,
    };
    let entries = parse_entry_forms(&args.entry_form)?;
    let forms = grid_forms_with_axes(
        &stops,
        &takes,
        args.take_floor_fees,
        &deadlines,
        &entry_ttls,
        &entries,
    );
    {
        let labels: std::collections::BTreeSet<&str> = forms.iter().map(|f| f.label).collect();
        anyhow::ensure!(
            labels.len() == forms.len(),
            "сетка: повторяющиеся формы в --stop-form/--take-form/--entry-ttl-secs/--entry-form дают одинаковые имена"
        );
    }
    // F6 (В-73): сигнал по записи подхода — только из кэша F1, реплея
    // подходов у сетки нет (полосу `D` выбирает прогон `lob touches
    // --approach-bps`; в часы ночи их пишет шаг H3).
    if args.signal == SignalArg::Approach {
        anyhow::ensure!(
            args.touches_from.is_some(),
            "--signal approach: нужен кэш подходов --touches-from <dir> (approaches-<SYMBOL>.csv, F1) — реплей подходов сетка не считает"
        );
    }
    if let Some(dir) = &args.touches_from {
        anyhow::ensure!(dir.is_dir(), "--touches-from {}: не каталог", dir.display());
        anyhow::ensure!(
            !forms.iter().any(|f| f.form.needs_sigma()),
            "--touches-from: σ-формы (s<a>/t<b>) требуют реплея — σ в CSV касаний суточная и с шестью знаками"
        );
        anyhow::ensure!(
            args.warmup_ms.is_none() && args.repeat_window_ms.is_none(),
            "--touches-from: прогрев и окно повтора трекера — умолчания, как у ночного `lob touches`"
        );
    }
    let sets: Vec<FilterSet> = if args.sets.is_empty() {
        vec![FilterSet::from_args(args)]
    } else {
        anyhow::ensure!(
            !args.frontrun_only
                && args.min_age_secs.is_none()
                && args.min_flow_pct.is_none()
                && args.side.is_none(),
            "--set задан: фильтры касаний только в наборах, флаги --min-age-secs/--min-flow-pct/--side/--frontrun-only у команды — отказ"
        );
        let parsed = args
            .sets
            .iter()
            .map(|s| FilterSet::parse(s))
            .collect::<anyhow::Result<Vec<_>>>()?;
        let names: std::collections::BTreeSet<&str> =
            parsed.iter().map(|s| s.name.as_str()).collect();
        anyhow::ensure!(
            names.len() == parsed.len(),
            "--set: имена наборов повторяются"
        );
        parsed
    };
    // F6 (В-73): у записи подхода нет ни истории размера (ключ `eaten=`), ни
    // хода цены до взвода (ключи контекста `ret*`/`pool*`/`btc*`) — фильтры,
    // которые их читают, молча выбросили бы все сигналы; отказ, как у F2.
    if args.signal == SignalArg::Approach {
        anyhow::ensure!(
            !sets.iter().any(|s| s.eaten_max_pct.is_some()),
            "--signal approach: ключ `eaten=` у подхода не определён — размер на взводе и есть старт"
        );
        anyhow::ensure!(
            !sets.iter().any(FilterSet::uses_ctx),
            "--signal approach: ключи контекста (ret*/pool*/btc*) у подхода не определены — кэш подходов хода до взвода не несёт"
        );
    }
    anyhow::ensure!(
        args.regime_from.is_some() || !sets.iter().any(FilterSet::uses_regime),
        "ключи pool*/btc* у наборов требуют --regime-from <study/regime>"
    );
    if let Some(dir) = &args.regime_from {
        anyhow::ensure!(dir.is_dir(), "--regime-from {}: не каталог", dir.display());
    }
    let need_regime = args.regime_from.is_some() && sets.iter().any(FilterSet::uses_regime);
    let need_ret = sets.iter().any(|s| s.ctx[..3].iter().any(|r| r.is_set()));
    // Режим суток читается один раз на сутки (общий для символов).
    let mut regime_days: BTreeMap<String, RegimeDay> = BTreeMap::new();
    let symbols = if args.symbols.is_empty() {
        pool_symbols(&args.root)?
    } else {
        args.symbols.clone()
    };

    let header_for = |set: &FilterSet| {
        format!(
        "# lob bounce-grid: root={} days={} forms={} base=В-65(stop_form={:?} take_form={:?} take_floor_fees={:?} frontrun_only={} min_age_secs={:?} min_flow_pct={:?} side={} eaten_max={:?} usd_min={:?} ctx={} deadlines={:?}) RTT={}нс {} h3={:?} lot={} threads={} driver={} queue={} entry_post_only={} entry_ttl={} band_exit_bps={} signal={} entry_forms={} paths=1:сделки-на-нашей-цене-частично(очередь) 2:сделка-в-сторону-от-нас-весь-остаток(приоритет-цены) 3:лучшая-цена-дошла-до-нашей-без-сделки-весь-остаток(оптимистично-по-размеру,-счётчик-n_fill_by_cross) touches={} verified={}{}",
        args.root.display(),
        if args.days.is_empty() {
            "all".to_string()
        } else {
            args.days.join("+")
        },
        forms.len(),
        args.stop_form,
        args.take_form,
        args.take_floor_fees,
        set.frontrun_only,
        set.min_age_secs,
        set.min_flow_pct,
        set.side.map_or("both", SideArg::label),
        set.eaten_max_pct,
        set.usd_min,
        set.ctx_label(),
        deadlines,
        args.median_rtt_ns,
        args.median_rtt_ns.provenance(),
        args.h3.h3_mode,
        match (args.order_qty_e9, args.order_usd) {
            (Some(v), _) => format!("e9:{v}x{}", args.order_qty_mult),
            (None, Some(usd)) => format!("usd:{usd}x{}", args.order_qty_mult),
            _ => format!("pool(22a)x{}", args.order_qty_mult),
        },
        threads,
        args.driver.label(),
        queue_model.label(),
        args.entry_post_only(),
        entry_ttls
            .iter()
            .map(|t| t.label())
            .collect::<Vec<_>>()
            .join("+"),
        band_exit_bps,
        // F6 (В-73): по какому сигналу вход (`touch` — гейт, `approach` —
        // запись подхода F1) и какими формами входа (ось `--entry-form`).
        args.signal.label(),
        entries
            .iter()
            .map(|e| e.label())
            .collect::<Vec<_>>()
            .join("+"),
        args.touches_from
            .as_ref()
            .map_or("replay".to_string(), |d| format!("csv({})", d.display())),
        if args.allow_unverified {
            "allow-unverified(debug)"
        } else {
            "marker-required"
        },
        if set.name.is_empty() {
            String::new()
        } else {
            format!(" set={}", set.name)
        }
    )
    };
    let mut outs: Vec<Outputs> = Vec::with_capacity(sets.len());
    for set in &sets {
        let dir = if set.name.is_empty() {
            args.out_dir.clone()
        } else {
            args.out_dir.join(&set.name)
        };
        let header = header_for(set);
        outs.push(Outputs::create(&dir, &header)?);
        let mut m = std::fs::File::create(dir.join("manifest.txt"))?;
        writeln!(m, "{header}")?;
        writeln!(m, "symbols={}", symbols.join(","))?;
        writeln!(
            m,
            "forms={}",
            forms.iter().map(|f| f.label).collect::<Vec<_>>().join(",")
        )?;
    }
    if !args.sets.is_empty() {
        let mut m = std::fs::File::create(args.out_dir.join("manifest.txt"))?;
        writeln!(
            m,
            "# lob bounce-grid: sets={} (артефакты по подкаталогам)",
            sets.iter()
                .map(|s| s.name.as_str())
                .collect::<Vec<_>>()
                .join(",")
        )?;
        for spec in &args.sets {
            writeln!(m, "set={spec}")?;
        }
    }

    let mut summary = BounceGridSummary {
        forms: forms.len(),
        rounds_path: outs[0].rounds_path.clone(),
        forms_path: outs[0].forms_path.clone(),
        sets: sets
            .iter()
            .zip(&outs)
            .map(|(s, o)| SetPaths {
                name: s.name.clone(),
                rounds_path: o.rounds_path.clone(),
                forms_path: o.forms_path.clone(),
            })
            .collect(),
        ..Default::default()
    };

    for symbol in &symbols {
        let marker = args.root.join(format!("verify-{symbol}.status"));
        if !args.allow_unverified && !read_verify_marker(&marker) {
            eprintln!(
                "bounce-grid: {symbol} — маркер {} не `ok`, символ пропущен (fail-closed; --allow-unverified для отладки)",
                marker.display()
            );
            summary.symbols_skipped_unverified += 1;
            continue;
        }
        let started = Instant::now();
        let parts = session_parts_for(&args.root, symbol)?;
        anyhow::ensure!(
            !parts.is_empty(),
            "{symbol}: частей записи в {} нет",
            args.root.display()
        );
        let (tick_e9, step_e9) = read_tick_step(&parts[0].path)?;
        let tick = tick_e9 as f64 / 1e9;
        let lot = step_e9 as f64 / 1e9;
        // Порог плотности — любой из режимов В-61 (`--h3-mode notional|strength|both`)
        // или прежние floor/percentile; тик и шаг лота — из заголовка бинлога.
        let mode = resolve_h3_mode_full(&args.root, symbol, &args.h3, args.h3_k, tick_e9, step_e9)?;
        ensure_holds_at_touch_checkable(&mode, symbol)?;
        let cfg_levels = LevelsConfig {
            mode,
            warmup_ms: args.warmup_ms.unwrap_or(DEFAULT_WARMUP_MS),
            repeat_window_ms: args.repeat_window_ms.unwrap_or(DEFAULT_REPEAT_WINDOW_MS),
            approach_bps: None,
            approach_min_age_ms: 0,
        };
        let mut parts_by_day: BTreeMap<String, Vec<PathBuf>> = BTreeMap::new();
        for p in &parts {
            parts_by_day
                .entry(p.day_utc.clone())
                .or_default()
                .push(p.path.clone());
        }
        // S1: касания один раз на символ — общие для всех форм; из кэша
        // `--touches-from` (сутки корня) или реплеем книги, тогда вместе с
        // ними срезы середины по границам секунд — для ряда `σ` (В-62).
        // F6 (В-73): `--signal approach` — записи подхода из того же кэша
        // (`approaches-<SYMBOL>.csv`, F1); реплея подходов нет — полосу `D`
        // задаёт прогон `lob touches --approach-bps` (проверено выше).
        let (days, sigma_series) = if args.signal == SignalArg::Approach {
            let dir = args
                .touches_from
                .as_deref()
                .expect("--signal approach без --touches-from отвергнут выше");
            // Суток в кэше нет — символ пропускается, а не роняет весь прогон
            // (как у `lob fill-capacity --targets approaches`, F2): реплея
            // подходов у сетки нет, полосу `D` знает только прогон F1.
            let Ok(days) = cached_approaches(dir, symbol, parts_by_day.keys()) else {
                eprintln!(
                    "bounce-grid: {symbol} — кэш подходов не годится, символ пропущен (нужен прогон `lob touches --approach-bps D`)"
                );
                summary.symbols_without_touches += 1;
                continue;
            };
            summary.symbols_from_cache += 1;
            (days, SigmaSeries::from_mids(&[]))
        } else {
            match args
                .touches_from
                .as_deref()
                .map(|dir| cached_touches(dir, symbol, parts_by_day.keys(), need_ret))
            {
                Some(Ok(days)) => {
                    summary.symbols_from_cache += 1;
                    (days, SigmaSeries::from_mids(&[]))
                }
                other => {
                    if let Some(Err(why)) = other {
                        eprintln!("bounce-grid: {symbol} — кэш касаний не годится ({why}), реплей");
                    }
                    let replay =
                        replay_symbol_touches_and_second_mids(&args.root, symbol, cfg_levels)?;
                    let mut all =
                        Vec::with_capacity(replay.days.iter().map(|d| d.mids.len()).sum());
                    for d in &replay.days {
                        all.extend(d.mids.iter().copied());
                    }
                    let days = replay
                        .days
                        .into_iter()
                        .map(|d| DayTouches {
                            rets: d
                                .touches
                                .iter()
                                .map(|t| {
                                    std::array::from_fn(|k| {
                                        super::touches::pre_touch_return_bps_csv(
                                            &d.mids,
                                            t.start_ms,
                                            super::touches::PRE_TOUCH_MS[k],
                                        )
                                    })
                                })
                                .collect(),
                            day: d.day,
                            touches: d.touches,
                            approaches: None,
                        })
                        .collect();
                    (days, SigmaSeries::from_mids(&all))
                }
            }
        };
        let touches_total: usize = days.iter().map(|d| d.touches.len()).sum();
        if touches_total == 0 {
            let what = if args.signal == SignalArg::Approach {
                "записей подхода"
            } else {
                "касаний"
            };
            eprintln!("bounce-grid: {symbol} — {what} нет, символ пропущен");
            summary.symbols_without_touches += 1;
            continue;
        }
        let order_qty_e9 = match (args.order_qty_e9, args.order_usd) {
            (Some(v), _) => v,
            (None, usd) => {
                let last = days
                    .iter()
                    .rev()
                    .find_map(|d| d.touches.last())
                    .expect("касания есть — проверено выше");
                let csv = args.root.join("instruments.csv");
                match usd {
                    Some(usd) => pool_order_qty_usd(&csv, symbol, last.price_tick, tick_e9, usd)?,
                    None => pool_order_qty(&csv, symbol, last.price_tick, tick_e9)?,
                }
            }
        }
        .saturating_mul(i64::from(args.order_qty_mult.max(1)));
        let order_qty = order_qty_e9 as f64 / 1e9;

        for day in &days {
            // `--day`: гнать только выбранные сутки. Касания при этом считаются
            // по всей записи символа (возраст уровня и прогрев трекера не
            // зависят от границы суток), так что круги выбранного дня те же,
            // что и в прогоне без фильтра.
            if !args.days.is_empty() && !args.days.contains(&day.day) {
                continue;
            }
            if day.touches.is_empty() {
                continue;
            }
            let Some(day_parts) = parts_by_day.get(&day.day) else {
                anyhow::bail!("{symbol}: сутки {} есть в реплее, но частей нет", day.day);
            };
            let day_started = Instant::now();
            // S4: события одних суток, не всей сессии.
            let events = day_events(day_parts)?;
            if events.is_empty() {
                eprintln!(
                    "bounce-grid: {symbol} {} — событий нет, сутки пропущены",
                    day.day
                );
                continue;
            }
            // S2: все формы над одним потоком событий, потоками; результат
            // каждой формы — сразу в дамп. Окна суток — один раз на все наборы.
            let windows = day_windows(&events, &day.touches, args.driver, tick, lot);
            let regime = if need_regime {
                let dir = args.regime_from.as_deref().expect("проверено выше");
                if !regime_days.contains_key(&day.day) {
                    regime_days.insert(day.day.clone(), read_regime_day(dir, &day.day)?);
                }
                regime_days.get(&day.day)
            } else {
                None
            };
            let ctx = touch_contexts(&day.rets, &day.touches, regime);
            let mut rounds: u64 = 0;
            let day_label = day.day.clone();
            for (set, out) in sets.iter().zip(outs.iter_mut()) {
                let forms_done = {
                    let forms_ref = &forms;
                    let mut sink =
                        |r: FormDayResult, signals: &[BounceSignal]| -> anyhow::Result<()> {
                            let n = out.write_form(
                                symbol,
                                &day_label,
                                forms_ref[r.form],
                                signals,
                                &r.run,
                                r.skipped,
                            )?;
                            rounds = rounds.saturating_add(n);
                            Ok(())
                        };
                    drive_day(
                        &events,
                        windows.as_ref(),
                        &day.touches,
                        day.approaches.as_deref(),
                        &forms,
                        DayParams {
                            tick,
                            lot,
                            rtt_ns: args.median_rtt_ns,
                            queue_model,
                            order_qty,
                            threads,
                            post_only: args.entry_post_only(),
                            frontrun_only: set.frontrun_only,
                            min_age_ms: set.min_age_secs.map(|s| s.saturating_mul(1_000)),
                            min_flow_pct: set.min_flow_pct,
                            side: set.side.map(Side::from),
                            eaten_max_pct: set.eaten_max_pct,
                            usd_min: set.usd_min,
                            ctx: if set.uses_ctx() { Some(&ctx) } else { None },
                            ctx_ranges: set.ctx,
                            mode,
                            h3_usd: args.h3.h3_usd,
                            band_exit_bps,
                            sigma: &sigma_series,
                        },
                        &mut sink,
                    )?
                };
                anyhow::ensure!(
                    forms_done == forms.len(),
                    "{symbol} {} {}: форм посчитано {}, ожидалось {}",
                    day.day,
                    set.name,
                    forms_done,
                    forms.len()
                );
            }
            summary.rounds = summary.rounds.saturating_add(rounds);
            summary.symbol_days += 1;
            eprintln!(
                "bounce-grid: {symbol} {} touches={} events={} forms={} rounds={} {:.1}s",
                day.day,
                day.touches.len(),
                events.len(),
                forms.len(),
                rounds,
                day_started.elapsed().as_secs_f64()
            );
        }
        summary.symbols_done += 1;
        eprintln!(
            "bounce-grid: {symbol} готов — суток {}, касаний {}, {:.1}s",
            days.iter().filter(|d| !d.touches.is_empty()).count(),
            touches_total,
            started.elapsed().as_secs_f64()
        );
    }
    Ok(summary)
}

#[cfg(test)]
mod tests;
