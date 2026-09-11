//! `lob clock` — CLI-обёртка `run_clock` (шаг 0.5). Логика измерения смещения
//! часов и NTP-источник — в `crate::bybit::clock`; здесь только аргументы,
//! фикстура для тестов без сети и печать (в `mod.rs::dispatch`).

use std::path::PathBuf;
use std::time::Duration;

use clap::Args;

use crate::bybit::clock::{
    run as run_clock_loop, BybitServerTimeSource, ClockRow, ReferenceClock, RoundTrip, UdpNtpSource,
};
use crate::bybit::conn::SystemClock;
use crate::bybit::rest::BYBIT_MAINNET_URL;

// ---------------------------------------------------------------------------
// `lob clock` (шаг 0.5).
// ---------------------------------------------------------------------------

/// Аргументы `lob clock`: смещение часов хоста против NTP и `serverTime`,
/// строка в `clock.csv`. Живой каденс шага — раз в час; команда снимает
/// `--samples` замеров подряд и выходит (часовая петля — дело рекордера).
#[derive(Debug, Args)]
pub struct ClockArgs {
    /// Корень записи: сюда дописывается `clock.csv`.
    #[arg(long, default_value = "data/bybit")]
    pub root: PathBuf,
    /// NTP-сервер с портом.
    #[arg(long, default_value = "pool.ntp.org:123")]
    pub ntp_server: String,
    /// REST-хост Bybit v5 для `serverTime`.
    #[arg(long, default_value = BYBIT_MAINNET_URL)]
    pub base_url: String,
    /// Сколько замеров снять подряд.
    #[arg(long, default_value_t = 1)]
    pub samples: u64,
    /// Фикстура без сети: сценарные раунды через те же `sample`/`append_row`.
    #[arg(long, default_value_t = false)]
    pub fixture: bool,
}

/// Тикер на N тактов: решение «когда закончить» снаружи (Decision 21 отдаёт
/// его `watch`), здесь только счётчик для `clock::run`.
struct CountTicker {
    remaining: u64,
}

impl crate::bybit::clock::Ticker for CountTicker {
    fn next_tick(&mut self) -> bool {
        if self.remaining == 0 {
            return false;
        }
        self.remaining -= 1;
        true
    }
}

/// Сценарный эталон для `--fixture`: та же роль, что `ScriptedSource` в
/// тестах `clock`, — готовые раунды вместо сети.
struct FixtureClock {
    trips: std::collections::VecDeque<RoundTrip>,
}

impl ReferenceClock for FixtureClock {
    fn round_trip(&mut self) -> Result<RoundTrip, crate::bybit::clock::ClockError> {
        self.trips.pop_front().ok_or_else(|| {
            crate::bybit::clock::ClockError::Transport("фикстура исчерпана".to_string())
        })
    }
}

fn fixture_trips(samples: u64) -> std::collections::VecDeque<RoundTrip> {
    (0..samples)
        .map(|i| {
            let base = 1_000_000_000 + i as i64 * 3_600_000_000_000;
            RoundTrip {
                local_send_ns: base,
                remote_ns: base + 1_000_000,
                local_recv_ns: base + 2_000_000,
            }
        })
        .collect()
}

/// Один часовой замер: раунд у обоих эталонов через общий `clock::run`,
/// строка в `clock.csv`. Возвращает строки для проверки `check_rows`.
pub fn run_clock(args: &ClockArgs) -> anyhow::Result<Vec<ClockRow>> {
    let path = args.root.join("clock.csv");
    let mut ticker = CountTicker {
        remaining: args.samples,
    };
    if args.fixture {
        let mut ntp = FixtureClock {
            trips: fixture_trips(args.samples),
        };
        let mut bybit = FixtureClock {
            trips: fixture_trips(args.samples),
        };
        return run_clock_loop(&path, &SystemClock, &mut ntp, &mut bybit, &mut ticker)
            .map_err(|e| anyhow::anyhow!("clock.csv: {e:?}"));
    }
    let mut ntp = UdpNtpSource::connect(args.ntp_server.as_str(), Duration::from_secs(10))
        .map_err(|e| anyhow::anyhow!("NTP {}: {e:?}", args.ntp_server))?;
    let mut bybit = BybitServerTimeSource::new(args.base_url.clone())
        .map_err(|e| anyhow::anyhow!("serverTime: {e:?}"))?;
    run_clock_loop(&path, &SystemClock, &mut ntp, &mut bybit, &mut ticker)
        .map_err(|e| anyhow::anyhow!("clock.csv: {e:?}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clock_fixture_writes_a_row() {
        let dir = tempfile::tempdir().unwrap();
        let args = ClockArgs {
            root: dir.path().to_path_buf(),
            ntp_server: "127.0.0.1:1".to_string(),
            base_url: "http://127.0.0.1:1".to_string(),
            samples: 2,
            fixture: true,
        };
        let rows = run_clock(&args).unwrap();
        assert_eq!(rows.len(), 2);
        let read_back = crate::bybit::clock::read_rows(&dir.path().join("clock.csv")).unwrap();
        assert_eq!(read_back.len(), 2);
        assert_eq!(read_back[0].ntp_offset_ns, Some(0));
        assert!(super::super::check_rows(&read_back).is_empty());
    }
}
