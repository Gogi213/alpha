//! `lob latency` — задержки торгового пути на живом счёте: RTT, постановка и
//! снятие лимитки, исполнение тейкера (владелец, 2026-09-19). Ядро —
//! `crate::bybit::latency` (фейки, без сети); здесь аргументы, три живых
//! транспорта (REST через `reqwest`, WS trade и приватный стрим через
//! `tokio-tungstenite`), запись CSV и печать сводки.
//!
//! **Ставит настоящие ордера**: post-only далеко от середины (снимается сразу)
//! и, если не `--skip-taker`, рыночные на `--notional-usd` в обе стороны
//! (позиция плоская после каждой ноги; в конце и после любой ошибки тейкера —
//! `cancel-all` + закрытие остатка `reduceOnly`). Ключи — только
//! `BYBIT_API_KEY`/`BYBIT_API_SECRET` из окружения (Decision 12), значения не
//! печатаются и в файлы не попадают.

use std::path::PathBuf;
use std::sync::mpsc;
use std::time::{Duration, Instant};

use clap::Args;
use futures::{SinkExt, StreamExt};
use tokio_tungstenite::tungstenite::Message;

use crate::bybit::latency::{
    endpoints_for, format_summary, notional_e9, parse_filters, qty_for_notional, summarize, Bench,
    Endpoints, Feed, LatencyError, Plan, PrivateSource, Rest, TradeWs, INSTRUMENTS_PATH,
    ORDERBOOK_PATH,
};
use crate::bybit::probe::{OrderSide, DEFAULT_RECV_WINDOW_MS};
use crate::bybit::sign::Credentials;

/// Общий HTTP-таймаут — как у `probe::HTTP_TIMEOUT` (дефект В-5): десять секунд.
const HTTP_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Debug, Args)]
pub struct LatencyArgs {
    /// Символ, например `DOGEUSDT`.
    #[arg(long)]
    pub symbol: String,
    /// Номинал одного ордера в USDT (владелец: 10). Лимитка и тейкер — одного размера.
    #[arg(long)]
    pub notional_usd: i64,
    /// Число циклов.
    #[arg(long)]
    pub cycles: usize,
    /// Сторона первой ноги тейкера и лимитки: `buy` или `sell`.
    #[arg(long, default_value = "buy")]
    pub side: String,
    /// Смещение post-only от середины в тиках (как у `lob probe`).
    #[arg(long, default_value_t = 100)]
    pub ticks_from_mid: i64,
    /// Контур: `mainnet`, `testnet`, `demo` — задаёт все три адреса разом.
    #[arg(long, default_value = "mainnet")]
    pub net: String,
    /// Переопределить REST-хост.
    #[arg(long)]
    pub rest_url: Option<String>,
    /// Переопределить адрес WS trade.
    #[arg(long)]
    pub ws_trade_url: Option<String>,
    /// Переопределить адрес приватного стрима.
    #[arg(long)]
    pub ws_private_url: Option<String>,
    /// Окно подписи Bybit (умолчание биржи — 5 с); им же ограничено ожидание кадров.
    #[arg(long, default_value_t = DEFAULT_RECV_WINDOW_MS)]
    pub recv_window_ms: u32,
    /// Пауза между циклами, мс (лимиты биржи: 10 ордеров/с на символ).
    #[arg(long, default_value_t = 1000)]
    pub pause_ms: u64,
    /// Не ставить рыночные ордера (только лимитка и снятие).
    #[arg(long, default_value_t = false)]
    pub skip_taker: bool,
    /// Не открывать WS trade (только REST + приватный стрим).
    #[arg(long, default_value_t = false)]
    pub skip_ws_trade: bool,
    /// Куда писать точки (по умолчанию `data/bybit/latency-<SYMBOL>-<UTC>.csv`).
    #[arg(long)]
    pub out: Option<PathBuf>,
}

fn parse_side(s: &str) -> anyhow::Result<OrderSide> {
    match s.to_ascii_lowercase().as_str() {
        "buy" => Ok(OrderSide::Buy),
        "sell" => Ok(OrderSide::Sell),
        other => Err(anyhow::anyhow!(
            "сторона обязана быть buy или sell, получено {other}"
        )),
    }
}

