//! Разметка подтверждающей выборки и гейт G1 (план, §5.1).
//!
//! Подтверждающая выборка — ровно сутки из `ready.flag` (Decision 21): запись
//! после флага продолжается, но эти сутки в анализ не входят и остаются
//! отложенной выборкой. Без флага подтверждающий markout отказывается
//! запускаться — этот отказ уже есть в `watch::require_ready_flag`, здесь он
//! переиспользуется, а не изобретается заново.
//!
//! Поток шага: записи `LevelRecord` (шаг 1.1) → исход `Outcome` по правилу
//! 70/20 (шаг 1.2, `LevelRecord::outcome`) → markout на 10 с
//! (`markout::markouts_for_level`, горизонт ячейки C1/C2 из Decision 16) →
//! распределение по классам → вердикт G1. Вердикт читает только доли исходов;
//! сам markout считается как свидетельство, что цепочка дошла до конца, и его
//! покрытие (есть/нет будущего на 10 с) печатается рядом, в гейт не входит.
//!
//! Что переиспользуется, а не дублируется:
//!
//! - `watch::day_eligible` — единственный предикат годности суток на весь
//!   документ (Decision 21). Своей копии здесь нет.
//! - `watch::require_ready_flag` / `ReadyFlag` — механическая защита от
//!   optional stopping (шаг 4.1). Своей проверки флага здесь нет.
//! - Правило 70/20 — `LevelRecord::outcome` (шаг 1.2). Своей классификации
//!   здесь нет.
//!
//! Границы суток — как у `watch`: сутки после флага молча пропускаются (это
//! отложенная выборка, а не ошибка), а отсутствие суток из флага, чужой символ
//! (состояние на один символ — по инстансу на кандидата пула), дубликат суток
//! или негодные сутки из списка флага — ошибка: флаг по построению содержит
//! только годные сутки с наблюдением, и расхождение есть дефект счётчика, а
//! не исход гейта.
//!
//! Красный G1 — методический: порог `H3` или правило 70/20 неудачны, шаг 1
//! переделывается один раз, обе попытки фиксируются в `runs.csv`. Сам файл
//! `runs.csv` ведёт шаг 7.1, не этот модуль: здесь только вердикт, который ту
//! запись требует.

use std::collections::BTreeSet;
use std::path::Path;

use crate::binlog::{BinlogError, Reader, Record};
use crate::lob::levels::{LevelRecord, Outcome};
use crate::lob::markout::{markouts_for_level, MidSample, HORIZONS_MS};
use crate::lob::watch::{day_eligible, require_ready_flag, DayTally, ReadyFlag, WatchError};

// ---------------------------------------------------------------------------
// Константы гейта G1. Каждое число — из формулировки гейта в плане.
// ---------------------------------------------------------------------------

/// Числитель порога G1: доля класса обязана быть не меньше этой дроби.
pub const G1_MIN_SHARE_NUM: u64 = 1;

/// Знаменатель порога G1: `1/20` есть ровно 5%. Граница включительная:
/// ровно 5% — ещё годно (`>=`, не `>`).
pub const G1_MIN_SHARE_DEN: u64 = 20;

/// Числитель ориентира «ожидаемый мир снятия»: `9/10` есть 90%.
pub const PULLED_DOMINANT_NUM: u64 = 9;

/// Знаменатель того же ориентира.
pub const PULLED_DOMINANT_DEN: u64 = 10;

/// Позиция горизонта 10 с в `HORIZONS_MS` (100 мс, 1 с, 10 с, 60 с).
/// Горизонт ячейки из Decision 16; индекс прибит тестом ниже к значению.
const MARKOUT_10S_INDEX: usize = 2;

/// Миллионных долей в целом: доли печатаются целыми ppm, без дробных чисел.
const PPM_UNIT: u128 = 1_000_000;

// ---------------------------------------------------------------------------
// Ошибки. Один тип на весь модуль, в стиле `watch.rs`: отказ с причиной.
// ---------------------------------------------------------------------------

