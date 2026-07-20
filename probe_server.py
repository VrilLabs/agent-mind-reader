"""
probe_server.py -- Agent Memory Probe HTTP/WebSocket Server (v3)

Endpoints:
  GET  /                       -> HTML dashboard (screenshot-able confirmation)
  GET  /dashboard              -> HTML dashboard
  GET  /dashboard.png          -> server-rendered PNG (or SVG fallback)
  GET  /snapshot               -> JSON memory snapshot
  GET  /snapshot?deep=1        -> + module agg + pympler deep + deep gql attribution
  WS   /stream                 -> continuous live snapshot stream (for TUI)
  POST /spill/full             -> catastrophic-meltdown-spill (entire live memory)
  POST /spill/full?inline=1    -> stream the dump back directly as ndjson
  POST /spill/graphql          -> GraphQL-only memory spill
  GET  /spill/list             -> list spill files on this host
  GET  /spill/download?name=   -> download a spill file (path-traversal safe)
  GET  /routes                 -> live aiohttp route table (legacy; /discover is generic)
  GET  /discover               -> generic framework + subsystem discovery (v3)
  GET  /harvest                -> ONE zip = everything (health/snapshot/discover/routes/
                                  stacks/net/spill/dashboard.png/manifest) [token-gated]
  GET  /harvest?format=json    -> consolidated JSON (no big spill) [token-gated]
  GET  /refs?id=<int>          -> referrers + referents + deep size for one object
  GET  /stacks                 -> live thread list + per-thread call stacks
  GET  /net                    -> process net_connections + gc socket objects
  GET  /health                 -> liveness probe

Optional shared-secret gate: set MIND_READER_TOKEN env; then /harvest and /spill/*
require ?token=<value>. /snapshot, /health, /dashboard remain open.
"""
import asyncio
import io
import json
import os
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(__file__))
from memory_surface import MemorySurface
import dashboard

from aiohttp import web

SPILL_DIR = os.environ.get("MIND_READER_SPILL_DIR", "spills")
os.makedirs(SPILL_DIR, exist_ok=True)
TOKEN = os.environ.get("MIND_READER_TOKEN") or None
ENDPOINT_URL = os.environ.get("MIND_READER_ENDPOINT_URL") or ""  # set by serve.py for display

surface = MemorySurface()
_APP = None


# ---------- auth helper ----------
def _check_token(request, gated: bool) -> bool:
    if not gated or not TOKEN:
        return True
    return request.query.get("token") == TOKEN


def _deny(request):
    return web.json_response({"error": "forbidden: ?token= required"}, status=403)


# ---------- handlers ----------
async def handle_snapshot(request):
    deep = request.query.get("deep") in ("1", "true", "yes")
    return web.json_response(surface.snapshot(deep=deep))


async def handle_health(request):
    return web.json_response({
        "status": "alive", "pid": os.getpid(),
        "version": "v3", "endpoints": sorted(_ROUTE_NAMES),
        "token_gated": bool(TOKEN),
    })


def _spill_path_safe(name):
    if not name:
        return None
    base = os.path.basename(name)
    if base != name or not base.endswith(".jsonl"):
        return None
    path = os.path.realpath(os.path.join(SPILL_DIR, base))
    if not path.startswith(os.path.realpath(SPILL_DIR) + os.sep):
        return None
    if not os.path.isfile(path):
        return None
    return path


async def handle_spill_list(request):
    if not _check_token(request, True):
        return _deny(request)
    files = []
    try:
        for name in sorted(os.listdir(SPILL_DIR)):
            if not name.endswith(".jsonl"):
                continue
            p = os.path.join(SPILL_DIR, name)
            if os.path.isfile(p):
                files.append({
                    "name": name,
                    "size_bytes": os.path.getsize(p),
                    "size_mb": round(os.path.getsize(p) / (1024 * 1024), 3),
                    "mtime_unix": os.path.getmtime(p),
                    "download_url": f"/spill/download?name={name}",
                })
    except FileNotFoundError:
        pass
    return web.json_response({"dir": SPILL_DIR, "count": len(files), "spills": files})


