//! Шаг 2 замера подготовленных данных (TK-050): поток СТРОК СЧЁТА (`CompactEvent` после push_frame/Update/push_side)
//! колонками (дельты varint) + zstd 9/19 против бинлога; ЦП загрузки в строки против нынешней полной цепочки
//! бинлог -> `CompactEvent`; ЦП сжатия; равенство потока строк. Запуск: `prep_rows <файл.binlog>...` (суммы по файлам).

use std::env;
use std::time::Instant;

use alpha::commands::lob::backtest::compact_events_of_binlog;
use alpha::lob::backtest::CompactEvent;

fn put_uv(buf: &mut Vec<u8>, mut v: u64) {
    while v >= 0x80 {
        buf.push((v as u8) | 0x80);
        v >>= 7;
    }
    buf.push(v as u8);
}
fn put_zz(buf: &mut Vec<u8>, v: i64) {
    put_uv(buf, ((v << 1) ^ (v >> 63)) as u64);
}
fn get_uv(buf: &[u8], pos: &mut usize) -> u64 {
    let b = buf[*pos];
    *pos += 1;
    if b < 0x80 {
        return u64::from(b);
    }
    let mut r = u64::from(b & 0x7f);
    let mut sh = 7;
    loop {
        let b = buf[*pos];
        *pos += 1;
        r |= u64::from(b & 0x7f) << sh;
        if b < 0x80 {
            return r;
        }
        sh += 7;
    }
}
fn get_zz(buf: &[u8], pos: &mut usize) -> i64 {
    let v = get_uv(buf, pos);
    ((v >> 1) as i64) ^ -((v & 1) as i64)
}

const NCOL: usize = 5;

fn encode(evs: &[CompactEvent]) -> [Vec<u8>; NCOL] {
    let mut c: [Vec<u8>; NCOL] = Default::default();
    let (mut lo, mut ex) = (0i64, 0i64);
    let mut px = [0i64; 4];
    for e in evs {
        let [l, ek, p, q] = e.raw();
        let k = (ek & 3) as usize;
        c[0].push(k as u8);
        put_zz(&mut c[1], l.wrapping_sub(lo));
        put_zz(&mut c[2], (ek >> 2).wrapping_sub(ex));
        put_zz(&mut c[3], p.wrapping_sub(px[k]));
        put_zz(&mut c[4], q);
        lo = l;
        ex = ek >> 2;
        px[k] = p;
    }
    c
}

fn decode(c: &[Vec<u8>; NCOL], n: usize, out: &mut Vec<CompactEvent>) {
    let mut p = [0usize; NCOL];
    let (mut lo, mut ex) = (0i64, 0i64);
    let mut px = [0i64; 4];
    out.clear();
    out.reserve(n);
    for i in 0..n {
        let k = c[0][i] as usize;
        lo = lo.wrapping_add(get_zz(&c[1], &mut p[1]));
        ex = ex.wrapping_add(get_zz(&c[2], &mut p[2]));
        px[k] = px[k].wrapping_add(get_zz(&c[3], &mut p[3]));
        let q = get_zz(&c[4], &mut p[4]);
        out.push(CompactEvent::from_raw([lo, (ex << 2) | k as i64, px[k], q]));
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let levels = [9, 19];
    let (mut disk, mut nev, mut raw) = (0u64, 0u64, 0u64);
    let mut zsz = [0u64; 2];
    let (mut t_chain, mut t_raw, mut mism) = (0f64, 0f64, 0u64);
    let (mut t_zd, mut t_zc) = ([0f64; 2], [0f64; 2]);
    let mut back: Vec<CompactEvent> = Vec::new();
    for path in env::args().skip(1) {
        disk += std::fs::metadata(&path)?.len();
        let t = Instant::now();
        let evs = compact_events_of_binlog(std::path::Path::new(&path))?;
        t_chain += t.elapsed().as_secs_f64();
        nev += evs.len() as u64;
        let cols = encode(&evs);
        raw += cols.iter().map(|c| c.len() as u64).sum::<u64>();
        let t = Instant::now();
        decode(&cols, evs.len(), &mut back);
        t_raw += t.elapsed().as_secs_f64();
        if back != evs {
            mism += 1;
        }
        for (i, &lv) in levels.iter().enumerate() {
            let t = Instant::now();
            let z: Vec<Vec<u8>> = cols
                .iter()
                .map(|c| zstd::bulk::compress(c, lv).unwrap())
                .collect();
            t_zc[i] += t.elapsed().as_secs_f64();
            zsz[i] += z.iter().map(|v| v.len() as u64).sum::<u64>();
            let t = Instant::now();
            let un: Vec<Vec<u8>> = z
                .iter()
                .zip(cols.iter())
                .map(|(v, c)| zstd::bulk::decompress(v, c.len() + 1).unwrap())
                .collect();
            let un: [Vec<u8>; NCOL] = un.try_into().unwrap();
            decode(&un, evs.len(), &mut back);
            t_zd[i] += t.elapsed().as_secs_f64();
            if back != evs {
                mism += 1;
            }
        }
    }
    println!(
        "файлов={} строк={nev} расхождений={mism}",
        env::args().count() - 1
    );
    println!("бинлоги: {:.1} МБ (1,00); нынешняя цепочка бинлог->строки счёта (ЦП 1 поток): {t_chain:.2} с", disk as f64 / 1e6);
    println!(
        "колонки без сжатия: {:.1} МБ ({:.2}); загрузка в строки: {t_raw:.2} с",
        raw as f64 / 1e6,
        raw as f64 / disk as f64
    );
    for (i, &lv) in levels.iter().enumerate() {
        println!(
            "колонки+zstd{lv}: {:.1} МБ ({:.2}); распаковка+загрузка в строки: {:.2} с ({:.1}x дешевле цепочки); сжатие: {:.2} с = {:.0} ЦП-с на ГБ бинлога",
            zsz[i] as f64 / 1e6,
            zsz[i] as f64 / disk as f64,
            t_zd[i],
            t_chain / t_zd[i],
            t_zc[i],
            t_zc[i] / (disk as f64 / 1e9)
        );
    }
    Ok(())
}
