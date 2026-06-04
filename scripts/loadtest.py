# scripts/loadtest.py
"""Quick MCP load test. Usage:
    python scripts/loadtest.py --url https://grundschutz.cyber.hn --users 20 --duration 120
"""
import argparse, asyncio, random, statistics, time
from collections import defaultdict
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

QUERIES = ["Ransomware", "Backup", "Phishing", "Verschlüsselung", "Notfallplan",
           "Cloud Security", "Patch Management", "Datenschutz", "Zugriffskontrolle"]
MODULES = ["CON.3", "OPS.1.1.5", "ORP.4", "NET.1.1", "ISMS.1", "APP.4.6", "SYS.3.1"]


async def one_user(url, sid, stop_at, stats):
    try:
        async with streamable_http_client(f"{url}/mcp") as (read, write, _):
            async with ClientSession(read, write) as s:
                await s.initialize()
                while time.monotonic() < stop_at:
                    plan = [
                        ("search",          {"query": random.choice(QUERIES), "limit": 5}),
                        ("get_module",      {"code":  random.choice(MODULES)}),
                        ("list_layers",     {}),
                        ("get_requirement", {"code":  "CON.3.A6"}),
                    ]
                    for tool, args in plan:
                        if time.monotonic() >= stop_at: return
                        t0 = time.monotonic()
                        try:
                            await s.call_tool(tool, args)
                            stats[tool].append(time.monotonic() - t0)
                        except Exception as e:
                            stats[f"{tool}.err"].append(str(e)[:80])
                    await asyncio.sleep(random.uniform(5, 30))   # think-time
    except Exception as e:
        stats["session.err"].append(str(e)[:80])


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url",     default="https://grundschutz.cyber.hn")
    p.add_argument("--users",   type=int, default=10)
    p.add_argument("--duration",type=int, default=120)
    a = p.parse_args()
    print(f"=== {a.users} users x {a.duration}s -> {a.url} ===")

    stats = defaultdict(list)
    stop = time.monotonic() + a.duration
    await asyncio.gather(*(one_user(a.url, i, stop, stats) for i in range(a.users)))

    print(f"\n=== Results ===")
    for k, v in sorted(stats.items()):
        if k.endswith(".err"):
            print(f"  {k}: {len(v)} errors  ({v[0] if v else ''})")
            continue
        if not v: continue
        ms = lambda x: x*1000
        p50 = ms(statistics.median(v))
        p95 = ms(statistics.quantiles(v, n=20)[18]) if len(v)>=20 else ms(max(v))
        print(f"  {k:20s}  n={len(v):4d}  p50={p50:6.0f}ms  p95={p95:6.0f}ms")

asyncio.run(main())
