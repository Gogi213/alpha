//! `lob export` — выгрузка суточных файлов в `npy` для крейта `hftbacktest`
//! (шаг 6.1 плана, Decision 17).
//!
//! Читает все `<SYMBOL>-*.binlog` из корня записи (каждый самодостаточен:
//! масштаб цены и размера несёт его собственный заголовок) и пишет один `npy`
//! писателем самого крейта (`write_npy`), своим писателем не обзаводится,
//! список зависимостей не растёт.
//!
//! # Маппинг `Record` → `Event`
//!
//! Метки, `ev` и цена с размером переносятся один в один (`px`/`qty` крейта
//! возвращаются из целых в его масштаб через `tick_e9`/`step_e9` заголовка
//! того файла, откуда взята запись: `px = price_ticks * tick_e9 / 1e9`,
//! `qty = qty_lots * step_e9 / 1e9`). Флаги `ev` (`DEPTH`/`TRADE`,
//! `SNAPSHOT`/`CLEAR`, `BUY`/`SELL`) привратником не трогаются — пишутся как
//! сырые события, разбирает их потребитель. Блочность записи (`block`)
//! возвращается в `ival` крейта: `ival = 1` — блочная сделка, как её понимает
//! downstream. `order_id = 0` и `fval = 0.0` — поток L2: числового id заявки
//! биржа в публичном канале не сообщает, а `fval` не несёт информации ни в
//! одной записи живой записи, поэтому в формате v3 этих полей и нет
//! (`docs/findings/binlog-format-2026-09-13.md`).
//!
//! # Дефектные события
//!
//! Инвариант `exch_ts < local_ts` проверяется на **каждом** событии строго.
//! Нарушение делает событие дефектным: оно не попадает в `npy`, а считается
//! в отдельный счётчик `defective` итога, который печатает диспетчер, —
//! молча не теряется ни одно. Поэтому вышедший файл держит 100% инварианта
//! по построению, что и требует done-condition шага.

use std::fs::File;
use std::io::Write;
use std::path::{Path, PathBuf};

use hftbacktest::types::Event;

use crate::binlog::{Reader, Record};

/// Делитель масштаба 1e-9: заголовок несёт шаги в миллиардных долях.
const E9: f64 = 1_000_000_000.0;

/// Аргументы `lob export`: корень суточных файлов, символ, выходной `npy`.
#[derive(Debug, Clone, clap::Args)]
pub struct ExportArgs {
    /// Корень записи: суточные файлы вида `<SYMBOL>-<день>.binlog`.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Символ, например `SOLUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// Куда писать `npy` (родитель создаётся).
    #[arg(long)]
    pub out: PathBuf,
}

/// Итог `lob export`: сколько файлов слито, сколько событий вышло в `npy`,
/// сколько отбраковано дефектом (счётчик, не тишина) и куда записано.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ExportSummary {
    pub files: usize,
    pub events: u64,
    pub defective: u64,
    pub out: PathBuf,
}

/// Перевод одной записи в масштаб крейта. Масштаб берут из заголовка того
/// файла, откуда запись прочитана: у частей суток после смены шагов он свой.
/// Касты точные: тики и лоты — целые порядков единиц–миллионов, шаги — 1e-9
/// порядков до миллиардов; всё далеко от 2^53. Граница A7 — здесь.
#[allow(clippy::cast_precision_loss)]
pub fn record_to_event(r: &Record, tick_e9: i64, step_e9: i64) -> Event {
    debug_assert!(
        tick_e9 > 0 && step_e9 > 0,
        "масштаб из заголовка обязан быть положительным — проверено читателем"
    );
    Event {
        ev: r.ev,
        exch_ts: r.exch_ts_ns,
        local_ts: r.local_ts_ns,
        px: r.price_ticks as f64 * tick_e9 as f64 / E9,
        qty: r.qty_lots as f64 * step_e9 as f64 / E9,
        order_id: 0,
        ival: i64::from(r.block),
        fval: 0.0,
    }
}

/// Дефект шага 6.1: метка биржи не строго раньше локальной. Строгое `<`,
/// не `<=`: done-condition требует строгого на 100% записей.
pub fn is_defective(ev: &Event) -> bool {
    ev.exch_ts >= ev.local_ts
}

