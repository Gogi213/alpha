//! `lob power` — гейт G-POWER-A (`PLAN.md` §6, история 43): планка до сбора,
//! ноль сетевых данных. Печатает три числа — фактическое `N` из шага отбора,
//! `SR0` и требуемый Шарп на наблюдение для DSR = 0.95 при `n = 100` — сама
//! статистика (`SR0`, требуемый Шарп) считается `crate::lob::final_metrics`,
//! здесь только чтение пула, число испытаний (`crate::lob::shortlist`) и
//! печать. Один вызов инструмента (история 43: «каждое число — одной
//! командой»), не юнит-тест.

use std::path::PathBuf;

use clap::Args;

use crate::commands::record::instruments_csv_path;
use crate::lob::final_metrics::{
    expected_sharpe_under_null_for_trial_count, required_sharpe_for_dsr,
};
use crate::lob::shortlist::{nominal_grid_size, total_trials};

/// Гейт G-POWER-A фиксирует DSR и число наблюдений статистического
/// контракта (`PLAN.md` §6, раздел о статистическом контракте) — не
/// подбираются здесь, печатаются вместе с числами, которые от них зависят.
const POWER_DSR_TARGET: f64 = 0.95;
const POWER_NUM_OBS: usize = 100;

/// Аргументы `lob power`: только пул (`instruments.csv` корня записи) и,
/// по желанию, число тестов на зависимость от часа суток сверх сетки
/// профилей (`shortlist::total_trials`) — обе оси реального числа испытаний.
#[derive(Debug, Args)]
pub struct PowerArgs {
    /// Корень записи с `instruments.csv` (пул, который берёт `lob pick`).
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// Число тестов на час поверх сетки профилей (Decision таска 06,
    /// `shortlist::total_trials`) — по умолчанию 0: без замера часовой
    /// зависимости число испытаний состоит только из сетки профилей.
    #[arg(long, default_value_t = 0)]
    pub hour_tests: usize,
}

/// Итог `lob power`: три числа гейта G-POWER-A плюс параметры, при которых
/// они посчитаны — таск 09 сравнивает с ними замеренный Шарп (G-POWER-B),
/// таск 13 печатает их в шапке шорт-листа.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PowerSummary {
    /// Фактическое число испытаний (сетка профилей плюс тесты на час).
    pub n: usize,
    /// Ожидаемый Sharpe лучшего из `n` испытаний под нулём.
    pub sr0: f64,
    /// Требуемый Шарп на наблюдение для `dsr_target` при `num_obs`.
    pub required_sharpe: f64,
    /// DSR, для которого посчитан требуемый Шарп (`POWER_DSR_TARGET`).
    pub dsr_target: f64,
    /// Число наблюдений, для которого посчитан требуемый Шарп (`POWER_NUM_OBS`).
    pub num_obs: usize,
}

/// Число инструментов пула из `instruments.csv`: колонка `symbol` — одна
/// строка на инструмент, покрытие сюда не требуется (таск 08 ещё не пишет
/// эту колонку) — номинальная сетка (`nominal_grid_size`) не требует
/// ничего сверх числа инструментов пула.
fn pool_size(instruments_csv: &std::path::Path) -> anyhow::Result<usize> {
    // Единственный читатель `instruments.csv` (дозапрос по ревью таска 08,
    // ось Craft) — терпит метку `debug` первой строкой, голый
    // `csv::Reader::from_path` читал бы её как заголовок и падал здесь же.
    let mut r = super::pick::instruments_csv_reader(instruments_csv).map_err(|e| {
        anyhow::anyhow!(
            "{}: {e} — lob power читает пул из instruments.csv (пишет lob pick)",
            instruments_csv.display()
        )
    })?;
    let headers = r.headers()?.clone();
    anyhow::ensure!(
        headers.iter().any(|h| h == "symbol"),
        "{}: нет колонки symbol",
        instruments_csv.display()
    );
    let n = r
        .records()
        .try_fold(0usize, |acc, row| row.map(|_| acc + 1))?;
    anyhow::ensure!(n > 0, "{}: пул пуст", instruments_csv.display());
    Ok(n)
}

