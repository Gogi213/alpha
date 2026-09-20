//! `lob fill-capacity` — замер ёмкости исполнения входа у стены без движка
//! (S10 шаг 1, владелец 20.09). Логика — `crate::lob::capacity`; здесь
//! флаги, выбор касаний тем же фильтром, что у сетки (`--set`,
//! `bounce_grid::TouchFilter`), реплей суток по частям и CSV.
//!
//! Вход — касания из кэша ночного H3 (`--touches-from`, как у `bounce-grid`;
//! реплея касаний здесь нет — трекер тот же, порог тот же `--h3-mode
//! notional --h3-usd 10000`), наборы фильтров `--set <имя>:<k=v,…>` (те же
//! ключи), режим суток `--regime-from` для ключей `pool*`/`btc*`. Полоса
//! `--band-bps` и окна постановки `--pre-secs` — числа замера из цитат
//! практиков ([S 08:53] 0,2–0,7 %), умолчаний нет.
//!
//! Выход — `<out-dir>/<набор>/capacity-<SYMBOL>.csv`, строка на (касание,
//! тик полосы): ключ касания (`side,price_tick,start_ms` — для склейки с
//! `touches-<SYMBOL>.csv`), шаг цены и лота (доллары считает читатель),
//! возраст и размер стены, смещение фронтрана, тик и расстояние в bps,
//! очередь/наторговано/лучшие цены по слотам `t0` и каждому `pre`, момент выборки
//! очереди `t0` на тике и книга на первом кадре после (`clear_ms`,
//! `best_after_clear`/`opp_after_clear` — «стена держит» или «насквозь»). `-1` в
//! `q_*`/`best_*`/`opp_*` — книги на момент не было (постановка раньше
//! первого кадра суток), не ноль. Читатель — `tools/compute/fill-capacity.py`.
//! K1: маркер `verify-<SYMBOL>.status == ok`, иначе символ пропущен
//! (`--allow-unverified` — отладка).
//!
//! `--targets approaches` (F2 этапа F, В-73) меняет источник целей: вместо
//! касания — запись подхода (F1, `approaches-<SYMBOL>.csv` того же кэша),
//! цель `Target { start_ms: arm_ms, end_ms: disarm_ms }`: слот `t0` — поставка
//! в `arm_ms` и жизнь до снятия взвода, слоты `post` — от `arm_ms`. Колонки и
//! читатель (`leg-distance.py`) те же: `age_ms` и `size_at_touch` берутся с
//! момента взвода, `frontrun_off` пуст (фронтрана на взводе не считаем).
//! Фильтр набора применяется к «виду подхода как касания» (`touch_view_of`):
//! возраст, размер и сторона — с момента взвода, порог В-66 — по силе кадра
//! взвода (`H3Mode::holds_at_size`). Ключи контекста (`ret*`/`pool*`/`btc*`) и
//! `eaten=` у подхода не определены — `--targets approaches` с ними отказ,
//! а не молчаливый ноль.

use std::collections::BTreeMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::Instant;

use clap::Args;

use super::backtest::read_tick_step;
use super::bounce_grid::{
    cached_touches, ensure_holds_at_touch_checkable, pool_symbols, read_regime_day, touch_contexts,
    FilterSet, RegimeDay, TouchFilter,
};
use super::profiles::read_verify_marker;
use super::touches::{read_approaches_csv, ApproachRow};
use super::{resolve_h3_mode_full, session_parts_for, side_name, trade_hit_from_record, H3Args};
use crate::binlog::Reader;
use crate::book::{Book, Side};
use crate::bybit::verify::FileReplayer;
use crate::lob::capacity::{CapacityTracker, Target, TargetCapacity};
use crate::lob::levels::{ApproachRecord, TouchRecord};