async def handle_spill_download(request):
    if not _check_token(request, True):
        return _deny(request)
    name = request.query.get("name", "")
    path = _spill_path_safe(name)
    if path is None:
        return web.json_response(
            {"error": "invalid or missing 'name'; must be a .jsonl file in the spills dir"},
            status=404,
        )
    return web.FileResponse(
        path,
        headers={
            "Content-Type": "application/x-ndjson",
            "Content-Disposition": f'attachment; filename="{os.path.basename(path)}"',
        },
    )


async def handle_spill_full(request):
    if not _check_token(request, True):
        return _deny(request)
    inline = request.query.get("inline") in ("1", "true", "yes")
    dump = surface.full_meltdown_spill()
    if inline:
        body = "\n".join(json.dumps(rec, default=str) for rec in dump).encode()
        return web.Response(
            body=body,
            headers={
                "Content-Type": "application/x-ndjson",
                "Content-Disposition": 'attachment; filename="meltdown_spill.jsonl"',
                "X-Objects-Dumped": str(len(dump)),
            },
        )
    fname = f"meltdown_spill_{int(time.time())}.jsonl"
    path = os.path.join(SPILL_DIR, fname)
    with open(path, "w") as f:
        for rec in dump:
            f.write(json.dumps(rec, default=str) + "\n")
    return web.json_response({
        "spill_type": "catastrophic_meltdown_full",
        "objects_dumped": len(dump),
        "file": path,
        "file_size_bytes": os.path.getsize(path),
        "download_url": f"/spill/download?name={fname}",
    })


async def handle_spill_graphql(request):
    if not _check_token(request, True):
        return _deny(request)
    dump = surface.graphql_memory_spill()
    fname = f"graphql_spill_{int(time.time())}.jsonl"
    path = os.path.join(SPILL_DIR, fname)
    with open(path, "w") as f:
        for rec in dump:
            f.write(json.dumps(rec, default=str) + "\n")
    return web.json_response({
        "spill_type": "graphql_only",
        "objects_dumped": len(dump),
        "file": path,
        "file_size_bytes": os.path.getsize(path),
        "download_url": f"/spill/download?name={fname}",
    })


async def handle_stream(request):
    ws = web.WebSocketResponse(heartbeat=15)
    await ws.prepare(request)
    try:
        while True:
            await ws.send_json(surface.snapshot(deep=False))
            await asyncio.sleep(1.0)
    except Exception:
        pass
    finally:
        await ws.close()
    return ws


async def handle_routes(request):
    if _APP is None:
        return web.json_response({"error": "app not built"}, status=503)
    return web.json_response(surface.routes_surface(_APP))


async def handle_discover(request):
    return web.json_response({
        "frameworks": surface.discover_frameworks(),
        "subsystems": surface.discover_subsystems(),
        "extract": surface.extract_state_surface(),
    })


async def handle_extract(request):
    """GET /extract -> live state of the bundled graphql_deep_extract engine."""
    return web.json_response(surface.extract_state_surface())


