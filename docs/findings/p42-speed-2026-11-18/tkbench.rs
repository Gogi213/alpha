// Standalone bench: current CSV parse (copy of parse_trades/parse_px/parse_dec) vs delta-varint store decode. Verifies decoded == parsed.
use std::io::{BufRead, BufReader, Read};
use std::process::{Command, Stdio};
use std::time::Instant;
const SCALE: i64 = 100_000_000; const DAY_MS: i64 = 86_400_000;
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Tick { px: i64, off: u32, bm: bool }
fn parse_dec(b: &[u8], digits: usize) -> Option<i64> {
    let (neg, b) = match b.first()? { b'-' => (true, &b[1..]), _ => (false, b) };
    let mut ip: i64 = 0; let mut i = 0;
    while i < b.len() && b[i] != b'.' { let d = b[i].wrapping_sub(b'0'); if d > 9 { return None; } ip = ip.checked_mul(10)?.checked_add(i64::from(d))?; i += 1; }
    let mut frac: i64 = 0; let mut nd = 0;
    if i < b.len() { i += 1; while i < b.len() { let d = b[i].wrapping_sub(b'0'); if d > 9 { return None; } if nd < digits { frac = frac * 10 + i64::from(d); nd += 1; } i += 1; } }
    while nd < digits { frac *= 10; nd += 1; }
    let v = ip.checked_mul(10i64.pow(digits as u32))?.checked_add(frac)?;
    Some(if neg { -v } else { v })
}
fn parse_px(b: &[u8]) -> Option<i64> { parse_dec(b, 8) }
fn parse_trades(mut rd: impl BufRead, base: i64) -> Vec<Tick> {
    let mut v = Vec::new(); let mut line: Vec<u8> = Vec::with_capacity(128); let mut prev = 0u32; let mut unsorted = false;
    loop {
        line.clear();
        if rd.read_until(b'\n', &mut line).unwrap() == 0 { break; }
        if !line.first().is_some_and(u8::is_ascii_digit) { continue; }
        let mut f = line.split(|&c| c == b',');
        let (_, Some(p), _, _, _, Some(t), Some(b)) = (f.next(), f.next(), f.next(), f.next(), f.next(), f.next(), f.next()) else { continue };
        let (Some(px), Some(ts)) = (parse_px(p), parse_dec(t, 0)) else { continue };
        let off = ts - base;
        if !(0..DAY_MS).contains(&off) { continue; }
        let off = off as u32;
        if off < prev { unsorted = true; }
        prev = off;
        v.push(Tick { px, off, bm: b.first() == Some(&b't') });
    }
    if unsorted { v.sort_by_key(|x| x.off); }
    v
}
fn gcd(a: u64, b: u64) -> u64 { if b == 0 { a } else { gcd(b, a % b) } }
fn put(out: &mut Vec<u8>, mut v: u64) { while v >= 0x80 { out.push((v as u8 & 0x7f) | 0x80); v >>= 7; } out.push(v as u8); }
fn encode(t: &[Tick]) -> Vec<u8> {
    let mut g = 0u64; let mut prev = t.first().map_or(0, |x| x.px);
    for x in t { g = gcd(g, x.px.wrapping_sub(prev).unsigned_abs()); prev = x.px; }
    if g == 0 { g = 1; }
    let mut out = Vec::with_capacity(t.len() * 3 + 32);
    out.extend_from_slice(&(t.len() as u32).to_le_bytes());
    out.extend_from_slice(&t.first().map_or(0, |x| x.px).to_le_bytes());
    out.extend_from_slice(&g.to_le_bytes());
    let (mut ppx, mut poff) = (t.first().map_or(0, |x| x.px), 0u32);
    for x in t {
        let d = (x.px - ppx) / g as i64;
        put(&mut out, ((d << 1) ^ (d >> 63)) as u64);
        put(&mut out, (u64::from(x.off - poff) << 1) | u64::from(x.bm));
        ppx = x.px; poff = x.off;
    }
    out
}
fn decode(b: &[u8]) -> Vec<Tick> {
    let n = u32::from_le_bytes(b[0..4].try_into().unwrap()) as usize;
    let mut px = i64::from_le_bytes(b[4..12].try_into().unwrap());
    let g = u64::from_le_bytes(b[12..20].try_into().unwrap()) as i64;
    let mut v = Vec::with_capacity(n); let mut off = 0u32; let mut i = 20;
    let get = |i: &mut usize| -> u64 { let mut r = 0u64; let mut s = 0; loop { let c = b[*i]; *i += 1; r |= u64::from(c & 0x7f) << s; if c < 0x80 { return r; } s += 7; } };
    for _ in 0..n {
        let z = get(&mut i); px += ((z >> 1) as i64 ^ -((z & 1) as i64)) * g;
        let w = get(&mut i); off += (w >> 1) as u32;
        v.push(Tick { px, off, bm: w & 1 == 1 });
    }
    v
}
fn main() {
    let a: Vec<String> = std::env::args().collect();
    let (path, base_ms): (&str, i64) = (&a[1], a[2].parse().unwrap());
    // 1. inflate only (unzip child) -> bytes
    let t = Instant::now();
    let mut ch = Command::new("unzip").arg("-p").arg(path).stdout(Stdio::piped()).spawn().unwrap();
    let mut raw = Vec::new(); ch.stdout.take().unwrap().read_to_end(&mut raw).unwrap(); ch.wait().unwrap();
    let t_unzip = t.elapsed().as_secs_f64();
    // 2. parse (current code path, from memory)
    let t = Instant::now();
    let ticks = parse_trades(BufReader::with_capacity(1 << 20, &raw[..]), base_ms);
    let t_parse = t.elapsed().as_secs_f64();
    // 3. encode
    let t = Instant::now(); let enc = encode(&ticks); let t_enc = t.elapsed().as_secs_f64();
    // 4. decode x20
    let t = Instant::now(); let mut last = Vec::new(); for _ in 0..20 { last = decode(&enc); std::hint::black_box(&last); } let t_dec = t.elapsed().as_secs_f64() / 20.0;
    let ok = last == ticks;
    let n = ticks.len() as f64;
    println!("{} rows={} csv={:.1}MB store={:.2}MB ({:.2} B/row) | unzip {:.0} ns/row, parse {:.0} ns/row, encode {:.0} ns/row, DECODE {:.1} ns/row | roundtrip_identical={}",
        path.rsplit('/').next().unwrap(), ticks.len(), raw.len() as f64 / 1e6, enc.len() as f64 / 1e6, enc.len() as f64 / n,
        t_unzip / n * 1e9, t_parse / n * 1e9, t_enc / n * 1e9, t_dec / n * 1e9, ok);
}