/// Аргументы `lob fill-capacity`.
#[derive(Debug, Args)]
pub struct FillCapacityArgs {
    /// Корень записи (суточные `<SYMBOL>-<день>[-pN].binlog`, `session.json`,
    /// `instruments.csv`, маркеры `verify-<SYMBOL>.status`).
    #[arg(long)]
    pub root: PathBuf,
    /// Символы (повторяемый флаг); пусто — весь пул `instruments.csv` корня.
    #[arg(long = "symbol")]
    pub symbols: Vec<String>,
    /// Сутки UTC `YYYY-MM-DD` (повторяемый флаг); пусто — все сутки записи.
    #[arg(long = "day")]
    pub days: Vec<String>,
    /// Кэш касаний ночного H3: `<dir>/<сутки>/touches-<SYMBOL>.csv` (или общий
    /// `<dir>/touches-<SYMBOL>.csv`). Обязателен: замер идёт по тем же
    /// касаниям, что сетка.
    #[arg(long)]
    pub touches_from: PathBuf,
    /// Что считать целью: касание (`touches`, прежнее поведение — байты те же)
    /// или запись подхода (`approaches`): постановка в `arm_ms` и жизнь до
    /// снятия взвода. Подходы читаются из того же кэша (`--touches-from`),
    /// файл `approaches-<SYMBOL>.csv`.
    #[arg(long, value_enum, default_value_t = TargetSource::Touches)]
    pub targets: TargetSource,
    /// Набор фильтров касаний (повторяемый): `<имя>:<k=v,…>`, ключи — как у
    /// `bounce-grid --set`. Артефакты набора — `<out-dir>/<имя>/`.
    #[arg(long = "set", required = true)]
    pub sets: Vec<String>,
    /// Режим по минутам для ключей `pool*`/`btc*` (`study/regime`).
    #[arg(long)]
    pub regime_from: Option<PathBuf>,
    #[command(flatten)]
    pub h3: H3Args,
    #[arg(long)]
    pub h3_k: Option<f64>,
    /// Полоса тиков перед стеной, bps (у практиков — 70: раскидка 0,2–0,7 %).
    #[arg(long)]
    pub band_bps: i64,
    /// Окна постановки до касания, секунды, через запятую (например `1,60,300`);
    /// слот `t0` — постановка в момент касания — есть всегда.
    #[arg(long, value_delimiter = ',', required = true)]
    pub pre_secs: Vec<i64>,
    /// Окна жизни заявки после старта касания, секунды, через запятую (например
    /// `60,300`): постановка в `t0`, сделки за `[t0, t0 + post]` независимо от
    /// конца касания (пусто — слотов `post` нет).
    #[arg(long, value_delimiter = ',')]
    pub post_secs: Vec<i64>,
    /// Каталог артефактов: `<набор>/capacity-<SYMBOL>.csv`, `manifest.txt`.
    #[arg(long)]
    pub out_dir: PathBuf,
    /// Снять требование маркера сверки (отладочные данные).
    #[arg(long, default_value_t = false)]
    pub allow_unverified: bool,
}

/// Сводка прогона для строки в stdout.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct FillCapacitySummary {
    pub symbols_done: usize,
    pub symbols_skipped_unverified: usize,
    pub symbols_without_touches: usize,
    /// Символы без годного кэша касаний (`--touches-from`).
    pub symbols_without_cache: usize,
    pub symbol_days: usize,
    pub touches: u64,
    pub rows: u64,
    pub out_dir: PathBuf,
}

/// Что считать целью замера (F2 этапа F, В-73): касание (прежнее поведение —
/// байты прежние) или запись подхода.
#[derive(Debug, Clone, Copy, PartialEq, Eq, clap::ValueEnum)]
pub enum TargetSource {
    /// Касание: постановка в `start_ms`, жизнь до `end_ms`.
    Touches,
    /// Подход: постановка в `arm_ms`, жизнь до `disarm_ms`.
    Approaches,
}

/// Поля строки, которые берутся у источника цели: у касания — с момента
/// касания, у подхода — с момента взвода (`touch_view_of`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct SourceRow {
    /// Возраст уровня к моменту цели, мс.
    age_ms: i64,
    /// Размер уровня в лотах к моменту цели.
    size_at_touch: i64,
    /// Цена первого фронтранера — только у касания (у подхода пусто).
    frontrun_tick: Option<i64>,
}

