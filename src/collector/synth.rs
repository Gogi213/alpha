//! Синтетический источник кадров Binance для лестницы нагрузки: локальный открытый WebSocket, заданный темп кадров на соединение.
//!
//! Зачем: настоящая биржа даёт до 4–5 тыс. кадров/с на соединение (книга Binance, замер TK-531) и до 10–25 тыс. у глубокого вида Bybit
//! (TK-533); потолок приёмника проверяется источником, который темп держит. Кадры по форме настоящие (`bookTicker`/`aggTrade`),
//! время биржи E — момент отправки, поэтому лаг «приём − E» показывает отставание приёмника. Источник тоже меряется: сколько
//! кадров ушло и отстаёт ли он от заданного темпа (клиент не успевает читать — буфер сокета полон — источник не добирает).

use std::io::Write as _;
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::time::{Duration, Instant};

use clap::Args;
use tungstenite::{Error as WsError, Message};

#[derive(Debug, Args)]
pub struct SynthArgs {
    /// Адрес прослушивания (открытый ws://).
    #[arg(long, default_value = "127.0.0.1:9001")]
    pub listen: String,
    /// Символов в потоке (S000USDT…); пул для коллектора — `--write-pool`.
    #[arg(long, default_value_t = 64)]
    pub symbols: usize,
    /// Кадров в секунду на каждое соединение.
    #[arg(long, default_value_t = 10_000)]
    pub rate: u64,
    /// Вид кадров: book (bookTicker) или trade (aggTrade).
    #[arg(long, default_value = "book")]
    pub kind: String,
    /// Остановиться через столько секунд (0 — работать до убийства процесса).
    #[arg(long, default_value_t = 0)]
    pub duration_s: u64,
    /// Записать пул `{"pool": [...]}` и работать дальше.
    #[arg(long)]
    pub write_pool: Option<PathBuf>,
}

fn sym_name(i: usize) -> String {
    format!("S{i:03}USDT")
}

fn now_ms() -> i64 {
    super::events::now_ms()
}

/// Кадр книги: u растёт по символу, цены — «как с биржи» (строки).
fn book_frame(out: &mut Vec<u8>, sym: &str, lower: &str, u: i64, e: i64, px: i64) {
    out.clear();
    let _ = write!(
        out,
        r#"{{"stream":"{lower}@bookTicker","data":{{"e":"bookTicker","u":{u},"s":"{sym}","ps":"{sym}","b":"{}.{:04}","B":"{}","a":"{}.{:04}","A":"{}","T":{},"E":{e},"st":1}}}}"#,
        px / 10_000,
        px % 10_000,
        px % 97 + 1,
        px / 10_000,
        px % 10_000 + 1,
        px % 89 + 1,
        e - 1
    );
}

fn trade_frame(out: &mut Vec<u8>, sym: &str, lower: &str, a: i64, e: i64, px: i64) {
    out.clear();
    let _ = write!(
        out,
        r#"{{"stream":"{lower}@aggTrade","data":{{"e":"aggTrade","E":{e},"a":{a},"s":"{sym}","p":"{}.{:04}","q":"{}","nq":"{}","f":{},"l":{},"T":{},"m":{},"st":1}}}}"#,
        px / 10_000,
        px % 10_000,
        px % 97 + 1,
        px % 97 + 1,
        a * 2,
        a * 2 + 1,
        e - 1,
        px % 2 == 0
    );
}

