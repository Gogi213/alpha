//! `lob event-cache-probe` — проба TK-048 п.0а: стоит ли хранить разобранные события суток вместо бинлога.
//!
//! Только чтение. Суточные части → `Vec<CompactEvent>` (тем же путём, что `bounce-grid`), затем два формата:
//! A «плоский» (32 Б на событие) и B «дельта-варинт»; для каждого — размер, размер и время zstd 1 и 3
//! (сжатие, разжатие), для B ещё время разбора обратно. Разбор B сверяется с исходными событиями.
//! Время чтения файлов с диска сюда не входит отдельно: кэш страниц не контролируется.

use std::path::PathBuf;
use std::time::Instant;

use anyhow::{anyhow, bail, Context};
use clap::Args;
use serde_json::Value;

use super::backtest::{open_replay_feed, read_tick_step, replay_compact_into_until};
use crate::lob::backtest::{CompactEvent, EventKind};

const ZSTD_LEVELS: [i32; 2] = [1, 3];

/// Аргументы `lob event-cache-probe`.
#[derive(Debug, Args)]
pub struct EventCacheProbeArgs {
    /// Часть суток бинлога (повторяемый флаг: части суток по порядку).
    #[arg(long = "binlog", required = true)]
    pub binlog: Vec<PathBuf>,
    /// JSON с теми же ключами, что и строка `key=value`.
    #[arg(long)]
    pub out_json: Option<PathBuf>,
}

fn zigzag(v: i64) -> u64 {
    ((v << 1) ^ (v >> 63)) as u64
}

fn unzigzag(v: u64) -> i64 {
    ((v >> 1) as i64) ^ -((v & 1) as i64)
}

fn put_varint(buf: &mut Vec<u8>, mut v: u64) {
    while v >= 0x80 {
        buf.push((v as u8) | 0x80);
        v >>= 7;
    }
    buf.push(v as u8);
}

fn get_varint(buf: &[u8], pos: &mut usize) -> anyhow::Result<u64> {
    let mut v = 0u64;
    let mut shift = 0u32;
    loop {
        let b = *buf
            .get(*pos)
            .ok_or_else(|| anyhow!("варинт обрезан на байте {}", *pos))?;
        *pos += 1;
        if shift >= 64 {
            bail!("варинт длиннее 10 байт на байте {}", *pos);
        }
        v |= u64::from(b & 0x7f) << shift;
        if b & 0x80 == 0 {
            return Ok(v);
        }
        shift += 7;
    }
}

fn kind_of(b: u8) -> anyhow::Result<EventKind> {
    Ok(match b {
        0 => EventKind::BidDepth,
        1 => EventKind::AskDepth,
        2 => EventKind::BuyTrade,
        3 => EventKind::SellTrade,
        _ => bail!("вид события {b} вне 0..=3"),
    })
}

/// Формат A: четыре i64 little-endian на событие (`local_ts`, `exch_ms << 2 | kind`, `px_e9`, `qty_e9`).
fn encode_a(events: &[CompactEvent]) -> Vec<u8> {
    let mut out = Vec::with_capacity(events.len() * 32);
    for e in events {
        out.extend_from_slice(&e.local_ts().to_le_bytes());
        out.extend_from_slice(&((e.exch_ms() << 2) | e.kind() as i64).to_le_bytes());
        out.extend_from_slice(&e.px_e9().to_le_bytes());
        out.extend_from_slice(&e.qty_e9().to_le_bytes());
    }
    out
}

/// Формат B: на событие `zigzag-варинт(Δlocal_ts)`, `zigzag-варинт(Δexch_ms)`, байт вида,
/// `zigzag-варинт(Δpx в тиках)`, `варинт(qty в шагах)`. Не кратное сетке — ошибка, байты не теряются.
fn encode_b(events: &[CompactEvent], tick_e9: i64, step_e9: i64) -> anyhow::Result<Vec<u8>> {
    if tick_e9 <= 0 || step_e9 <= 0 {
        bail!("сетка бинлога не положительна: tick_e9={tick_e9} step_e9={step_e9}");
    }
    let mut out = Vec::with_capacity(events.len() * 8);
    let (mut ts, mut ms, mut px) = (0i64, 0i64, 0i64);
    for (i, e) in events.iter().enumerate() {
        if e.px_e9() % tick_e9 != 0 {
            bail!(
                "событие {i}: px_e9={} не кратна tick_e9={tick_e9}",
                e.px_e9()
            );
        }
        if e.qty_e9() < 0 || e.qty_e9() % step_e9 != 0 {
            bail!(
                "событие {i}: qty_e9={} не кратна step_e9={step_e9} или отрицательна",
                e.qty_e9()
            );
        }
        let px_ticks = e.px_e9() / tick_e9;
        put_varint(&mut out, zigzag(e.local_ts().wrapping_sub(ts)));
        put_varint(&mut out, zigzag(e.exch_ms().wrapping_sub(ms)));
        out.push(e.kind() as u8);
        put_varint(&mut out, zigzag(px_ticks.wrapping_sub(px)));
        put_varint(&mut out, (e.qty_e9() / step_e9) as u64);
        (ts, ms, px) = (e.local_ts(), e.exch_ms(), px_ticks);
    }
    Ok(out)
}