impl SourceRow {
    fn of(t: &TouchRecord) -> Self {
        Self {
            age_ms: t.age_ms(),
            size_at_touch: t.size_at_touch,
            frontrun_tick: t.frontrun_tick,
        }
    }
}

/// Цель замера вместе с полями строки, из которых она построена.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Picked {
    src: SourceRow,
    target: Target,
}

/// Цели одних суток символа: вид источника как касания (для `TouchFilter` и
/// порога В-66), цели трекера и ход до момента постановки — по одному элементу
/// на цель, в одном порядке.
struct DayTargets {
    day: String,
    views: Vec<TouchRecord>,
    targets: Vec<Target>,
    rets: Vec<[Option<f64>; 3]>,
}

fn target_of(t: &TouchRecord) -> Target {
    Target {
        side: t.side,
        price_tick: t.price_tick,
        start_ms: t.start_ms,
        end_ms: t.end_ms,
    }
}

/// Вид подхода как касания: `TouchFilter` и `H3Mode::holds_at_touch` читают
/// касание, у подхода те же поля называются иначе. `start_ms` — `arm_ms`
/// (фильтры возраста и минуты режима смотрят на взвод), `size_at_touch` —
/// `size_at_arm`, `level_birth_ms` — рождение уровня, `touch_index` —
/// `approach_index`, `strength_e2` — сила кадра взвода (по ней работает порог
/// В-66), `flow_1h_lots` — оборот к взводу. Чего у подхода нет, то ноль:
/// `frontrun_tick: None` (ключ `--frontrun-only` подходы выбросит: фронтрана
/// на взводе не считаем), `traded_during` 0 и `size_max_before` = `size_at_arm`
/// (ключ `eaten=` на подходах смысла не имеет — вызов с ним отвергается),
/// стопки 0.
fn touch_view_of(a: &ApproachRecord) -> TouchRecord {
    TouchRecord {
        side: a.side,
        price_tick: a.price_tick,
        touch_index: a.approach_index,
        start_ms: a.arm_ms,
        end_ms: a.disarm_ms,
        duration_ms: a.duration_ms(),
        level_birth_ms: a.level_birth_ms,
        size_at_touch: a.size_at_arm,
        size_max_before: a.size_at_arm,
        traded_during: 0,
        frontrun_lots: 0,
        frontrun_tick: None,
        swept_lots: 0,
        round_zeros: crate::lob::levels::round_zeros(a.price_tick),
        ended_by_death: false,
        stack_levels: 0,
        stack_next_tick: None,
        traded_first_s: [0; crate::lob::levels::REACTION_WINDOWS_S.len()],
        flow_1h_lots: a.flow_1h_lots,
        strength_e2: a.strength_e2,
        strength_held_e2: [-1; crate::lob::levels::STRENGTH_HELD_WINDOWS_S.len()],
        repeat_count: 0,
    }
}

/// Цели суток из записей подхода: цель — окно взвода, вид — `touch_view_of`,
/// хода до постановки кэш подходов не несёт (`None` по всем окнам).
fn day_targets_of_approaches(day: &str, rows: &[ApproachRow]) -> DayTargets {
    DayTargets {
        day: day.to_string(),
        views: rows.iter().map(|r| touch_view_of(&r.approach)).collect(),
        targets: rows
            .iter()
            .map(|r| Target {
                side: r.approach.side,
                price_tick: r.approach.price_tick,
                start_ms: r.approach.arm_ms,
                end_ms: r.approach.disarm_ms,
            })
            .collect(),
        rets: vec![[None; 3]; rows.len()],
    }
}