fn wall_ms() -> Result<i64, LatencyError> {
    crate::bybit::probe::wall_clock_timestamp_ms()
        .map_err(|e| LatencyError::Transport(format!("{e:?}")))
}

// ---------------------------------------------------------------------------
// REST.
// ---------------------------------------------------------------------------

struct LiveRest {
    client: reqwest::Client,
    base: String,
    runtime: tokio::runtime::Runtime,
    creds: Credentials,
    recv_window_ms: u32,
}

impl LiveRest {
    fn new(base: &str, recv_window_ms: u32) -> Result<Self, LatencyError> {
        let creds = Credentials::from_env().map_err(|e| {
            LatencyError::Transport(format!(
                "ключи: {e:?} — выставьте BYBIT_API_KEY/BYBIT_API_SECRET"
            ))
        })?;
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|e| LatencyError::Transport(e.to_string()))?;
        let client = reqwest::Client::builder()
            .timeout(HTTP_TIMEOUT)
            .build()
            .map_err(|e| LatencyError::Transport(e.to_string()))?;
        Ok(Self {
            client,
            base: base.to_string(),
            runtime,
            creds,
            recv_window_ms,
        })
    }

    fn run(&mut self, req: reqwest::RequestBuilder) -> Result<String, LatencyError> {
        self.runtime.block_on(async move {
            let resp = req
                .send()
                .await
                .map_err(|e| LatencyError::Transport(e.to_string()))?;
            resp.text()
                .await
                .map_err(|e| LatencyError::Transport(e.to_string()))
        })
    }

    /// Подпись v5: `timestamp + api_key + recv_window + (query | body)`.
    fn signed(
        &self,
        req: reqwest::RequestBuilder,
        payload: &str,
    ) -> Result<reqwest::RequestBuilder, LatencyError> {
        let ts = wall_ms()?;
        let sig = self
            .creds
            .sign(ts, self.recv_window_ms, payload)
            .map_err(|e| LatencyError::Transport(format!("подпись: {e:?}")))?;
        Ok(req
            .header("X-BAPI-API-KEY", self.creds.api_key())
            .header("X-BAPI-TIMESTAMP", ts.to_string())
            .header("X-BAPI-RECV-WINDOW", self.recv_window_ms.to_string())
            .header("X-BAPI-SIGN", sig))
    }

    fn url(&self, path: &str, query: &str) -> String {
        if query.is_empty() {
            format!("{}{path}", self.base)
        } else {
            format!("{}{path}?{query}", self.base)
        }
    }
}

impl Rest for LiveRest {
    fn get_public(&mut self, path: &str, query: &str) -> Result<String, LatencyError> {
        let req = self.client.get(self.url(path, query));
        self.run(req)
    }
    fn get_signed(&mut self, path: &str, query: &str) -> Result<String, LatencyError> {
        let req = self.signed(self.client.get(self.url(path, query)), query)?;
        self.run(req)
    }
    fn post_signed(&mut self, path: &str, body: &str) -> Result<String, LatencyError> {
        let req = self
            .signed(self.client.post(self.url(path, "")), body)?
            .header("Content-Type", "application/json")
            .body(body.to_string());
        self.run(req)
    }
}

// ---------------------------------------------------------------------------
// WebSocket: общее — подключение и `auth`.
// ---------------------------------------------------------------------------

type WsStream =
    tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>;

fn auth_frame(creds: &Credentials, recv_window_ms: u32) -> Result<String, LatencyError> {
    let expires = wall_ms()? + i64::from(recv_window_ms);
    let sig = creds
        .ws_auth_signature(expires)
        .map_err(|e| LatencyError::Transport(format!("подпись auth: {e:?}")))?;
    Ok(serde_json::json!({"op": "auth", "args": [creds.api_key(), expires, sig]}).to_string())
}

async fn next_text(
    stream: &mut WsStream,
    timeout: Duration,
) -> Result<Option<String>, LatencyError> {
    let deadline = tokio::time::Instant::now() + timeout;
    loop {
        let next = tokio::time::timeout_at(deadline, stream.next()).await;
        match next {
            Err(_) => return Ok(None),
            Ok(None) => return Err(LatencyError::Transport("сокет закрыт".to_string())),
            Ok(Some(Err(e))) => return Err(LatencyError::Transport(e.to_string())),
            Ok(Some(Ok(Message::Text(t)))) => return Ok(Some(t.as_str().to_string())),
            Ok(Some(Ok(Message::Close(_)))) => {
                return Err(LatencyError::Transport("сокет закрыт биржей".to_string()))
            }
            Ok(Some(Ok(_))) => continue,
        }
    }
}

