//! σ монеты на взводе для σ-лестницы (В-131, Судья 27.09 «Окончательно»): боковая таблица
//! `<--sigma-from>/sigma-<SYMBOL>.csv` от `tools/compute/sigma-table.py` — σ = √Σr² минутных лог-доходностей
//! закрытий за 240 минут, в bps, окно кончается на последней **закрытой** минуте до сигнала; меньше 200
//! минут из 240 — строки нет (σ нет, сигнал не торгуется, `n_no_sigma`). Кэш подходов не пересчитывается:
//! σ зависит только от монеты и минуты взвода, поэтому таблица — по концу окна, а не по подходу.

use std::path::Path;

/// Шапка таблицы: конец окна (UTC, мс, кратно минуте — момент закрытия последней минуты окна) и σ, bps.
const HEADER: [&str; 2] = ["window_end_ms", "sigma_bps"];

const MINUTE_MS: i64 = 60_000;

/// σ монеты по концам окон, строго по возрастанию конца.
#[derive(Debug, Default)]
pub(super) struct EntrySigma {
    end_ms: Vec<i64>,
    sigma_bps: Vec<f64>,
}

impl EntrySigma {
    /// Таблица монеты; файла нет — отказ (σ-форма без σ считала бы пустоту).
    pub(super) fn read(dir: &Path, symbol: &str) -> anyhow::Result<Self> {
        let path = dir.join(format!("sigma-{symbol}.csv"));
        let mut rd = csv::Reader::from_path(&path).map_err(|e| {
            anyhow::anyhow!(
                "--sigma-from: {} не читается ({e}) — таблица σ монеты от tools/compute/sigma-table.py",
                path.display()
            )
        })?;
        let header: Vec<String> = rd.headers()?.iter().map(str::to_string).collect();
        anyhow::ensure!(
            header == HEADER,
            "{}: шапка {header:?}, ожидалась {HEADER:?}",
            path.display()
        );
        let mut out = Self::default();
        for (i, rec) in rd.records().enumerate() {
            let rec = rec?;
            let line = i + 2;
            let end: i64 = rec[0].parse().map_err(|_| {
                anyhow::anyhow!("{}:{line}: конец окна {:?}", path.display(), &rec[0])
            })?;
            let sigma: f64 = rec[1]
                .parse()
                .map_err(|_| anyhow::anyhow!("{}:{line}: σ {:?}", path.display(), &rec[1]))?;
            anyhow::ensure!(
                end % MINUTE_MS == 0,
                "{}:{line}: конец окна {end} не кратен минуте",
                path.display()
            );
            anyhow::ensure!(
                sigma.is_finite() && sigma >= 0.0,
                "{}:{line}: σ {sigma} — конечное ≥ 0",
                path.display()
            );
            anyhow::ensure!(
                out.end_ms.last().is_none_or(|&prev| end > prev),
                "{}:{line}: концы окон не строго возрастают",
                path.display()
            );
            out.end_ms.push(end);
            out.sigma_bps.push(sigma);
        }
        Ok(out)
    }

    /// σ для сигнала в `signal_ms`: окно, кончающееся на последней минутной границе не позже сигнала
    /// (минута, закрывшаяся ровно в `signal_ms`, уже закрыта). Строки нет — σ нет.
    pub(super) fn at(&self, signal_ms: i64) -> Option<f64> {
        let end = signal_ms.div_euclid(MINUTE_MS) * MINUTE_MS;
        self.end_ms
            .binary_search(&end)
            .ok()
            .map(|i| self.sigma_bps[i])
    }
}
