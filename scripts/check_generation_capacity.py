"""Opt-in synthetic HTTP admission check; never connects to a running server.

Run from the checkout: python -m scripts.check_generation_capacity --users 150

Creates a temporary SQLite app and synthetic accounts/sessions, replaces only
the in-process DH generator, and submits a concurrent burst through ASGI. Held
generation lets us check immediate rejection and independent reads. No network,
real database, real accounts, or cryptographic generation is used. This is not
a hardware-capacity benchmark, PostgreSQL load test, or network-latency test.
Sessions are seeded directly; password hashing and login load are not tested.
"""
import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory
from threading import Event, Lock
import time
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import httpx

from crucible import dh_v7
from crucible.app import create_app
from crucible.config import Settings
from crucible.db import LoginSession, User
from crucible.security import mac, token_hash


async def check_capacity(users=150, slots=2, timeout=30.0):
    if type(users) is not int or not 2 <= users <= 500:
        raise ValueError("Use between 2 and 500 synthetic users")
    if type(slots) is not int or not 1 <= slots < users or slots > 32:
        raise ValueError("Slots must be between 1 and 32 and fewer than users")
    if not 1 <= timeout <= 120:
        raise ValueError("Timeout must be between 1 and 120 seconds")
    now = 3_600_600.0
    origin = "http://capacity.test"
    release, entered, mutex = Event(), Event(), Lock()
    calls = 0

    def synthetic_generate(profile_id):
        nonlocal calls
        with mutex:
            calls += 1
            if calls == slots:
                entered.set()
        if not release.wait(timeout + 5):
            raise RuntimeError("Synthetic generator was not released")
        return SimpleNamespace(public={"synthetic_test_only": True, "profile_id": profile_id},
                               target=b"s" * 32, diagnostics={"shared_secret_matches": True})

    with TemporaryDirectory(prefix="crucible-capacity-") as directory:
        settings = Settings(f"sqlite:///{Path(directory) / 'synthetic.db'}", secrets.token_bytes(32),
                            origins=(origin,), rate_limits=False, generation_concurrency=slots)
        app = create_app(settings, clock=lambda: now, initialize=True)
        request_headers = []
        try:
            with app.state.db.transaction(write=True) as session:
                accounts = []
                for index in range(users):
                    account_id, token = str(uuid4()), secrets.token_urlsafe(32)
                    session.add(User(id=account_id, handle=f"synthetic_{index}",
                                     password_hash="!synthetic-no-password", created_at=now))
                    accounts.append((account_id, token))
                    request_headers.append({
                        "Cookie": f"{settings.cookie_name}={token}",
                        "Origin": origin,
                        "X-CSRF-Token": mac(settings.secret, "csrf-v1", token.encode("ascii")),
                    })
                session.flush()
                session.add_all(LoginSession(token_hash=token_hash(token), user_id=account_id,
                                             expires_at=now + 3600) for account_id, token in accounts)
            body = {"scheme_id": dh_v7.SCHEME_ID, "scheme_version": dh_v7.SCHEME_VERSION,
                    "profile_id": dh_v7.manifests()[0]["id"]}
            with patch.object(dh_v7, "generate", synthetic_generate):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=origin) as client:
                    tasks = [asyncio.create_task(client.post("/api/challenges", json=body, headers=headers))
                             for headers in request_headers]
                    read_results = []
                    try:
                        async with asyncio.timeout(timeout):
                            while not entered.is_set():
                                await asyncio.sleep(0.005)
                            pending = set(tasks)
                            while len(pending) > slots:
                                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                                for task in done:
                                    response = task.result()
                                    if response.status_code != 429 or response.headers.get("retry-after") != "3":
                                        raise AssertionError("Saturated requests must return 429 with Retry-After")
                            for path in ("/health", "/api/session", "/api/schemes"):
                                started = time.monotonic()
                                response = await client.get(path, headers=request_headers[0])
                                if response.status_code != 200:
                                    raise AssertionError(f"Independent read failed: {path}")
                                read_results.append({"path": path, "status": response.status_code,
                                                     "seconds": round(time.monotonic() - started, 6)})
                            if any(task.done() for task in pending):
                                raise AssertionError("Accepted generation must still be held during reads")
                    finally:
                        release.set()
                        responses = await asyncio.gather(*tasks, return_exceptions=True)
                    if any(isinstance(response, BaseException) for response in responses):
                        raise AssertionError("A synthetic HTTP request failed before receiving a response")
                    statuses = Counter(response.status_code for response in responses)
                    if statuses != Counter({200: slots, 429: users - slots}) or calls != slots:
                        raise AssertionError(f"Unexpected admission counts: {dict(statuses)}")
            snapshot = app.state.generation_limiter.snapshot()
            if snapshot["inflight"] != 0 or snapshot["peak_inflight"] != slots:
                raise AssertionError("Generation capacity was exceeded or leaked")
            return {
                "scope": "Synthetic ASGI admission check; not hosted throughput or cryptographic performance",
                "users": users, "slots": slots, "response_counts": dict(statuses),
                "independent_reads_while_busy": read_results, "limiter": snapshot,
                "database": "Temporary SQLite database; deleted after check",
            }
        finally:
            release.set()
            app.state.db.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=150)
    parser.add_argument("--slots", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(check_capacity(args.users, args.slots, args.timeout)), indent=2))


if __name__ == "__main__":
    main()
