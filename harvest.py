#!/usr/bin/env python3
"""
harvest.py -- portable mind-reader client (stdlib HTTP only).

Works from ANY agent environment (Claude Code, Codex, Cursor, generic
sandbox, plain terminal) -- no aiohttp/pympler/psutil needed on the client
side. Just Python 3 stdlib.

  python harvest.py harvest <url> [--token T] [--out bundle.zip]
      -> fetches /harvest (one zip = everything) and saves it
  python harvest.py snapshot <url> [--token T]
      -> prints the deep snapshot JSON
  python harvest.py dashboard <url> [--out dashboard.png]
      -> fetches /dashboard.png confirmation image
  python harvest.py verify <url>
      -> hits /health, /discover, /harvest?format=json summary; prints pass/fail
"""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error


def _get(url, token=None, timeout=120):
    req = urllib.request.Request(url)
    if token:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}token={token}"
        req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read()


def cmd_harvest(args):
    status, headers, body = _get(args.url.rstrip("/") + "/harvest", args.token)
    out = args.out or f"mind_reader_harvest_{int(__import__('time').time())}.zip"
    with open(out, "wb") as f:
        f.write(body)
    size_mb = len(body) / (1024 * 1024)
    print(f"[harvest] HTTP {status} -> {out} ({size_mb:.2f} MB)")
    # print manifest summary if present by peeking (zip; skip)
    print(f"[harvest] bundle saved. Unzip and open manifest.json / dashboard.png to review.")


def cmd_snapshot(args):
    status, headers, body = _get(args.url.rstrip("/") + "/snapshot?deep=1", args.token)
    print(body.decode())


def cmd_dashboard(args):
    status, headers, body = _get(args.url.rstrip("/") + "/dashboard.png", args.token)
    out = args.out or "dashboard.png"
    with open(out, "wb") as f:
        f.write(body)
    print(f"[dashboard] HTTP {status} -> {out} ({len(body)} bytes, {headers.get('Content-Type')})")


def cmd_verify(args):
    base = args.url.rstrip("/")
    results = {}
    # health
    try:
        s, _, b = _get(base + "/health", timeout=15)
        results["health"] = json.loads(b.decode())
    except Exception as e:
        results["health"] = {"error": str(e)}
    # discover
    try:
        s, _, b = _get(base + "/discover", timeout=30)
        d = json.loads(b.decode())
        results["discover"] = {
            "frameworks": d["frameworks"]["framework_count"],
            "subsystems_present": d["subsystems"]["present_count"],
        }
    except Exception as e:
        results["discover"] = {"error": str(e)}
    # harvest json summary
    try:
        s, _, b = _get(base + "/harvest?format=json", args.token, timeout=120)
        h = json.loads(b.decode())
        results["harvest"] = h.get("manifest", {}).get("headline", {})
        results["harvest"]["spill_objects"] = h.get("spill_summary", {}).get("objects_dumped")
    except Exception as e:
        results["harvest"] = {"error": str(e)}
    print(json.dumps(results, indent=2))
    ok = all("error" not in v for v in results.values())
    print("VERIFY:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(prog="mind_reader.harvest")
    sub = ap.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("harvest")
    h.add_argument("url")
    h.add_argument("--token", default=None)
    h.add_argument("--out", default=None)
    h.set_defaults(fn=cmd_harvest)

    s = sub.add_parser("snapshot")
    s.add_argument("url")
    s.add_argument("--token", default=None)
    s.set_defaults(fn=cmd_snapshot)

    d = sub.add_parser("dashboard")
    d.add_argument("url")
    d.add_argument("--token", default=None)
    d.add_argument("--out", default=None)
    d.set_defaults(fn=cmd_dashboard)

    v = sub.add_parser("verify")
    v.add_argument("url")
    v.add_argument("--token", default=None)
    v.set_defaults(fn=cmd_verify)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