/// Подключение и `auth`; ответ биржи проверяется (`retCode == 0` у trade,
/// `success == true` у приватного стрима), иначе — ошибка с текстом.
async fn connect_authed(
    url: &str,
    creds: &Credentials,
    recv_window_ms: u32,
    wait: Duration,
) -> Result<WsStream, LatencyError> {
    let (mut stream, _resp) = tokio_tungstenite::connect_async(url)
        .await
        .map_err(|e| LatencyError::Transport(format!("{url}: {e}")))?;
    stream
        .send(Message::Text(auth_frame(creds, recv_window_ms)?.into()))
        .await
        .map_err(|e| LatencyError::Transport(e.to_string()))?;
    let raw = next_text(&mut stream, wait)
        .await?
        .ok_or(LatencyError::Timeout("ответ auth"))?;
    let v: serde_json::Value =
        serde_json::from_str(&raw).map_err(|e| LatencyError::Decode(e.to_string()))?;
    let ok = v["retCode"].as_i64() == Some(0) || v["success"].as_bool() == Some(true);
    if !ok {
        return Err(LatencyError::Transport(format!("auth отклонён: {raw}")));
    }
    Ok(stream)
}

// ---------------------------------------------------------------------------
// WS trade: синхронная обёртка над одним сокетом.
// ---------------------------------------------------------------------------

struct LiveTradeWs {
    runtime: tokio::runtime::Runtime,
    stream: WsStream,
}

impl LiveTradeWs {
    fn connect(url: &str, recv_window_ms: u32, wait: Duration) -> Result<Self, LatencyError> {
        let creds = Credentials::from_env()
            .map_err(|e| LatencyError::Transport(format!("ключи: {e:?}")))?;
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|e| LatencyError::Transport(e.to_string()))?;
        let stream = runtime.block_on(connect_authed(url, &creds, recv_window_ms, wait))?;
        Ok(Self { runtime, stream })
    }
}

impl TradeWs for LiveTradeWs {
    fn send(&mut self, frame: &str) -> Result<(), LatencyError> {
        let msg = Message::Text(frame.to_string().into());
        self.runtime
            .block_on(self.stream.send(msg))
            .map_err(|e| LatencyError::Transport(e.to_string()))
    }
    fn recv(&mut self, timeout: Duration) -> Result<String, LatencyError> {
        self.runtime
            .block_on(next_text(&mut self.stream, timeout))?
            .ok_or(LatencyError::Timeout("кадр WS trade"))
    }
}

// ---------------------------------------------------------------------------
// Приватный стрим: поток-читатель штампует приём каждого кадра.
// ---------------------------------------------------------------------------

struct LivePrivate {
    to_socket: tokio::sync::mpsc::UnboundedSender<String>,
    from_socket: mpsc::Receiver<(Instant, String)>,
}