/// Ключ хронологического порядка суточных файлов: день UTC, затем часть
/// суток (`-p2` после смены шагов). Голая лексикография здесь врёт: `-`
/// (0x2D) меньше `.` (0x2E), и `SYM-день-p2.binlog` встал бы раньше
/// `SYM-день.binlog`, то есть хвост суток — раньше их начала, а данные
/// крейту нужны по времени. Имя после префикса символа — либо `день`,
/// либо `день-pN`; всё нераспознанное считается частью 1 того же имени.
fn part_order_key(prefix: &str, name: &str) -> (String, u32, String) {
    let rest = name.strip_prefix(prefix).unwrap_or(name);
    let rest = rest.strip_suffix(".binlog").unwrap_or(rest);
    if let Some(tail) = rest.get(10..) {
        if let Some(num) = tail.strip_prefix("-p") {
            if let Ok(part) = num.parse::<u32>() {
                return (rest[..10].to_string(), part, name.to_string());
            }
        }
    }
    (rest.to_string(), 1, name.to_string())
}

/// Выгрузка всех суточных файлов символа в один `npy` через `write_npy`
/// самого крейта. Файлы идут в хронологическом порядке (`part_order_key`:
/// день, затем часть); порядок записей внутри файлов не меняется.
/// Дефектные события в выход не попадают, их число — в итоге.
pub fn run_export(args: &ExportArgs) -> anyhow::Result<ExportSummary> {
    let prefix = format!("{}-", args.symbol);
    let entries = std::fs::read_dir(&args.root)
        .map_err(|e| anyhow::anyhow!("корень {} не читается: {e}", args.root.display()))?;
    let mut files: Vec<PathBuf> = Vec::new();
    for e in entries {
        let e = e.map_err(|e| anyhow::anyhow!("запись каталога не читается: {e}"))?;
        let name = e.file_name().to_string_lossy().into_owned();
        if name.starts_with(&prefix) && name.ends_with(".binlog") {
            files.push(e.path());
        }
    }
    let file_name = |p: &PathBuf| {
        p.file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default()
    };
    files.sort_by(|a, b| {
        part_order_key(&prefix, &file_name(a)).cmp(&part_order_key(&prefix, &file_name(b)))
    });
    if files.is_empty() {
        anyhow::bail!(
            "нет суточных файлов {}-*.binlog в {}",
            args.symbol,
            args.root.display()
        );
    }

    let mut events: Vec<Event> = Vec::new();
    let mut defective: u64 = 0;
    for path in &files {
        export_one_file(path, &mut events, &mut defective)?;
    }

    if let Some(parent) = args.out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)
                .map_err(|e| anyhow::anyhow!("каталог {} не создан: {e}", parent.display()))?;
        }
    }
    let mut out = File::create(&args.out)
        .map_err(|e| anyhow::anyhow!("файл {} не создан: {e}", args.out.display()))?;
    hftbacktest::backtest::data::write_npy(&mut out, &events)
        .map_err(|e| anyhow::anyhow!("запись {}: {e}", args.out.display()))?;
    out.flush()
        .map_err(|e| anyhow::anyhow!("сброс {}: {e}", args.out.display()))?;

    Ok(ExportSummary {
        files: files.len(),
        events: events.len() as u64,
        defective,
        out: args.out.clone(),
    })
}

/// Долив одного суточного файла в общий буфер. Масштаб — из заголовка этого
/// же файла, дефектные события считает в `defective` и пропускает.
fn export_one_file(path: &Path, out: &mut Vec<Event>, defective: &mut u64) -> anyhow::Result<()> {
    let data = std::fs::read(path)
        .map_err(|e| anyhow::anyhow!("файл {} не читается: {e}", path.display()))?;
    let mut reader = Reader::open(&data[..])
        .map_err(|e| anyhow::anyhow!("заголовок {}: {e:?}", path.display()))?;
    let header = reader.header();
    loop {
        let frame = reader
            .read_frame()
            .map_err(|e| anyhow::anyhow!("кадр {}: {e:?}", path.display()))?;
        let Some(records) = frame else { break };
        for r in &records {
            let ev = record_to_event(r, header.tick_e9, header.step_e9);
            if is_defective(&ev) {
                *defective += 1;
                continue;
            }
            out.push(ev);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests;