async def handle_extract_run(request):
    """POST /extract/run  (token-gated) -- drive the extraction engine remotely.

    ?action=introspect&source=countries:trevorblades
    ?action=run&source=countries:trevorblades&operation=countries.snapshot.v1&vars=key=val

    Makes an outbound GraphQL call to the configured endpoint. Returns the
    new extraction state so the operator sees the capture immediately.
    """
    if not _check_token(request, True):
        return _deny(request)
    action = request.query.get("action", "introspect")
    source_id = request.query.get("source")
    if not source_id:
        return web.json_response({"error": "?source=<id> required"}, status=400)
    try:
        import extract.deep_extract as de
    except Exception as e:
        return web.json_response({
            "error": f"extract engine not importable: {e}",
            "hint": "pip install requests pyyaml",
        }, status=503)
    try:
        loop = asyncio.get_event_loop()
        if action == "introspect":
            await loop.run_in_executor(None, de.introspect, source_id)
            msg = f"introspected {source_id}"
        elif action == "capture":
            operation_id = request.query.get("operation")
            if not operation_id:
                return web.json_response({"error": "?operation=<id> required for capture"}, status=400)
            await loop.run_in_executor(None, de.capture, source_id, operation_id)
            msg = f"captured {operation_id} from {source_id}"
        elif action == "run":
            operation_id = request.query.get("operation")
            if not operation_id:
                return web.json_response({"error": "?operation=<id> required for run"}, status=400)
            variables = {}
            for kv in request.query.get("vars", "").split(","):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    variables[k] = v
            await loop.run_in_executor(None, lambda: de.run(source_id, operation_id, variables))
            msg = f"ran {operation_id} on {source_id}"
        else:
            return web.json_response({"error": f"unknown action: {action}"}, status=400)
    except Exception as e:
        return web.json_response({"error": str(e), "action": action, "source": source_id}, status=500)
    return web.json_response({"ok": True, "message": msg, "extract": surface.extract_state_surface()})


async def handle_refs(request):
    raw = request.query.get("id")
    if raw is None:
        return web.json_response({"error": "?id=<int> required"}, status=400)
    try:
        target_id = int(raw)
    except ValueError:
        return web.json_response({"error": "id must be an int"}, status=400)
    return web.json_response(surface.refs_surface(target_id))


async def handle_stacks(request):
    return web.json_response(surface.stacks_surface())


async def handle_net(request):
    return web.json_response(surface.net_surface())


# ---------- visual confirmation ----------
async def handle_dashboard_html(request):
    snap = surface.snapshot(deep=True)
    html = dashboard.render_html(snap, ENDPOINT_URL)
    return web.Response(text=html, content_type="text/html")


async def handle_dashboard_png(request):
    snap = surface.snapshot(deep=True)
    ctype, data = dashboard.render_png(snap, ENDPOINT_URL)
    return web.Response(body=data, content_type=ctype)


# ---------- universal one-shot harvest ----------
def _build_manifest(snap, discover, urls):
    return {
        "probe_version": "v3",
        "endpoint_url": ENDPOINT_URL,
        "captured_at_unix": time.time(),
        "pid": snap["process"]["pid"],
        "headline": {
            "rss_mb": snap["process"]["rss_mb"],
            "tracked_objects": snap["gc"]["total_tracked_objects"],
            "shallow_heap_mb": snap["gc"]["total_shallow_mb"],
            "graphql_objects": snap["graphql_composition"]["graphql_object_count"],
            "frameworks_detected": discover["frameworks"]["framework_count"],
            "subsystems_present": discover["subsystems"]["present_count"],
        },
        "files": [
            "health.json", "snapshot_deep.json", "discover.json", "routes.json",
            "stacks.json", "net.json", "spill_full.jsonl", "dashboard.png",
        ],
        "urls": urls,
    }