impl LivePrivate {
    fn connect(url: &str, recv_window_ms: u32, wait: Duration) -> Result<Self, LatencyError> {
        let creds = Credentials::from_env()
            .map_err(|e| LatencyError::Transport(format!("ключи: {e:?}")))?;
        let (to_socket, mut cmd_rx) = tokio::sync::mpsc::unbounded_channel::<String>();
        let (frame_tx, from_socket) = mpsc::channel::<(Instant, String)>();
        let (ready_tx, ready_rx) = mpsc::channel::<Result<(), LatencyError>>();
        let url = url.to_string();
        std::thread::Builder::new()
            .name("alpha-private-ws".to_string())
            .spawn(move || {
                let runtime = match tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                {
                    Ok(r) => r,
                    Err(e) => {
                        let _ = ready_tx.send(Err(LatencyError::Transport(e.to_string())));
                        return;
                    }
                };
                runtime.block_on(async move {
                    let mut stream = match connect_authed(&url, &creds, recv_window_ms, wait).await
                    {
                        Ok(s) => s,
                        Err(e) => {
                            let _ = ready_tx.send(Err(e));
                            return;
                        }
                    };
                    let sub = r#"{"op":"subscribe","args":["order","execution"]}"#;
                    if let Err(e) = stream.send(Message::Text(sub.into())).await {
                        let _ = ready_tx.send(Err(LatencyError::Transport(e.to_string())));
                        return;
                    }
                    match next_text(&mut stream, wait).await {
                        Ok(Some(raw)) if raw.contains("\"success\":true") => {}
                        Ok(Some(raw)) => {
                            let _ = ready_tx.send(Err(LatencyError::Transport(format!(
                                "subscribe отклонён: {raw}"
                            ))));
                            return;
                        }
                        Ok(None) => {
                            let _ = ready_tx.send(Err(LatencyError::Timeout("ответ subscribe")));
                            return;
                        }
                        Err(e) => {
                            let _ = ready_tx.send(Err(e));
                            return;
                        }
                    }
                    let _ = ready_tx.send(Ok(()));
                    loop {
                        tokio::select! {
                            cmd = cmd_rx.recv() => match cmd {
                                None => break,
                                Some(text) => {
                                    if stream.send(Message::Text(text.into())).await.is_err() {
                                        break;
                                    }
                                }
                            },
                            msg = stream.next() => match msg {
                                Some(Ok(Message::Text(t))) => {
                                    let at = Instant::now();
                                    if frame_tx.send((at, t.as_str().to_string())).is_err() {
                                        break;
                                    }
                                }
                                Some(Ok(Message::Close(_))) | None | Some(Err(_)) => break,
                                Some(Ok(_)) => {}
                            },
                        }
                    }
                });
            })
            .map_err(|e| LatencyError::Transport(e.to_string()))?;
        match ready_rx.recv_timeout(wait + HTTP_TIMEOUT) {
            Ok(Ok(())) => Ok(Self {
                to_socket,
                from_socket,
            }),
            Ok(Err(e)) => Err(e),
            Err(_) => Err(LatencyError::Timeout("подключение приватного стрима")),
        }
    }
}

impl PrivateSource for LivePrivate {
    fn send(&mut self, frame: &str) -> Result<(), LatencyError> {
        self.to_socket
            .send(frame.to_string())
            .map_err(|_| LatencyError::Transport("приватный стрим закрыт".to_string()))
    }
    fn recv_timeout(
        &mut self,
        timeout: Duration,
    ) -> Result<Option<(Instant, String)>, LatencyError> {
        match self.from_socket.recv_timeout(timeout) {
            Ok(x) => Ok(Some(x)),
            Err(mpsc::RecvTimeoutError::Timeout) => Ok(None),
            Err(mpsc::RecvTimeoutError::Disconnected) => Err(LatencyError::Transport(
                "приватный стрим закрыт".to_string(),
            )),
        }
    }
}

// ---------------------------------------------------------------------------
// Прогон.
// ---------------------------------------------------------------------------

pub struct LatencyReport {
    pub out: PathBuf,
    pub qty_e9: i64,
    pub notional_e9: i64,
    pub summary: String,
    pub errors: Vec<String>,
    pub flattened_e9: Option<i64>,
}

fn resolve_endpoints(args: &LatencyArgs) -> anyhow::Result<(String, String, String)> {
    let base: Endpoints = endpoints_for(&args.net).ok_or_else(|| {
        anyhow::anyhow!(
            "--net обязан быть mainnet|testnet|demo, получено {}",
            args.net
        )
    })?;
    Ok((
        args.rest_url
            .clone()
            .unwrap_or_else(|| base.rest.to_string()),
        args.ws_trade_url
            .clone()
            .unwrap_or_else(|| base.ws_trade.to_string()),
        args.ws_private_url
            .clone()
            .unwrap_or_else(|| base.ws_private.to_string()),
    ))
}

