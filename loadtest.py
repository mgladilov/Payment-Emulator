"""Нагрузочный тест агентского API.

Гонит реалистичный микс запросов (check → pay → status×2) с заданной
конкурентностью и считает RPS, латентности (p50/p95/p99), распределение
кодов и число ошибок (в т.ч. 5xx — их даёт SQLite при контеншене записи).

Требует запущенный сервер. Пример:
    .venv/bin/python loadtest.py --url http://127.0.0.1:8096 --concurrency 30 --iterations 20
"""
import argparse
import asyncio
import time
import uuid
from collections import Counter

import httpx

REQUISITES = [
    "4111111111110001",  # instant success
    "4111111111110002",  # instant decline
    "4111111111110003",  # delayed success
    "4276000011119999",  # default success
]


async def worker(client, iterations, samples):
    for _ in range(iterations):
        requisite = REQUISITES[uuid.uuid4().int % len(REQUISITES)]

        for coro, label in (
            (client.post("/check", json={"requisite": requisite, "amount": 10000}), "check"),
            (
                client.post(
                    "/pay",
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                    json={"requisite": requisite, "amount": 10000},
                ),
                "pay",
            ),
        ):
            t = time.perf_counter()
            try:
                r = await coro
                samples.append((label, r.status_code, time.perf_counter() - t))
                pid = r.json().get("payment_id") if label == "pay" else None
            except Exception as exc:  # noqa: BLE001
                samples.append((label, f"EXC:{type(exc).__name__}", time.perf_counter() - t))
                pid = None

        if pid:
            for _ in range(2):
                t = time.perf_counter()
                try:
                    r = await client.get(f"/status/{pid}")
                    samples.append(("status", r.status_code, time.perf_counter() - t))
                except Exception as exc:  # noqa: BLE001
                    samples.append(("status", f"EXC:{type(exc).__name__}", time.perf_counter() - t))


def pct(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    k = min(len(values) - 1, int(round((p / 100) * (len(values) - 1))))
    return values[k]


def report(samples, duration):
    total = len(samples)
    codes = Counter(s[1] for s in samples)
    errors = sum(n for c, n in codes.items() if not (isinstance(c, int) and c < 500))
    print("\n" + "=" * 60)
    print("НАГРУЗОЧНЫЙ ТЕСТ — РЕЗУЛЬТАТ")
    print("=" * 60)
    print(f"  Запросов всего:   {total}")
    print(f"  Длительность:     {duration:.2f} c")
    print(f"  Пропускная спос.: {total / duration:.0f} req/s")
    print(f"  Ошибки (5xx/exc): {errors}")
    print("\n  Коды ответов:")
    for code, n in sorted(codes.items(), key=lambda x: str(x[0])):
        print(f"    {code}: {n}")
    print("\n  Латентность по эндпоинтам (мс):")
    print(f"    {'endpoint':<10}{'n':>7}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}")
    for ep in ("check", "pay", "status"):
        lat = [s[2] * 1000 for s in samples if s[0] == ep and isinstance(s[1], int)]
        if lat:
            print(
                f"    {ep:<10}{len(lat):>7}{pct(lat,50):>9.1f}{pct(lat,95):>9.1f}"
                f"{pct(lat,99):>9.1f}{max(lat):>9.1f}"
            )
    print("=" * 60)
    return errors


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8096")
    ap.add_argument("--concurrency", type=int, default=30)
    ap.add_argument("--iterations", type=int, default=20)
    ap.add_argument("--user", default="agent")
    ap.add_argument("--password", default="agent-secret")
    args = ap.parse_args()

    limits = httpx.Limits(max_connections=args.concurrency + 10, max_keepalive_connections=args.concurrency)
    samples: list = []
    async with httpx.AsyncClient(
        base_url=args.url, auth=(args.user, args.password), timeout=30.0, limits=limits
    ) as client:
        print(
            f"Старт: concurrency={args.concurrency}, iterations={args.iterations} "
            f"(~{args.concurrency * args.iterations * 4} запросов) -> {args.url}"
        )
        start = time.perf_counter()
        await asyncio.gather(*(worker(client, args.iterations, samples) for _ in range(args.concurrency)))
        duration = time.perf_counter() - start

    errors = report(samples, duration)
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    asyncio.run(main())