fn parse_b(b: &[u8], n: usize, tick_e9: i64, step_e9: i64) -> anyhow::Result<Vec<CompactEvent>> {
    let mut out = Vec::with_capacity(n);
    let (mut pos, mut ts, mut ms, mut px) = (0usize, 0i64, 0i64, 0i64);
    for _ in 0..n {
        ts = ts.wrapping_add(unzigzag(get_varint(b, &mut pos)?));
        ms = ms.wrapping_add(unzigzag(get_varint(b, &mut pos)?));
        let kind = kind_of(
            *b.get(pos)
                .ok_or_else(|| anyhow!("поток B обрезан на виде"))?,
        )?;
        pos += 1;
        px = px.wrapping_add(unzigzag(get_varint(b, &mut pos)?));
        let qty = get_varint(b, &mut pos)? as i64;
        out.push(CompactEvent::new(
            kind,
            ms,
            ts,
            px.wrapping_mul(tick_e9),
            qty.wrapping_mul(step_e9),
        ));
    }
    if pos != b.len() {
        bail!(
            "в потоке B после {n} событий лишние байты: {} из {}",
            pos,
            b.len()
        );
    }
    Ok(out)
}

/// zstd уровней 1 и 3 над `raw`: размер, время сжатия и разжатия; `false` — разжатое не равно исходному.
fn zstd_probe(prefix: &str, raw: &[u8], out: &mut Vec<(String, Value)>) -> anyhow::Result<bool> {
    let mut same = true;
    for level in ZSTD_LEVELS {
        let t = Instant::now();
        let packed = zstd::bulk::compress(raw, level).context("zstd: сжатие")?;
        let t_pack = t.elapsed().as_secs_f64();
        let t = Instant::now();
        let unpacked = zstd::bulk::decompress(&packed, raw.len()).context("zstd: разжатие")?;
        let t_unpack = t.elapsed().as_secs_f64();
        same &= unpacked == raw;
        out.push((format!("{prefix}_zstd{level}_bytes"), packed.len().into()));
        out.push((format!("{prefix}_zstd{level}_t_pack_s"), t_pack.into()));
        out.push((format!("{prefix}_zstd{level}_t_unpack_s"), t_unpack.into()));
    }
    Ok(same)
}

pub fn run_event_cache_probe(args: &EventCacheProbeArgs) -> anyhow::Result<()> {
    let raw_bytes = args
        .binlog
        .iter()
        .map(|p| {
            std::fs::metadata(p)
                .map(|m| m.len())
                .with_context(|| format!("бинлог {}: размер не читается", p.display()))
        })
        .sum::<anyhow::Result<u64>>()?;
    let (tick_e9, step_e9) = read_tick_step(&args.binlog[0])?;

    let t = Instant::now();
    let mut events: Vec<CompactEvent> = Vec::new();
    for path in &args.binlog {
        let mut feed = open_replay_feed(path)?;
        replay_compact_into_until(&mut feed, None, &mut events);
    }
    let t_decode = t.elapsed().as_secs_f64();
    let n = events.len();

    let mut kv: Vec<(String, Value)> = vec![
        ("raw_bytes".into(), raw_bytes.into()),
        ("n_events".into(), n.into()),
        ("t_decode_s".into(), t_decode.into()),
    ];

    let a = encode_a(&events);
    kv.push(("a_bytes".into(), a.len().into()));
    let mut ok = zstd_probe("a", &a, &mut kv)?;

    let b = encode_b(&events, tick_e9, step_e9)?;
    kv.push(("b_bytes".into(), b.len().into()));
    ok &= zstd_probe("b", &b, &mut kv)?;
    let t = Instant::now();
    let parsed = parse_b(&b, n, tick_e9, step_e9)?;
    kv.push(("b_t_parse_s".into(), t.elapsed().as_secs_f64().into()));
    ok &= parsed == events;
    kv.push(("roundtrip_ok".into(), ok.into()));

    println!(
        "{}",
        kv.iter()
            .map(|(k, v)| format!("{k}={v}"))
            .collect::<Vec<_>>()
            .join(" ")
    );
    if let Some(path) = &args.out_json {
        let json = Value::Object(kv.into_iter().collect());
        std::fs::write(path, serde_json::to_string_pretty(&json)?)
            .with_context(|| format!("запись {}", path.display()))?;
    }
    if !ok {
        bail!("event-cache-probe: разбор не совпал с исходными событиями (roundtrip_ok=false)");
    }
    Ok(())
}