/// Отказ разметки. Ни один вариант не паникует.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MarkupError {
    /// Флаг отсутствует или испорчен — переиспользованный отказ `watch`.
    Flag(WatchError),
    /// Кадр бинлога не разобрался при сливе через `Reader`.
    Binlog(BinlogError),
    /// Чужой символ при состоянии на один символ — по инстансу на каждого
    /// кандидата пула.
    SymbolMismatch { expected: String, got: String },
    /// Сутки из списка флага негодны по общему предикату: флаг по построению
    /// годных суток такое содержать не может — дефект счётчика, не исход.
    DayNotEligible { day: String },
    /// Сутки из флага не поданы: выборка обязана быть ровно списком флага.
    MissingDay { day: String },
    /// Те же сутки поданы дважды.
    DuplicateDay { day: String },
}

impl std::fmt::Display for MarkupError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            MarkupError::Flag(e) => write!(f, "флаг: {e}"),
            MarkupError::Binlog(e) => write!(f, "бинлог: {e}"),
            MarkupError::SymbolMismatch { expected, got } => {
                write!(f, "чужой символ: флаг на {expected}, подали {got}")
            }
            MarkupError::DayNotEligible { day } => {
                write!(f, "сутки {day} из флага негодны: дефект счётчика")
            }
            MarkupError::MissingDay { day } => {
                write!(f, "сутки {day} из флага не поданы: выборка неполна")
            }
            MarkupError::DuplicateDay { day } => write!(f, "сутки {day} поданы дважды"),
        }
    }
}

impl std::error::Error for MarkupError {}

impl From<WatchError> for MarkupError {
    fn from(e: WatchError) -> Self {
        MarkupError::Flag(e)
    }
}

impl From<BinlogError> for MarkupError {
    fn from(e: BinlogError) -> Self {
        MarkupError::Binlog(e)
    }
}

// ---------------------------------------------------------------------------
// Распределение по классам исхода (шаг 1.2 — потребитель).
// ---------------------------------------------------------------------------

/// Число уровней по классам `eaten`/`pulled`/`mixed` и итог.
/// Единица наблюдений — `LevelRecord` из `levels.rs`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct ClassCounts {
    /// Всего уровней в выборке.
    pub total: u64,
    /// Съедены: объём против уровня ≥ 70% максимума.
    pub eaten: u64,
    /// Сняты: объём против уровня ≤ 20% максимума.
    pub pulled: u64,
    /// Между 20% и 70%.
    pub mixed: u64,
}

fn share_ppm(part: u64, total: u64) -> Option<u64> {
    if total == 0 {
        return None;
    }
    // Итоговый каст точен: частное — доля в миллионных (≤ 1e6) по построению.
    #[allow(clippy::cast_possible_truncation)]
    let ppm = (part as u128 * PPM_UNIT / total as u128) as u64;
    Some(ppm)
}

impl ClassCounts {
    /// Считает распределение из записей правилом 70/20 шага 1.2.
    /// Классификация — `LevelRecord::outcome`, своей здесь нет.
    pub fn from_records(records: &[LevelRecord]) -> Self {
        let mut counts = Self::default();
        for rec in records {
            counts.add(rec.outcome());
        }
        counts
    }

    /// Добавляет один исход. Сложение с насыщением, как счётчики `watch`.
    pub fn add(&mut self, outcome: Outcome) {
        self.total = self.total.saturating_add(1);
        match outcome {
            Outcome::Eaten => self.eaten = self.eaten.saturating_add(1),
            Outcome::Pulled => self.pulled = self.pulled.saturating_add(1),
            Outcome::Mixed => self.mixed = self.mixed.saturating_add(1),
        }
    }

    /// Доля `eaten` в миллионных долях целыми. `None` — уровней нет.
    pub fn eaten_share_ppm(&self) -> Option<u64> {
        share_ppm(self.eaten, self.total)
    }

    /// Доля `pulled` в миллионных долях целыми. `None` — уровней нет.
    pub fn pulled_share_ppm(&self) -> Option<u64> {
        share_ppm(self.pulled, self.total)
    }

    /// Доля `mixed` в миллионных долях целыми. `None` — уровней нет.
    pub fn mixed_share_ppm(&self) -> Option<u64> {
        share_ppm(self.mixed, self.total)
    }