/// Записи подхода символа из кэша (F1): `<dir>/<сутки>/approaches-<SYMBOL>.csv`,
/// иначе общий `<dir>/approaches-<SYMBOL>.csv`, из которого берутся строки этих
/// суток. Как `cached_touches`, но по подходам: нет файла или в суточном файле
/// чужие сутки — `Err`. Порядок строк — порядок файла.
fn cached_approaches<'a>(
    dir: &Path,
    symbol: &str,
    days: impl Iterator<Item = &'a String>,
) -> anyhow::Result<Vec<DayTargets>> {
    let flat = dir.join(format!("approaches-{symbol}.csv"));
    let mut flat_rows: Option<Vec<ApproachRow>> = None;
    let mut out = Vec::new();
    for day in days {
        let per_day = dir.join(day).join(format!("approaches-{symbol}.csv"));
        let rows: Vec<ApproachRow> = if per_day.is_file() {
            let rows = read_approaches_csv(&per_day)?;
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
                flat_rows = Some(read_approaches_csv(&flat)?);
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
        out.push(day_targets_of_approaches(day, &rows));
    }
    Ok(out)
}

/// Шапка CSV: слоты `t0`, `pre<секунды>` и `post<секунды>` в порядке трекера
/// (у `post` очередь и лучшие цены — те же, что у `t0`, поэтому только `q`/`sold`).
fn header(pre_ms: &[i64], post_ms: &[i64]) -> String {
    let mut cols = vec![
        "symbol",
        "day",
        "side",
        "price_tick",
        "tick_px",
        "lot_qty",
        "start_ms",
        "end_ms",
        "age_ms",
        "size_at_touch",
        "frontrun_off",
        "band",
        "off",
        "tick",
        "dist_bps",
        "best_t0",
        "opp_t0",
        "q_t0",
        "sold_touch",
    ]
    .into_iter()
    .map(String::from)
    .collect::<Vec<_>>();
    for p in pre_ms {
        let s = p / 1_000;
        cols.push(format!("best_pre{s}"));
        cols.push(format!("opp_pre{s}"));
        cols.push(format!("q_pre{s}"));
        cols.push(format!("sold_pre{s}"));
    }
    for p in post_ms {
        let s = p / 1_000;
        cols.push(format!("q_post{s}"));
        cols.push(format!("sold_post{s}"));
    }
    // Выборка очереди `t0` (мс от `t0`, `-1` — нет) и книга на первом кадре после.
    cols.push("clear_ms".into());
    cols.push("best_after_clear".into());
    cols.push("opp_after_clear".into());
    cols.join(",")
}

/// Постоянные строки символа-суток: имя, сутки, шаг цены и лота, число окон.
struct RowScope<'a> {
    symbol: &'a str,
    day: &'a str,
    tick_px: f64,
    lot_qty: f64,
    npre: usize,
    npost: usize,
}

/// Строки одной цели (касания или подхода): по тику полосы.
#[allow(clippy::cast_precision_loss)]
fn write_rows<W: Write>(
    w: &mut W,
    scope: &RowScope<'_>,
    src: SourceRow,
    cap: &TargetCapacity,
) -> anyhow::Result<u64> {
    let RowScope {
        symbol,
        day,
        tick_px,
        lot_qty,
        npre,
        npost,
    } = *scope;
    let t = cap.target;
    let away = match t.side {
        Side::Bid => 1,
        Side::Ask => -1,
    };
    let frontrun_off = src.frontrun_tick.map(|f| (f - t.price_tick) * away);
    let t0 = npre;
    let mut n = 0u64;
    for k in 0..=cap.band {
        let ku = k as usize;
        let dist_bps = k as f64 * 10_000.0 / t.price_tick as f64;
        write!(
            w,
            "{symbol},{day},{},{},{tick_px},{lot_qty},{},{},{},{},{},{},{k},{},{dist_bps:.3},{},{},{},{}",
            side_name(t.side),
            t.price_tick,
            t.start_ms,
            t.end_ms,
            src.age_ms,
            src.size_at_touch,
            frontrun_off.map(|v| v.to_string()).unwrap_or_default(),
            cap.band,
            cap.tick_at(k),
            cap.best(t0).ours,
            cap.best(t0).opposite,
            cap.queue(ku, t0),
            cap.sold(ku, t0),
        )?;
        for s in 0..npre {
            write!(
                w,
                ",{},{},{},{}",
                cap.best(s).ours,
                cap.best(s).opposite,
                cap.queue(ku, s),
                cap.sold(ku, s)
            )?;
        }
        for j in 0..npost {
            let s = npre + 1 + j;
            write!(w, ",{},{}", cap.queue(ku, s), cap.sold(ku, s))?;
        }
        let cl = cap.clear_ms(ku);
        let rel = if cl < 0 { -1 } else { cl - t.start_ms };
        let after = cap.best_after_clear(ku);
        write!(w, ",{rel},{},{}", after.ours, after.opposite)?;
        writeln!(w)?;
        n += 1;
    }
    Ok(n)
}