/// Считает три числа гейта G-POWER-A. `N` — из одного источника
/// (`shortlist::nominal_grid_size` на пуле `instruments.csv`, плюс
/// `--hour-tests` через `shortlist::total_trials`), никакой другой счётчик
/// испытаний сюда не подмешивается.
pub fn run_power(args: &PowerArgs) -> anyhow::Result<PowerSummary> {
    let n_instruments = pool_size(&instruments_csv_path(&args.root))?;
    let grid_len = nominal_grid_size(n_instruments);
    let n = total_trials(grid_len, args.hour_tests);
    let sr0 = expected_sharpe_under_null_for_trial_count(n)
        .ok_or_else(|| anyhow::anyhow!("SR0 не считается для N={n}"))?;
    let required_sharpe = required_sharpe_for_dsr(n, POWER_NUM_OBS, POWER_DSR_TARGET)
        .ok_or_else(|| anyhow::anyhow!("требуемый Шарп не считается для N={n}"))?;
    Ok(PowerSummary {
        n,
        sr0,
        required_sharpe,
        dsr_target: POWER_DSR_TARGET,
        num_obs: POWER_NUM_OBS,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write_instruments_csv(path: &std::path::Path, symbols: &[&str]) {
        let mut w = csv::Writer::from_path(path).unwrap();
        w.write_record([
            "symbol",
            "tick_size",
            "min_order_qty",
            "qty_step",
            "min_notional_value",
        ])
        .unwrap();
        for s in symbols {
            w.write_record([*s, "0.1", "1", "1", "5"]).unwrap();
        }
        w.flush().unwrap();
    }

    /// Критерий приёмки таска 05: число испытаний берётся из одного
    /// источника — вывод шага отбора (`nominal_grid_size` на пуле
    /// `instruments.csv`) — не изобретённая константа. Десять символов дают
    /// то же число, что `nominal_grid_size(10)` и тест
    /// `shortlist::nominal_grid_is_29_plus_150_for_ten_instruments`, но
    /// здесь через саму команду, не напрямую.
    #[test]
    fn power_reads_trial_count_from_instrument_pool_not_a_constant() {
        let dir = tempfile::tempdir().unwrap();
        let symbols = [
            "AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT", "EEEUSDT", "FFFUSDT", "GGGUSDT", "HHHUSDT",
            "IIIUSDT", "JJJUSDT",
        ];
        write_instruments_csv(&instruments_csv_path(dir.path()), &symbols);
        let summary = run_power(&PowerArgs {
            root: dir.path().to_path_buf(),
            hour_tests: 0,
        })
        .unwrap();
        assert_eq!(summary.n, nominal_grid_size(10));
        assert!(summary.sr0 > 0.0);
        assert!(summary.required_sharpe > summary.sr0);
        assert_eq!(summary.dsr_target, POWER_DSR_TARGET);
        assert_eq!(summary.num_obs, POWER_NUM_OBS);
    }

    /// `--hour-tests` идёт в `N` тем же путём, что `shortlist::total_trials`:
    /// разница между двумя прогонами на одном пуле равна разнице флага.
    #[test]
    fn power_adds_hour_tests_to_trial_count() {
        let dir = tempfile::tempdir().unwrap();
        write_instruments_csv(&instruments_csv_path(dir.path()), &["SOLUSDT"]);
        let base = run_power(&PowerArgs {
            root: dir.path().to_path_buf(),
            hour_tests: 0,
        })
        .unwrap();
        let with_hours = run_power(&PowerArgs {
            root: dir.path().to_path_buf(),
            hour_tests: 5,
        })
        .unwrap();
        assert_eq!(with_hours.n, base.n + 5);
    }

    /// Без `instruments.csv` — понятная ошибка, не паника и не ноль.
    #[test]
    fn power_without_instruments_csv_errors() {
        let dir = tempfile::tempdir().unwrap();
        let err = run_power(&PowerArgs {
            root: dir.path().to_path_buf(),
            hour_tests: 0,
        })
        .unwrap_err();
        assert!(err.to_string().contains("instruments.csv"), "{err}");
    }

    /// Старое номинальное число сетки на десять инструментов (до и после
    /// третьей корзины повторяемости, `SETTLED.md`/таск 06) не существует
    /// буквой в этом файле — число испытаний только вычисляется через
    /// `nominal_grid_size`/`total_trials`. Проверка — грепом по собственному
    /// исходнику (`concat!` — иначе сама проверка была бы своей находкой).
    #[test]
    fn module_does_not_hardcode_the_old_nominal_grid_size() {
        const SRC: &str = include_str!("power.rs");
        let banned = [concat!("17", "8"), concat!("17", "9")];
        for b in banned {
            assert!(
                !SRC.contains(b),
                "исходник содержит изобретённое число: {b}"
            );
        }
    }
}