pub fn run_latency(args: &LatencyArgs) -> anyhow::Result<LatencyReport> {
    if args.cycles == 0 {
        return Err(anyhow::anyhow!("--cycles обязан быть ≥ 1"));
    }
    if args.notional_usd <= 0 {
        return Err(anyhow::anyhow!("--notional-usd обязан быть > 0"));
    }
    let side = parse_side(&args.side)?;
    let (rest_url, ws_trade_url, ws_private_url) = resolve_endpoints(args)?;
    let wait = Duration::from_millis(u64::from(args.recv_window_ms));

    let mut rest =
        LiveRest::new(&rest_url, args.recv_window_ms).map_err(|e| anyhow::anyhow!("{e}"))?;

    // Фильтры инструмента и размер под номинал — по текущей середине.
    let body = rest
        .get_public(
            INSTRUMENTS_PATH,
            &format!("category=linear&symbol={}", args.symbol),
        )
        .map_err(|e| anyhow::anyhow!("instruments-info: {e}"))?;
    let filters = parse_filters(&body, &args.symbol).map_err(|e| anyhow::anyhow!("{e}"))?;
    let book = rest
        .get_public(
            ORDERBOOK_PATH,
            &format!("category=linear&symbol={}&limit=1", args.symbol),
        )
        .map_err(|e| anyhow::anyhow!("orderbook: {e}"))?;
    let mid =
        crate::bybit::latency::mid_from_orderbook(&book).map_err(|e| anyhow::anyhow!("{e}"))?;
    let notional = args.notional_usd * 1_000_000_000;
    let qty_e9 = qty_for_notional(notional, mid, filters).map_err(|e| anyhow::anyhow!("{e}"))?;

    let private = LivePrivate::connect(&ws_private_url, args.recv_window_ms, wait)
        .map_err(|e| anyhow::anyhow!("приватный стрим: {e}"))?;
    let mut feed = Feed::new(private);
    let mut errors = Vec::new();
    let mut trade = if args.skip_ws_trade {
        None
    } else {
        match LiveTradeWs::connect(&ws_trade_url, args.recv_window_ms, wait) {
            Ok(t) => Some(t),
            Err(e) => {
                errors.push(format!("WS trade не открыт, ступени via=ws пропущены: {e}"));
                None
            }
        }
    };

    let plan = Plan {
        symbol: args.symbol.clone(),
        side,
        qty_e9,
        filters,
        ticks_from_mid: args.ticks_from_mid,
        recv_window_ms: args.recv_window_ms,
        wait,
        taker: !args.skip_taker,
    };
    let mut bench = Bench::new(&mut rest, trade.as_mut(), &mut feed, &plan);
    bench.errors.append(&mut errors);
    for cycle in 0..args.cycles {
        let taker_failed = bench.run_cycle(cycle);
        if taker_failed {
            match bench.flatten() {
                Ok(Some(size)) => bench.errors.push(format!(
                    "cycle {cycle}: закрыт остаток {}",
                    crate::bybit::latency::format_e9(size)
                )),
                Ok(None) => {}
                Err(e) => bench.errors.push(format!("cycle {cycle}: flatten: {e}")),
            }
        }
        if cycle + 1 < args.cycles {
            std::thread::sleep(Duration::from_millis(args.pause_ms));
        }
    }
    let flattened_e9 = match bench.flatten() {
        Ok(x) => x,
        Err(e) => {
            bench.errors.push(format!(
                "финальный flatten: {e} — ПРОВЕРЬТЕ ПОЗИЦИЮ ВРУЧНУЮ"
            ));
            None
        }
    };

    let out = args.out.clone().unwrap_or_else(|| {
        PathBuf::from("data/bybit").join(format!(
            "latency-{}-{}.csv",
            args.symbol,
            chrono::Utc::now().format("%Y%m%dT%H%M%SZ")
        ))
    });
    if let Some(parent) = out.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
    }
    let mut w = csv::Writer::from_path(&out)?;
    w.write_record(["cycle", "stage", "via", "ns"])?;
    for s in &bench.samples {
        w.write_record([
            s.cycle.to_string(),
            s.stage.to_string(),
            s.via.to_string(),
            s.ns.to_string(),
        ])?;
    }
    w.flush()?;
    let summary = format_summary(&summarize(&bench.samples));
    let errors = std::mem::take(&mut bench.errors);
    Ok(LatencyReport {
        out,
        qty_e9,
        notional_e9: notional_e9(qty_e9, mid),
        summary,
        errors,
        flattened_e9,
    })
}