/// Одно соединение: читает подписку, отвечает на неё, шлёт кадры в темпе `rate`, раз в секунду печатает счёт.
fn serve(stream: TcpStream, args: &SynthArgs, t_end: Option<Instant>) {
    let Ok(mut ws) = tungstenite::accept(stream) else { return };
    match ws.read() {
        Ok(m) if m.is_text() => {}
        _ => return,
    }
    let _ = ws.send(Message::text(r#"{"result":null,"id":1}"#));
    if ws.get_ref().set_nonblocking(true).is_err() {
        return;
    }
    let names: Vec<(String, String)> = (0..args.symbols.max(1)).map(|i| (sym_name(i), sym_name(i).to_lowercase())).collect();
    let mut seq = vec![1_000_000i64; names.len()];
    let mut buf = Vec::with_capacity(512);
    let (t0, per_tick) = (Instant::now(), args.rate);
    let (mut sent, mut last_print, mut printed_at) = (0u64, Instant::now(), 0u64);
    let mut px = 86_790i64;
    let mut idx = 0usize;
    let mut pending = false; // в буфере tungstenite есть неотправленное (сокет полон)
    loop {
        if t_end.is_some_and(|t| Instant::now() >= t) {
            return;
        }
        let tick_start = Instant::now();
        // накопленный «долг»: темп считается от старта, отставание догоняется, пока сокет принимает
        let due = u64::try_from(t0.elapsed().as_micros()).unwrap_or(0) * per_tick / 1_000_000;
        let mut owed = due.saturating_sub(sent);
        let mut batch = 0u64;
        while owed > 0 && !pending && batch < 2_000 {
            let (sym, lower) = names.get(idx).map_or(("S000USDT", "s000usdt"), |(a, b)| (a.as_str(), b.as_str()));
            let slot = seq.get_mut(idx);
            let n = slot.map_or(0, |s| {
                *s += 1;
                *s
            });
            px = (px + (n % 7) - 3).max(10_000);
            if args.kind == "trade" {
                trade_frame(&mut buf, sym, lower, n, now_ms(), px);
            } else {
                book_frame(&mut buf, sym, lower, n, now_ms(), px);
            }
            if ws.write(Message::text(String::from_utf8_lossy(&buf).into_owned())).is_err() {
                return;
            }
            idx = (idx + 1) % names.len();
            sent += 1;
            owed -= 1;
            batch += 1;
        }
        match ws.flush() {
            Ok(()) => pending = false,
            Err(WsError::Io(e)) if e.kind() == std::io::ErrorKind::WouldBlock => pending = true,
            Err(_) => return,
        }
        // клиентские кадры (ping) — прочитать, чтобы pong ушёл
        match ws.read() {
            Ok(Message::Close(_)) | Err(WsError::ConnectionClosed | WsError::AlreadyClosed) => return,
            Ok(_) => {}
            Err(WsError::Io(e)) if e.kind() == std::io::ErrorKind::WouldBlock => {}
            Err(_) => return,
        }
        if last_print.elapsed() >= Duration::from_secs(1) {
            let secs = last_print.elapsed().as_secs_f64();
            println!("synth t={:.0}s sent={} rate={:.0}/s due_behind={} pending={}", t0.elapsed().as_secs_f64(), sent, (sent - printed_at) as f64 / secs, due.saturating_sub(sent), pending);
            (last_print, printed_at) = (Instant::now(), sent);
        }
        let spent = tick_start.elapsed();
        if spent < Duration::from_micros(500) && (owed == 0 || pending) {
            std::thread::sleep(Duration::from_micros(500) - spent);
        }
    }
}

pub fn run(args: &SynthArgs) -> anyhow::Result<()> {
    if let Some(p) = &args.write_pool {
        let pool: Vec<String> = (0..args.symbols).map(sym_name).collect();
        std::fs::write(p, serde_json::json!({"pool": pool}).to_string())?;
    }
    let listener = TcpListener::bind(&args.listen)?;
    println!("synth listening {} symbols={} rate={}/s kind={}", args.listen, args.symbols, args.rate, args.kind);
    let t_end = (args.duration_s > 0).then(|| Instant::now() + Duration::from_secs(args.duration_s));
    let args = std::sync::Arc::new(SynthArgs { listen: args.listen.clone(), symbols: args.symbols, rate: args.rate, kind: args.kind.clone(), duration_s: args.duration_s, write_pool: None });
    listener.set_nonblocking(true)?;
    loop {
        if t_end.is_some_and(|t| Instant::now() >= t) {
            return Ok(());
        }
        match listener.accept() {
            Ok((s, _)) => {
                s.set_nodelay(true)?;
                s.set_nonblocking(false)?;
                let a = std::sync::Arc::clone(&args);
                std::thread::spawn(move || serve(s, &a, t_end));
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => std::thread::sleep(Duration::from_millis(20)),
            Err(e) => return Err(e.into()),
        }
    }
}
