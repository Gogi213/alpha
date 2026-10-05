use super::*;
use crate::lob::backtest::EventKind;

fn sample(n: usize) -> Vec<CompactEvent> {
    let kinds = [
        EventKind::BidDepth,
        EventKind::AskDepth,
        EventKind::BuyTrade,
        EventKind::SellTrade,
    ];
    (0..n)
        .map(|i| {
            let i = i as i64;
            CompactEvent::new(
                kinds[(i % 4) as usize],
                1_700_000_000_000 + i / 3,
                1_700_000_000_000_000_000 + i * 7_000 + (i % 5) * 13,
                50_000_000_000 + (i % 17) * 10_000_000 - (i % 3) * 5_000_000,
                if i % 11 == 0 { 0 } else { (i % 29) * 1_000_000 },
            )
        })
        .collect()
}

fn round_trip(evs: &[CompactEvent], until: Option<i64>) -> (Vec<CompactEvent>, bool) {
    let z = encode_chunk(evs, 3).unwrap();
    let mut scratch: [Vec<u8>; NCOL] = Default::default();
    let mut out = Vec::new();
    let hit = decode_chunk(&z, evs.len(), until, &mut scratch, &mut out).unwrap();
    (out, hit)
}

#[test]
fn chunk_round_trip_is_bit_exact() {
    let evs = sample(5_000);
    let (out, hit) = round_trip(&evs, None);
    assert!(!hit);
    assert_eq!(out, evs);
}

#[test]
fn chunk_stops_at_first_event_at_or_after_ceiling() {
    let evs = sample(5_000);
    let until = evs[1_234].raw()[0];
    let (out, hit) = round_trip(&evs, Some(until));
    assert!(hit);
    let want: Vec<_> = evs
        .iter()
        .take_while(|e| e.raw()[0] < until)
        .copied()
        .collect();
    assert_eq!(out, want);
}

#[test]
fn prepared_file_round_trip_and_stamp_mismatch() {
    let dir = std::env::temp_dir().join(format!("alpha-prep-test-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let src = dir.join("TESTUSDT-2026-01-15.binlog");
    std::fs::write(&src, b"x").unwrap();
    let (len, mtime) = file_stamp(&src).unwrap();
    let evs = sample(2_500_000);
    let mut body = Vec::new();
    let mut entries = Vec::new();
    for c in evs.chunks(CHUNK_EVENTS) {
        entries.push((c[0].raw()[0], c.len() as u32, encode_chunk(c, 3).unwrap()));
    }
    let mut off = (HEADER_FIXED + entries.len() * ENTRY_LEN) as u64;
    let mut table = Vec::new();
    for (f, n, d) in &entries {
        table.extend_from_slice(&f.to_le_bytes());
        table.extend_from_slice(&n.to_le_bytes());
        table.extend_from_slice(&off.to_le_bytes());
        table.extend_from_slice(&(d.len() as u32).to_le_bytes());
        off += d.len() as u64;
        body.extend_from_slice(d);
    }
    let mut file = Vec::new();
    file.extend_from_slice(MAGIC);
    file.extend_from_slice(&(evs.len() as u64).to_le_bytes());
    file.extend_from_slice(&len.to_le_bytes());
    file.extend_from_slice(&mtime.to_le_bytes());
    file.extend_from_slice(&[0u8; 32]);
    file.extend_from_slice(&(entries.len() as u32).to_le_bytes());
    file.extend_from_slice(&table);
    file.extend_from_slice(&body);
    std::fs::write(dir.join("TESTUSDT-2026-01-15.binlog.prep"), &file).unwrap();
    let dirs = vec![dir.clone()];

    let all = prepared_day_events(&dirs, std::slice::from_ref(&src))
        .unwrap()
        .unwrap();
    assert_eq!(all, evs);

    let until = evs[1_900_000].raw()[0];
    let mut carry = Vec::new();
    let n = prepared_carry_events(&dirs, std::slice::from_ref(&src), until, &mut carry)
        .unwrap()
        .unwrap();
    let want: Vec<_> = evs
        .iter()
        .take_while(|e| e.raw()[0] < until)
        .copied()
        .collect();
    assert_eq!(n, want.len());
    assert_eq!(carry, want);

    std::fs::write(&src, b"xy").unwrap();
    assert!(prepared_day_events(&dirs, std::slice::from_ref(&src))
        .unwrap()
        .is_none());
    std::fs::remove_dir_all(&dir).unwrap();
}
