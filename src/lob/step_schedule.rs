//! Расписание действующих шагов цены и лота внутри суток (TK-037, В-172).
//!
//! Файл суток (binlog v4) хранит книгу в тиках и лотах **мельчайшей** сетки
//! суток (заголовок), а шаги, действовавшие на бирже, — списком `StepAt`.
//! Симулятор ставит ордера только на действующей сетке: до смены шага — на
//! старой (грубой) цене и кратно старому лоту, после — на новой. Этот модуль —
//! чистый поиск «какой шаг действовал в момент `t`»; сборка плана по нему —
//! `commands::lob::backtest::plan::bounce_plan_sched`.

use crate::binlog::StepAt;

/// Шаги суток по времени: непустой список, метки строго по возрастанию, каждый
/// шаг положителен и кратен сетке данных.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StepSchedule {
    entries: Vec<StepAt>,
}

impl StepSchedule {
    /// По заголовку суток (`tick_e9`/`step_e9` — сетка данных) и расписанию из
    /// файла (`Reader::step_schedule()`). Пустое расписание (файл v2/v3) — один
    /// шаг на всё время, равный сетке. Шаг не кратен сетке, не положителен или
    /// метки не по возрастанию — ошибка.
    pub fn from_header(tick_e9: i64, step_e9: i64, schedule: &[StepAt]) -> Result<Self, String> {
        if tick_e9 <= 0 || step_e9 <= 0 {
            return Err(format!(
                "сетка данных не положительна: tick_e9={tick_e9} step_e9={step_e9}"
            ));
        }
        if schedule.is_empty() {
            return Ok(Self {
                entries: vec![StepAt {
                    ts_ns: i64::MIN,
                    tick_e9,
                    step_e9,
                }],
            });
        }
        let mut prev_ts: Option<i64> = None;
        for (i, a) in schedule.iter().enumerate() {
            if a.tick_e9 <= 0 || a.step_e9 <= 0 {
                return Err(format!("запись {i}: шаг не положителен"));
            }
            if a.tick_e9 % tick_e9 != 0 || a.step_e9 % step_e9 != 0 {
                return Err(format!(
                    "запись {i}: шаг ({}, {}) не кратен сетке данных ({tick_e9}, {step_e9})",
                    a.tick_e9, a.step_e9
                ));
            }
            if prev_ts.is_some_and(|p| p >= a.ts_ns) {
                return Err(format!("запись {i}: метки времени не по возрастанию"));
            }
            prev_ts = Some(a.ts_ns);
        }
        Ok(Self {
            entries: schedule.to_vec(),
        })
    }

    /// Шаги `(tick_e9, step_e9)`, действующие в момент `ts_ns`: последняя запись
    /// с меткой не позже `ts_ns`; до первой записи — первая. Бинарный поиск, без
    /// аллокаций.
    pub fn at(&self, ts_ns: i64) -> (i64, i64) {
        let n = self.entries.partition_point(|a| a.ts_ns <= ts_ns);
        let a = &self.entries[n.saturating_sub(1)];
        (a.tick_e9, a.step_e9)
    }
}

#[cfg(test)]
mod tests;