/// Реплей частей суток одной книгой (каждая часть начинается снапшотом —
/// `Book::apply` сам перезаписывает книгу) через трекеры всех наборов.
/// Разрыв последовательности останавливает часть, как в `verify`/`replay`.
fn replay_day(
    parts: &[PathBuf],
    trackers: &mut [CapacityTracker],
    tick_e9: i64,
    step_e9: i64,
) -> anyhow::Result<Book> {
    let mut book = Book::new(tick_e9, step_e9);
    for path in parts {
        let data = std::fs::read(path)
            .map_err(|e| anyhow::anyhow!("файл {} не читается: {e}", path.display()))?;
        let mut reader = Reader::open(&data[..])
            .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
        let header = reader.header();
        anyhow::ensure!(
            header.tick_e9 == tick_e9 && header.step_e9 == step_e9,
            "{}: шаг цены/лота части не совпадает с первой частью символа",
            path.display()
        );
        let mut replayer = FileReplayer::new();
        let mut ups = Vec::new();
        let mut tps = Vec::new();
        let mut file_ok = true;
        'frames: loop {
            let frame = super::parts::read_frame_soft(&mut reader, path)?;
            let Some(frame_records) = frame else { break };
            for rec in &frame_records {
                ups.clear();
                tps.clear();
                replayer.push_frame(
                    std::slice::from_ref(rec),
                    header.tick_e9,
                    header.step_e9,
                    &mut ups,
                    &mut tps,
                );
                for up in &ups {
                    for tr in trackers.iter_mut() {
                        tr.before_update(up.cts_ms, &book);
                    }
                    if book.apply(up).is_err() {
                        file_ok = false;
                        break 'frames;
                    }
                    for tr in trackers.iter_mut() {
                        tr.after_update(up.cts_ms, &book);
                    }
                }
                if let Some(h) = trade_hit_from_record(rec) {
                    for tr in trackers.iter_mut() {
                        tr.observe_trade(h, &book);
                    }
                }
            }
        }
        if file_ok {
            let mut tail = Vec::new();
            replayer.finish(&mut tail);
            for up in &tail {
                for tr in trackers.iter_mut() {
                    tr.before_update(up.cts_ms, &book);
                }
                if book.apply(up).is_err() {
                    break;
                }
                for tr in trackers.iter_mut() {
                    tr.after_update(up.cts_ms, &book);
                }
            }
        }
    }
    Ok(book)
}

