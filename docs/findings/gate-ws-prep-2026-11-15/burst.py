import asyncio, time, sys, websockets, json
URL="wss://fx-ws.gateio.ws/v4/ws/usdt"
N=int(sys.argv[1]); GAP=float(sys.argv[2]); HOLD=float(sys.argv[3])
res=[]
async def one(i):
    await asyncio.sleep(i*GAP)
    t0=time.time()
    try:
        async with websockets.connect(URL, additional_headers={"X-Gate-Size-Decimal":"1"}, ping_interval=None, compression=None, open_timeout=20) as ws:
            dt=time.time()-t0
            await ws.send(json.dumps({"time":int(time.time()),"channel":"futures.tickers","event":"subscribe","payload":["BTC_USDT"]}))
            m=await asyncio.wait_for(ws.recv(),10); ok=json.loads(m).get("result",{}).get("status")
            await asyncio.sleep(HOLD)
            res.append((i,round(dt,2),ok,"held"))
    except Exception as e:
        res.append((i,round(time.time()-t0,2),None,repr(e)[:120]))
async def main():
    await asyncio.gather(*(one(i) for i in range(N)))
    bad=[r for r in res if r[2]!="success" or r[3]!="held"]
    ts=sorted(r[1] for r in res)
    print(f"N={N} gap={GAP}s hold={HOLD}s: ok {len(res)-len(bad)} bad {len(bad)}; connect+ws time p50 {ts[len(ts)//2]} max {ts[-1]}")
    for b in bad[:8]: print("  BAD",b)
asyncio.run(main())
