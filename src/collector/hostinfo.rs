//! Сведения о хосте для health (collector-perf, S6): качество часов (chrony) и TCP-счётчики хоста (дропы, ретрансмиты).
//!
//! Метка приёма в файле — часы хоста; её смысл ограничен точностью часов (chrony: смещение, RMS, дисперсия корня), поэтому они пишутся в health раз в минуту,
//! а смещение больше 1 мс — признак тревоги. TCP-счётчики хоста (`TCPRcvQDrop`, `TCPBacklogDrop`, `PruneCalled`, `TCPOFODrop`, `RetransSegs`) — накопительные:
//! при больших окнах пакетирования в буферах сокетов лежит больше байт, и рост этих счётчиков — первый признак, что буфер мал.

use std::process::Command;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

const CHRONY_EVERY: Duration = Duration::from_secs(60);
/// Смещение часов больше этого — тревога (мс).
const OFFSET_ALERT_MS: f64 = 1.0;

#[derive(Clone, Copy, Debug)]
struct Chrony {
    stratum: i64,
    offset_s: f64,
    rms_s: f64,
    root_dispersion_s: f64,
    root_delay_s: f64,
}

pub struct HostInfo {
    chrony: Option<Chrony>,
    last: Option<Instant>,
}

impl Default for HostInfo {
    fn default() -> Self {
        Self::new()
    }
}

/// Разбор `chronyc -c tracking` (CSV: refid, имя, stratum, время, смещение, последнее смещение, RMS, частота, остаточная частота, skew, root delay, root dispersion, …).
fn parse_chrony(line: &str) -> Option<Chrony> {
    let f: Vec<&str> = line.trim().split(',').collect();
    let num = |i: usize| f.get(i).and_then(|s| s.parse::<f64>().ok());
    Some(Chrony {
        stratum: f.get(2)?.parse().ok()?,
        offset_s: num(4)?,
        rms_s: num(6)?,
        root_delay_s: num(10)?,
        root_dispersion_s: num(11)?,
    })
}

/// Счётчики из `/proc/net/netstat` (раздел `TcpExt`) и `/proc/net/snmp` (`Tcp: RetransSegs`).
fn tcp_counters() -> Value {
    fn section(path: &str, tag: &str) -> std::collections::HashMap<String, u64> {
        let text = std::fs::read_to_string(path).unwrap_or_default();
        let mut head: Option<Vec<&str>> = None;
        for line in text.lines() {
            if let Some(rest) = line.strip_prefix(tag) {
                let f: Vec<&str> = rest.split_whitespace().collect();
                match head.take() {
                    None => head = Some(f),
                    Some(h) => {
                        return h
                            .iter()
                            .zip(f.iter())
                            .filter_map(|(k, v)| Some(((*k).to_string(), v.parse().ok()?)))
                            .collect();
                    }
                }
            }
        }
        std::collections::HashMap::new()
    }
    let ext = section("/proc/net/netstat", "TcpExt:");
    let tcp = section("/proc/net/snmp", "Tcp:");
    let g = |m: &std::collections::HashMap<String, u64>, k: &str| m.get(k).copied();
    json!({
        "tcp_rcvq_drop": g(&ext, "TCPRcvQDrop"), "tcp_backlog_drop": g(&ext, "TCPBacklogDrop"), "prune_called": g(&ext, "PruneCalled"),
        "tcp_ofo_drop": g(&ext, "TCPOFODrop"), "tcp_timeouts": g(&ext, "TCPTimeouts"), "retrans_segs": g(&tcp, "RetransSegs"),
    })
}

impl HostInfo {
    pub fn new() -> Self {
        Self {
            chrony: None,
            last: None,
        }
    }

    /// Обновить часы (не чаще раза в минуту; `chronyc` нет или не отвечает — прежнее значение).
    pub fn refresh(&mut self) {
        if self.last.is_some_and(|t| t.elapsed() < CHRONY_EVERY) {
            return;
        }
        self.last = Some(Instant::now());
        if let Ok(out) = Command::new("chronyc").args(["-c", "tracking"]).output() {
            if let Some(c) = parse_chrony(&String::from_utf8_lossy(&out.stdout)) {
                self.chrony = Some(c);
            }
        }
    }

    /// Раздел health `host`.
    pub fn json(&self) -> Value {
        let clock = self.chrony.map(|c| {
            json!({
                "stratum": c.stratum, "offset_us": (c.offset_s * 1e6).round(), "rms_us": (c.rms_s * 1e6).round(),
                "root_dispersion_us": (c.root_dispersion_s * 1e6).round(), "root_delay_us": (c.root_delay_s * 1e6).round(),
                "alert": c.offset_s.abs() * 1000.0 > OFFSET_ALERT_MS,
            })
        });
        json!({"clock": clock, "tcp": tcp_counters()})
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_chrony_tracking_csv() {
        let l = "1FD155F2,ntp1.lwlcom.net,2,1794580265.1,0.000064919,0.000067734,0.000271755,11.883,0.002,0.039,0.010757888,0.000624028,1036.9,Normal";
        let c = parse_chrony(l).unwrap();
        assert_eq!(c.stratum, 2);
        assert!((c.offset_s - 0.000_064_919).abs() < 1e-12);
        assert!((c.root_dispersion_s - 0.000_624_028).abs() < 1e-12);
        assert!(parse_chrony("").is_none());
    }
}