async def handle_harvest(request):
    if not _check_token(request, True):
        return _deny(request)

    fmt = request.query.get("format", "zip")
    snap = surface.snapshot(deep=True)
    discover = {
        "frameworks": surface.discover_frameworks(),
        "subsystems": surface.discover_subsystems(),
    }
    routes = surface.routes_surface(_APP) if _APP is not None else {"error": "app not built"}
    stacks = surface.stacks_surface()
    net = surface.net_surface()
    extract_state = surface.extract_state_surface()
    health = {"status": "alive", "pid": os.getpid(), "version": "v3"}
    _, png = dashboard.render_png(snap, ENDPOINT_URL)

    if fmt == "json":
        # consolidated JSON, no big spill (use spill_summary instead)
        spill = surface.full_meltdown_spill()
        by_type = {}
        for r in spill:
            by_type[r["type"]] = by_type.get(r["type"], 0) + 1
        consolidated = {
            "health": health,
            "snapshot": snap,
            "discover": discover,
            "extract": extract_state,
            "routes": routes,
            "stacks": stacks,
            "net": net,
            "spill_summary": {
                "objects_dumped": len(spill),
                "types": dict(sorted(by_type.items(), key=lambda kv: -kv[1])[:20]),
            },
            "manifest": _build_manifest(snap, discover, {
                "snapshot": "/snapshot?deep=1", "discover": "/discover",
                "routes": "/routes", "stacks": "/stacks", "net": "/net",
                "spill_full_inline": "/spill/full?inline=1", "dashboard_png": "/dashboard.png",
                "harvest_zip": "/harvest",
            }),
        }
        return web.json_response(consolidated)

    # default: zip bundle
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("health.json", json.dumps(health, indent=2))
        z.writestr("snapshot_deep.json", json.dumps(snap, indent=2))
        z.writestr("discover.json", json.dumps(discover, indent=2))
        z.writestr("routes.json", json.dumps(routes, indent=2))
        z.writestr("stacks.json", json.dumps(stacks, indent=2))
        z.writestr("net.json", json.dumps(net, indent=2))
        z.writestr("extract.json", json.dumps(extract_state, indent=2))
        # fresh inline spill
        spill = surface.full_meltdown_spill()
        spill_text = "\n".join(json.dumps(r, default=str) for r in spill)
        z.writestr("spill_full.jsonl", spill_text)
        z.writestr("dashboard.png", png)
        z.writestr("dashboard.html", dashboard.render_html(snap, ENDPOINT_URL))
        z.writestr("manifest.json", json.dumps(
            _build_manifest(snap, discover, {
                "snapshot": "/snapshot?deep=1", "discover": "/discover",
                "routes": "/routes", "stacks": "/stacks", "net": "/net",
                "spill_full_inline": "/spill/full?inline=1", "dashboard_png": "/dashboard.png",
                "harvest_zip": "/harvest", "harvest_json": "/harvest?format=json",
            }), indent=2))
    buf.seek(0)
    return web.Response(
        body=buf.getvalue(),
        headers={
            "Content-Type": "application/zip",
            "Content-Disposition": f'attachment; filename="mind_reader_harvest_{int(time.time())}.zip"',
        },
    )


_ROUTE_NAMES = set()


def build_app():
    global _APP
    app = web.Application()
    routes = [
        ("GET", "/", handle_dashboard_html),
        ("GET", "/dashboard", handle_dashboard_html),
        ("GET", "/dashboard.png", handle_dashboard_png),
        ("GET", "/snapshot", handle_snapshot),
        ("GET", "/health", handle_health),
        ("POST", "/spill/full", handle_spill_full),
        ("POST", "/spill/graphql", handle_spill_graphql),
        ("GET", "/spill/list", handle_spill_list),
        ("GET", "/spill/download", handle_spill_download),
        ("GET", "/routes", handle_routes),
        ("GET", "/discover", handle_discover),
        ("GET", "/extract", handle_extract),
        ("POST", "/extract/run", handle_extract_run),
        ("GET", "/harvest", handle_harvest),
        ("GET", "/refs", handle_refs),
        ("GET", "/stacks", handle_stacks),
        ("GET", "/net", handle_net),
        ("GET", "/stream", handle_stream),
    ]
    for method, path, handler in routes:
        if method == "GET":
            app.router.add_get(path, handler)
        else:
            app.router.add_post(path, handler)
        _ROUTE_NAMES.add(path)
    _APP = app
    return app


def run_server(host="0.0.0.0", port=8787):
    """Run the aiohttp app in the calling thread. Blocking.

    Does not install OS signal handlers (safe to call from a daemon thread
    for --attach mode, or the main thread for `serve`).
    """
    app = build_app()
    web.run_app(app, host=host, port=port, print=None, handle_signals=False)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8787)
    args = p.parse_args()
    print(f"[probe_server] v3 listening on {args.host}:{args.port}")
    run_server(args.host, args.port)