    /// Распределение одной строкой — то «напечатано» из done-condition 5.1.
    /// Библиотечный код в stdout не пишет: печатает вызывающий (`lob`).
    /// Доли — целыми ppm рядом со счётчиками, без дробных чисел.
    pub fn format_distribution(&self) -> String {
        match (self.eaten_share_ppm(), self.pulled_share_ppm()) {
            (Some(e), Some(p)) => format!(
                "total={} eaten={} ({} ppm) pulled={} ({} ppm) mixed={}",
                self.total, self.eaten, e, self.pulled, p, self.mixed
            ),
            (None, None) => {
                "total=0 eaten=0 pulled=0 mixed=0 (долей нет: выборка пуста)".to_string()
            }
            _ => format!(
                "total={} eaten={} pulled={} mixed={}",
                self.total, self.eaten, self.pulled, self.mixed
            ),
        }
    }
}

impl std::fmt::Display for ClassCounts {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.format_distribution())
    }
}

// ---------------------------------------------------------------------------
// Гейт G1 — дословно по формулировке плана.
// ---------------------------------------------------------------------------

/// Вердикт гейта G1.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum G1Verdict {
    /// Доли `eaten` и `pulled` обе ≥ 5%.
    Pass,
    /// Хотя бы одна доля ниже 5% (включая пустую выборку): красный
    /// по методике — шаг 1 переделывается один раз, обе попытки в `runs.csv`.
    RedMethodology,
}

impl G1Verdict {
    /// Проход — зелёный свет дальше по подтверждающему прогону.
    pub fn is_pass(self) -> bool {
        matches!(self, G1Verdict::Pass)
    }
}

impl std::fmt::Display for G1Verdict {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            G1Verdict::Pass => write!(f, "G1 pass: доли eaten и pulled обе >= 5%"),
            G1Verdict::RedMethodology => write!(
                f,
                "G1 RED (методика): доля eaten или pulled ниже 5% — шаг 1 переделать один раз"
            ),
        }
    }
}

/// Решение G1 строго целочисленно, без деления: `n * 20 >= total`
/// есть доля ≥ 5% с включительной границей. Пустая выборка — красный:
/// долей нет, и требовать их не с чего.
pub fn decide_g1(counts: &ClassCounts) -> G1Verdict {
    if counts.total == 0 {
        return G1Verdict::RedMethodology;
    }
    let num = G1_MIN_SHARE_NUM as u128;
    let den = G1_MIN_SHARE_DEN as u128;
    let total = counts.total as u128;
    let eaten_ok = counts.eaten as u128 * den >= total * num;
    let pulled_ok = counts.pulled as u128 * den >= total * num;
    if eaten_ok && pulled_ok {
        G1Verdict::Pass
    } else {
        G1Verdict::RedMethodology
    }
}

/// Доля `pulled` ≥ 90%: ожидаемый мир, где сигнал живёт в снятии.
/// Гипотезу не закрывает — отдельного красного по высокому `pulled` нет,
/// решает только `decide_g1`. Нужно отчёту как пометка, не гейту.
pub fn pulled_dominant(counts: &ClassCounts) -> bool {
    if counts.total == 0 {
        return false;
    }
    counts.pulled as u128 * PULLED_DOMINANT_DEN as u128
        >= counts.total as u128 * PULLED_DOMINANT_NUM as u128
}

// ---------------------------------------------------------------------------
// Подтверждающий прогон: сутки флага через уровни, исход и markout.
// ---------------------------------------------------------------------------

/// Одни сутки подтверждающей выборки: счётчик качества (`watch`),
/// записи уровней (`levels`) и срезы середины (`markout`).
pub struct ConfirmatoryDay<'a> {
    /// Счётчик суток: символ, дата, качество verify/разрывов.
    pub tally: &'a DayTally,
    /// Записи уровней этих суток.
    pub records: &'a [LevelRecord],
    /// Срезы середины этих суток в неубывающем времени.
    pub mids: &'a [MidSample],
}

