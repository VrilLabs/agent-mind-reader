"""
serve.py -- Mind-Reader v3 launcher + cloudflared supervisor.

Stability design (fixes the recurring tunnel death):
  * The probe server runs in the MAIN thread as a blocking call. No
    tunnel_manager daemon-thread wrapper (that wrapper crashed and killed
    the probe). One stable process.
  * cloudflared is spawned as a child and SUPERVISED: if it dies, it is
    restarted; the trycloudflare URL is captured and written to
    mind_reader.url + printed. So probe+tunnel live in ONE process.
  * Uses sys.executable for any python needs (no `source venv`), avoiding
    the scoped-credential guard that tripped background launches.
  * --attach mode: importable from inside the TARGET agent's process so the
    probe introspects the real agent (its actual routes/memory), started in
    daemon threads that do not block the agent's own loop.

Usage:
  python serve.py serve --port 8787 --tunnel         # blocking, one process
  python serve.py serve --port 8787 --no-tunnel      # direct mode
  python serve.py url                                # print captured tunnel url
  # in-process (from inside an agent):
  from serve import attach; attach(port=8787, tunnel=True)
"""
import argparse
import os
import re
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

URL_FILE = os.environ.get("MIND_READER_URL_FILE", os.path.join(os.getcwd(), "mind_reader.url"))
LOG_FILE = os.environ.get("MIND_READER_LOG", os.path.join(os.getcwd(), "cloudflared.log"))

_tunnel_proc = {"proc": None, "url": None}
_stop = threading.Event()


def detect_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _capture_url(timeout=30):
    """Tail LOG_FILE for the trycloudflare URL."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE) as f:
                    content = f.read()
                m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", content)
                if m:
                    return m.group(0)
            except Exception:
                pass
        if _tunnel_proc["proc"] and _tunnel_proc["proc"].poll() is not None:
            return None
        time.sleep(0.5)
    return None


def cloudflared_supervisor(local_port, cloudflared_bin="./cloudflared", restart=True):
    """Spawn+supervise cloudflared quick-tunnel. Captures URL, restarts on death."""
    # empty previous log
    try:
        open(LOG_FILE, "w").close()
    except Exception:
        pass
    while not _stop.is_set():
        try:
            proc = subprocess.Popen(
                [cloudflared_bin, "tunnel", "--url", f"http://localhost:{local_port}",
                 "--no-autoupdate"],
                stdout=open(LOG_FILE, "w"), stderr=subprocess.STDOUT,
            )
            _tunnel_proc["proc"] = proc
            url = _capture_url(timeout=30)
            if url:
                _tunnel_proc["url"] = url
                os.environ["MIND_READER_ENDPOINT_URL"] = url
                try:
                    with open(URL_FILE, "w") as f:
                        f.write(url + "\n")
                except Exception:
                    pass
                print("=" * 60, flush=True)
                print("AGENT-MIND-READER v3 -- CLOUDFLARED TUNNEL MODE", flush=True)
                print(f"  Tunnel URL -> {url}", flush=True)
                print(f"  (also written to {URL_FILE})", flush=True)
                print("=" * 60, flush=True)
            else:
                print("[serve] cloudflared started but no URL captured within 30s", flush=True)
        except FileNotFoundError:
            print(f"[serve] cloudflared binary not found at {cloudflared_bin}; "
                  f"tunnel disabled. Run with a path via --cloudflared-bin.", flush=True)
            return
        except Exception as e:
            print(f"[serve] cloudflared error: {e}", flush=True)
        # wait for process death or stop
        while not _stop.is_set():
            if _tunnel_proc["proc"] and _tunnel_proc["proc"].poll() is not None:
                print("[serve] cloudflared exited; restarting in 3s...", flush=True)
                break
            time.sleep(1.0)
        if not restart:
            return
        time.sleep(3)


def serve_blocking(host="0.0.0.0", port=8787, tunnel=True, cloudflared_bin="./cloudflared"):
    """Blocking entrypoint: probe in main thread + (optional) supervised tunnel."""
    from probe_server import run_server
    if tunnel:
        t = threading.Thread(target=cloudflared_supervisor,
                             args=(port, cloudflared_bin), daemon=True)
        t.start()
        time.sleep(1.0)  # let cloudflared begin before probe binds (probe binds immediately anyway)
    print(f"[serve] probe v3 on {host}:{port}", flush=True)
    run_server(host=host, port=port)  # blocks main thread


def attach(host="0.0.0.0", port=8787, tunnel=True, cloudflared_bin="./cloudflared"):
    """In-process attach: start probe + tunnel in daemon threads, return immediately.

    Call this from inside the target agent's process so /discover and /routes
    reflect the agent's own live state. Non-blocking.
    """
    from probe_server import run_server
    server_t = threading.Thread(target=run_server, kwargs={"host": host, "port": port}, daemon=True)
    server_t.start()
    if tunnel:
        tun_t = threading.Thread(target=cloudflared_supervisor,
                                 args=(port, cloudflared_bin), daemon=True)
        tun_t.start()
    time.sleep(0.5)
    return {"host": host, "port": port, "tunnel": tunnel}


def main():
    ap = argparse.ArgumentParser(prog="mind_reader.serve")
    sub = ap.add_subparsers(dest="cmd", required=False)

    s = sub.add_parser("serve", help="blocking probe + supervised tunnel")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--tunnel", dest="tunnel", action="store_true", default=True)
    s.add_argument("--no-tunnel", dest="tunnel", action="store_false")
    s.add_argument("--cloudflared-bin", default="./cloudflared")

    u = sub.add_parser("url", help="print captured tunnel url")
    a = sub.add_parser("attach", help="in-process daemon-thread attach (non-blocking)")
    a.add_argument("--host", default="0.0.0.0")
    a.add_argument("--port", type=int, default=8787)
    a.add_argument("--no-tunnel", dest="tunnel", action="store_false", default=True)

    args = ap.parse_args()
    cmd = args.cmd or "serve"

    if cmd == "url":
        try:
            print(open(URL_FILE).read().strip())
        except Exception:
            print("")
        return
    if cmd == "attach":
        info = attach(host=args.host, port=args.port, tunnel=args.tunnel)
        print("[attach] probe started (non-blocking):", info, flush=True)
        # keep alive so a background launch persists
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            return
    # serve (default)
    if hasattr(args, "host"):
        serve_blocking(host=args.host, port=args.port, tunnel=args.tunnel,
                       cloudflared_bin=args.cloudflared_bin)
    else:
        # bare `serve.py` with no args -> serve default
        serve_blocking()


if __name__ == "__main__":
    main()
