//! §13.21: таблица признаков кандидатов (`breakout tick --ftab <csv>`) и фильтр кандидатов (`fa=<имя> fo=lo|hi fv=<число>`).
//!
//! Таблица — CSV с заголовком `sym,sig_ms,<признак>,<признак>,…`: одна строка на ключ кандидата (`sym`, `sig_ms` — как в дампе `P42_SIGSX`:
//! имя монеты и время сигнала, мс от начала сетки). Пустое значение или `nan` — «нет значения». Читаются только столбцы, названные в
//! вариантах (`fa`); остальные пропускаются без разбора. Фильтр работает над списком сигналов фазы 1, ДО портфеля: порядок сигналов не
//! меняется, отброшенный кандидат не занимает слот, не расходует уровень и не запускает паузы.

use super::tick::TSig;
use anyhow::{bail, Context, Result};
use std::collections::HashMap;

/// Значения выбранных признаков по ключам кандидатов: хеш строится один раз, поиск на сигнал — один запрос без аллокаций.
pub struct FeatTab {
    keys: HashMap<(u16, i64), u32>,
    cols: Vec<(String, Vec<f64>)>,
}

/// Счётчики одного применения фильтра (диагностика, `meta.json`).
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct FilterStat {
    pub cands: u64,
    pub dropped: u64,
    /// Кандидатов без строки в таблице (проходят).
    pub no_key: u64,
    /// Кандидатов со строкой, но без значения признака (пусто / NaN; проходят).
    pub no_value: u64,
}

fn parse_val(raw: &str) -> Option<f64> {
    let t = raw.trim();
    if t.is_empty() || ["na", "none", "null"].contains(&t.to_ascii_lowercase().as_str()) {
        return Some(f64::NAN);
    }
    t.parse::<f64>().ok()
}

impl FeatTab {
    /// `text` — CSV таблицы; `sym_idx` — монета → её номер в `syms` (строки чужих монет пропускаются); `want` — нужные столбцы-признаки.
    pub fn parse(text: &str, sym_idx: &HashMap<&str, u16>, want: &[&str]) -> Result<FeatTab> {
        let mut lines = text.lines().enumerate();
        let Some((_, head)) = lines.next() else {
            bail!("таблица признаков пуста");
        };
        let head: Vec<&str> = head.trim_end_matches('\r').split(',').collect();
        if head.len() < 2 || head[0] != "sym" || head[1] != "sig_ms" {
            bail!("заголовок таблицы признаков должен начинаться с `sym,sig_ms`");
        }
        let mut pos: Vec<usize> = Vec::new();
        for w in want {
            let Some(i) = head.iter().skip(2).position(|h| h == w) else {
                bail!("в таблице признаков нет столбца {w}");
            };
            pos.push(i + 2);
        }
        let mut cols: Vec<(String, Vec<f64>)> = want
            .iter()
            .map(|w| ((*w).to_string(), Vec::new()))
            .collect();
        let mut keys: HashMap<(u16, i64), u32> = HashMap::new();
        let need = pos.iter().max().map_or(2, |m| m + 1);
        for (n, line) in lines {
            let line = line.trim_end_matches('\r');
            if line.is_empty() {
                continue;
            }
            let f: Vec<&str> = line.split(',').collect();
            if f.len() < need {
                bail!(
                    "таблица признаков, строка {}: полей {} < {need}",
                    n + 1,
                    f.len()
                );
            }
            let Some(&si) = sym_idx.get(f[0]) else {
                continue;
            };
            let ms: i64 = f[1]
                .trim()
                .parse()
                .with_context(|| format!("таблица признаков, строка {}: sig_ms={}", n + 1, f[1]))?;
            let row = u32::try_from(cols.first().map_or(0, |c| c.1.len()))
                .context("таблица признаков: больше 2^32 строк")?;
            for (c, &p) in cols.iter_mut().zip(&pos) {
                let Some(v) = parse_val(f[p]) else {
                    bail!(
                        "таблица признаков, строка {}: {}={} — не число",
                        n + 1,
                        c.0,
                        f[p]
                    );
                };
                c.1.push(v);
            }
            keys.insert((si, ms), row);
        }
        Ok(FeatTab { keys, cols })
    }

    /// Номер столбца-признака.
    pub fn col(&self, name: &str) -> Option<usize> {
        self.cols.iter().position(|c| c.0 == name)
    }

    /// Значение признака `c` кандидата (`sym`, `ms`); `None` — строки с таким ключом нет.
    pub fn value(&self, c: usize, sym: u16, ms: i64) -> Option<f64> {
        let r = *self.keys.get(&(sym, ms))?;
        Some(self.cols[c].1[r as usize])
    }

    pub fn rows(&self) -> usize {
        self.keys.len()
    }
}

/// Кандидат проходит: нет значения (нет строки / NaN) — проходит; `lo` отбрасывает `value < fv`, `hi` — `value > fv`.
pub fn passes(value: Option<f64>, hi: bool, fv: f64) -> bool {
    match value {
        None => true,
        Some(x) if x.is_nan() => true,
        Some(x) => !(if hi { x > fv } else { x < fv }),
    }
}

/// Список сигналов без отброшенных кандидатов (порядок сохраняется) и счётчики. `c` — столбец признака, `hi` — `fo=hi`, `fv` — порог.
pub fn filter_sigs(
    sigs: &[TSig],
    tab: &FeatTab,
    c: usize,
    hi: bool,
    fv: f64,
) -> (Vec<TSig>, FilterStat) {
    let mut st = FilterStat::default();
    let mut out = Vec::with_capacity(sigs.len());
    for sg in sigs {
        st.cands += 1;
        let v = tab.value(c, sg.sym, sg.ms);
        match v {
            None => st.no_key += 1,
            Some(x) if x.is_nan() => st.no_value += 1,
            _ => {}
        }
        if passes(v, hi, fv) {
            out.push(*sg);
        } else {
            st.dropped += 1;
        }
    }
    (out, st)
}