pub fn run_fill_capacity(args: &FillCapacityArgs) -> anyhow::Result<FillCapacitySummary> {
    anyhow::ensure!(
        args.root.is_dir(),
        "{}: корень записи не каталог",
        args.root.display()
    );
    anyhow::ensure!(
        args.touches_from.is_dir(),
        "--touches-from {}: не каталог",
        args.touches_from.display()
    );
    anyhow::ensure!(args.band_bps > 0, "--band-bps: положительное число bps");
    anyhow::ensure!(
        args.pre_secs.iter().all(|&s| s > 0),
        "--pre-secs: положительные секунды"
    );
    anyhow::ensure!(
        args.post_secs.iter().all(|&s| s > 0),
        "--post-secs: положительные секунды"
    );
    let mut post_ms: Vec<i64> = args.post_secs.iter().map(|s| s * 1_000).collect();
    post_ms.sort_unstable();
    anyhow::ensure!(
        post_ms.windows(2).all(|w| w[0] != w[1]),
        "--post-secs: окна повторяются: {:?}",
        args.post_secs
    );
    // Слоты в CSV — в порядке трекера (по возрастанию окна).
    let mut pre_ms: Vec<i64> = args.pre_secs.iter().map(|s| s * 1_000).collect();
    pre_ms.sort_unstable();
    anyhow::ensure!(
        pre_ms.windows(2).all(|w| w[0] != w[1]),
        "--pre-secs: окна повторяются: {:?}",
        args.pre_secs
    );
    let sets = args
        .sets
        .iter()
        .map(|s| FilterSet::parse(s))
        .collect::<anyhow::Result<Vec<_>>>()?;
    {
        let names: std::collections::BTreeSet<&str> =
            sets.iter().map(|s| s.name.as_str()).collect();
        anyhow::ensure!(
            names.len() == sets.len(),
            "--set: имена наборов повторяются"
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
    if args.targets == TargetSource::Approaches {
        anyhow::ensure!(
            !sets.iter().any(|s| s.ctx.iter().any(|r| r.is_set())),
            "--targets approaches: ключи контекста (ret*/pool*/btc*) у подхода не определены — кэш подходов хода до взвода не несёт (замер — по подходам, без осей контекста)"
        );
    }
    let mut regime_days: BTreeMap<String, RegimeDay> = BTreeMap::new();
    let symbols = if args.symbols.is_empty() {
        pool_symbols(&args.root)?
    } else {
        args.symbols.clone()
    };
    std::fs::create_dir_all(&args.out_dir)?;
    for set in &sets {
        std::fs::create_dir_all(args.out_dir.join(&set.name))?;
    }
    {
        let mut m = std::fs::File::create(args.out_dir.join("manifest.txt"))?;
        writeln!(
            m,
            "# lob fill-capacity: targets={:?} band_bps={} pre_secs={:?} post_secs={:?} touches_from={} regime_from={} h3={:?} symbols={} days={:?}",
            args.targets,
            args.band_bps,
            args.pre_secs,
            args.post_secs,
            args.touches_from.display(),
            args.regime_from
                .as_deref()
                .map(|p| p.display().to_string())
                .unwrap_or_else(|| "-".into()),
            args.h3,
            symbols.len(),
            args.days
        )?;
        for spec in &args.sets {
            writeln!(m, "set={spec}")?;
        }
    }

    let mut summary = FillCapacitySummary {
        out_dir: args.out_dir.clone(),
        ..Default::default()
    };
    for symbol in &symbols {
        let marker = args.root.join(format!("verify-{symbol}.status"));
        if !args.allow_unverified && !read_verify_marker(&marker) {
            eprintln!(
                "fill-capacity: {symbol} — маркер {} не `ok`, символ пропущен (--allow-unverified для отладки)",
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
        let mode = resolve_h3_mode_full(&args.root, symbol, &args.h3, args.h3_k, tick_e9, step_e9)?;
        ensure_holds_at_touch_checkable(&mode, symbol)?;
        let mut parts_by_day: BTreeMap<String, Vec<PathBuf>> = BTreeMap::new();
        for p in &parts {
            parts_by_day
                .entry(p.day_utc.clone())
                .or_default()
                .push(p.path.clone());
        }
        // Кэша на символ нет (у ночного H3 он не считался — например, сутки без
        // маркера) — символ пропускается со счётчиком, реплея здесь нет.
        let days: Vec<DayTargets> = match args.targets {
            TargetSource::Touches => {
                match cached_touches(&args.touches_from, symbol, parts_by_day.keys(), need_ret) {
                    Ok(days) => days
                        .into_iter()
                        .map(|d| {
                            let targets: Vec<Target> = d.touches.iter().map(target_of).collect();
                            DayTargets {
                                day: d.day,
                                views: d.touches,
                                targets,
                                rets: d.rets,
                            }
                        })
                        .collect(),
                    Err(why) => {
                        eprintln!(
                        "fill-capacity: {symbol} — кэш касаний не годится ({why}), символ пропущен"
                    );
                        summary.symbols_without_cache += 1;
                        continue;
                    }
                }
            }
            TargetSource::Approaches => {
                match cached_approaches(&args.touches_from, symbol, parts_by_day.keys()) {
                    Ok(days) => days,
                    Err(why) => {
                        eprintln!(
                            "fill-capacity: {symbol} — кэш подходов не годится ({why}), символ пропущен (нужен прогон `lob touches --approach-bps`)"
                        );
                        summary.symbols_without_cache += 1;
                        continue;
                    }
                }
            }
        };
        let touches_total: usize = days.iter().map(|d| d.views.len()).sum();
        if touches_total == 0 {
            eprintln!(
                "fill-capacity: {symbol} — целей нет ({}), символ пропущен",
                match args.targets {
                    TargetSource::Touches => "касаний",
                    TargetSource::Approaches => "подходов",
                }
            );
            summary.symbols_without_touches += 1;
            continue;
        }
        let mut writers: Vec<Option<std::io::BufWriter<std::fs::File>>> =
            sets.iter().map(|_| None).collect();
        for day in &days {
            if !args.days.is_empty() && !args.days.contains(&day.day) {
                continue;
            }
            if day.views.is_empty() {
                continue;
            }
            let Some(day_parts) = parts_by_day.get(&day.day) else {
                anyhow::bail!("{symbol}: сутки {} есть в кэше, но частей нет", day.day);
            };
            let regime = if need_regime {
                let dir = args.regime_from.as_deref().expect("проверено выше");
                if !regime_days.contains_key(&day.day) {
                    regime_days.insert(day.day.clone(), read_regime_day(dir, &day.day)?);
                }
                regime_days.get(&day.day)
            } else {
                None
            };
            let ctx = touch_contexts(&day.rets, &day.views, regime);
            // Цели каждого набора — тем же фильтром, что сигналы сетки.
            let picked: Vec<Vec<Picked>> = sets
                .iter()
                .map(|set| {
                    let f = TouchFilter::from_set(set, mode, tick, lot, &ctx);
                    day.views
                        .iter()
                        .enumerate()
                        .filter(|(ti, v)| f.admits(*ti, v))
                        .map(|(ti, v)| Picked {
                            src: SourceRow::of(v),
                            target: day.targets[ti],
                        })
                        .collect()
                })
                .collect();
            if picked.iter().all(Vec::is_empty) {
                continue;
            }
            let day_started = Instant::now();
            let mut trackers: Vec<CapacityTracker> = picked
                .iter()
                .map(|p| {
                    let targets: Vec<Target> = p.iter().map(|x| x.target).collect();
                    CapacityTracker::new(&pre_ms, &post_ms, args.band_bps, &targets)
                })
                .collect::<anyhow::Result<_>>()?;
            let book = replay_day(day_parts, &mut trackers, tick_e9, step_e9)?;
            let mut day_rows = 0u64;
            let mut day_touches = 0u64;
            for (si, ((set, p), tr)) in sets.iter().zip(&picked).zip(trackers).enumerate() {
                if p.is_empty() {
                    continue;
                }
                let caps = tr.finish(&book);
                if writers[si].is_none() {
                    let path = args
                        .out_dir
                        .join(&set.name)
                        .join(format!("capacity-{symbol}.csv"));
                    let mut w = std::io::BufWriter::new(std::fs::File::create(&path)?);
                    writeln!(w, "{}", header(&pre_ms, &post_ms))?;
                    writers[si] = Some(w);
                }
                let w = writers[si].as_mut().expect("только что открыт");
                let scope = RowScope {
                    symbol,
                    day: &day.day,
                    tick_px: tick,
                    lot_qty: lot,
                    npre: pre_ms.len(),
                    npost: post_ms.len(),
                };
                for (x, cap) in p.iter().zip(&caps) {
                    day_rows += write_rows(w, &scope, x.src, cap)?;
                    day_touches += 1;
                }
            }
            summary.rows += day_rows;
            summary.touches += day_touches;
            summary.symbol_days += 1;
            eprintln!(
                "fill-capacity: {symbol} {} touches={} rows={} {:.1}s",
                day.day,
                day_touches,
                day_rows,
                day_started.elapsed().as_secs_f64()
            );
        }
        for w in writers.iter_mut().flatten() {
            w.flush()?;
        }
        summary.symbols_done += 1;
        eprintln!(
            "fill-capacity: {symbol} готов — {:.1}s",
            started.elapsed().as_secs_f64()
        );
    }
    Ok(summary)
}

#[cfg(test)]
mod tests;