/// Итог подтверждающего прогона 5.1.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ConfirmatoryReport {
    /// Замороженный снимок момента анализа из флага.
    pub flag: ReadyFlag,
    /// Сутки, вошедшие в выборку, по возрастанию — обязаны совпасть
    /// со списком флага.
    pub days_used: Vec<String>,
    /// Распределение по классам — done-condition 5.1.
    pub counts: ClassCounts,
    /// Записей с будущим на 10 с (markout посчитался).
    pub markout_10s_available: u64,
    /// Записей без будущего на 10 с (markout — `None`, не ноль).
    pub markout_10s_missing: u64,
    /// Вердикт гейта G1.
    pub verdict: G1Verdict,
}

impl ConfirmatoryReport {
    /// Одна строка итога: распределение, покрытие markout и вердикт.
    pub fn summary_line(&self) -> String {
        format!(
            "{} markout_10s=available:{} missing:{} {}",
            self.counts.format_distribution(),
            self.markout_10s_available,
            self.markout_10s_missing,
            self.verdict
        )
    }
}

/// Прогоняет подтверждающую выборку через уровни, исход и markout.
///
/// Первым делом читает флаг: без флага — отказ переиспользованной ошибкой
/// `watch` (вызывающий завершается ненулевым кодом), до всякого markout.
/// Дальше берёт ровно сутки из списка флага: сутки после флага пропускаются
/// как отложенная выборка, отсутствие суток из флага — ошибка. Годность
/// каждого взятого дня проверяется общим `day_eligible`.
pub fn run_confirmatory(
    flag_path: &Path,
    days: &[ConfirmatoryDay<'_>],
) -> Result<ConfirmatoryReport, MarkupError> {
    let flag = require_ready_flag(flag_path).map_err(MarkupError::Flag)?;
    debug_assert_eq!(
        HORIZONS_MS[MARKOUT_10S_INDEX], 10_000,
        "индекс горизонта 10 с обязан указывать на 10 000 мс"
    );
    let wanted: BTreeSet<&str> = flag.days.iter().map(String::as_str).collect();
    let mut seen: BTreeSet<&str> = BTreeSet::new();
    let mut counts = ClassCounts::default();
    let mut available: u64 = 0;
    let mut missing: u64 = 0;
    let mut days_used: Vec<String> = Vec::new();
    for day in days {
        let key = day.tally.day_utc.as_str();
        if !wanted.contains(key) {
            continue;
        }
        if !seen.insert(key) {
            return Err(MarkupError::DuplicateDay {
                day: key.to_string(),
            });
        }
        if day.tally.symbol != flag.symbol {
            return Err(MarkupError::SymbolMismatch {
                expected: flag.symbol.clone(),
                got: day.tally.symbol.clone(),
            });
        }
        if !day_eligible(day.tally) {
            return Err(MarkupError::DayNotEligible {
                day: key.to_string(),
            });
        }
        days_used.push(key.to_string());
        for rec in day.records {
            counts.add(rec.outcome());
            match markouts_for_level(rec, day.mids)[MARKOUT_10S_INDEX] {
                Some(_) => available = available.saturating_add(1),
                None => missing = missing.saturating_add(1),
            }
        }
    }
    for want in &flag.days {
        if !seen.contains(want.as_str()) {
            return Err(MarkupError::MissingDay { day: want.clone() });
        }
    }
    let verdict = decide_g1(&counts);
    Ok(ConfirmatoryReport {
        flag,
        days_used,
        counts,
        markout_10s_available: available,
        markout_10s_missing: missing,
        verdict,
    })
}

// ---------------------------------------------------------------------------
// Тонкий шов бинлог → разметка: слив кадров `Reader` в записи.
// ---------------------------------------------------------------------------

/// Сливает все кадры читателя в один вектор записей.
/// Полный реплей книга → уровни (шаг 1.1) — дело вызывающего, не этого
/// модуля: здесь только чтение суточного файла тем же `Reader`, которым его
/// писали, без толкования событий.
pub fn drain_binlog_records<R: std::io::Read>(
    reader: &mut Reader<R>,
) -> Result<Vec<Record>, BinlogError> {
    let mut out = Vec::new();
    while let Some(frame) = reader.read_frame()? {
        out.extend_from_slice(&frame);
    }
    Ok(out)
}

#[cfg(test)]
mod tests;
