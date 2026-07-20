"""
mind_reader_client.py -- Agent-Mind-Reader Textual TUI Client

The operator enters an endpoint (direct IPv4/IPv6 address:port, or a
cloudflared https://*.trycloudflare.com URL) obtained from the probe's
tunnel_manager output, and this client connects to render a live,
animated view of the target agent process's real memory composition.

Graceful degradation:
  - Primary: WebSocket /stream for continuous live updates.
  - Fallback: if the WebSocket connection fails or drops, the client
    automatically falls back to polling GET /snapshot on an interval,
    and if that also fails, displays the last-known-good snapshot with
    a clearly marked "STALE / DISCONNECTED" banner rather than crashing.
"""
import asyncio
import json
import sys
import time

import aiohttp
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Header, Footer, Static, Log, ProgressBar
from textual.reactive import reactive


class MindReaderApp(App):
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #left { width: 60%; border: round green; }
    #right { width: 40%; border: round cyan; }
    #status { height: 3; border: round yellow; }
    .metric { padding: 0 1; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("f", "spill_full", "Catastrophic Meltdown Spill"),
        ("g", "spill_graphql", "GraphQL Spill"),
    ]

    connected = reactive(False)
    last_snapshot = reactive(dict)
    last_update_ts = reactive(0.0)

    def __init__(self, endpoint: str):
        super().__init__()
        self.endpoint = endpoint.rstrip("/")
        self.ws_url = self.endpoint.replace("https://", "wss://").replace("http://", "ws://") + "/stream"
        self._session = None
        self._poll_fallback_active = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="left"):
                yield Static("Live Memory Composition", classes="metric")
                yield Static(id="rss_display", classes="metric")
                yield ProgressBar(id="graphql_bar", total=100)
                yield Static(id="graphql_pct", classes="metric")
                yield Static(id="top_types", classes="metric")
            with Vertical(id="right"):
                yield Static("Event Log", classes="metric")
                yield Log(id="event_log")
        yield Static("Connecting...", id="status")
        yield Footer()

    async def on_mount(self):
        self.set_interval(1.0, self.render_snapshot)
        asyncio.create_task(self.connection_manager())

    async def on_unmount(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def connection_manager(self):
        self._session = aiohttp.ClientSession()
        while True:
            try:
                async with self._session.ws_connect(self.ws_url, timeout=8) as ws:
                    self.connected = True
                    self._poll_fallback_active = False
                    self.log_event(f"[OK] WebSocket connected: {self.ws_url}")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            self.last_snapshot = json.loads(msg.data)
                            self.last_update_ts = time.time()
            except Exception as e:
                self.connected = False
                if not self._poll_fallback_active:
                    self.log_event(f"[WARN] WebSocket unavailable ({e}); falling back to polling /snapshot")
                    self._poll_fallback_active = True
                await self.poll_fallback_once()
                await asyncio.sleep(2)

    async def poll_fallback_once(self):
        try:
            async with self._session.get(self.endpoint + "/snapshot", timeout=8) as resp:
                if resp.status == 200:
                    self.last_snapshot = await resp.json()
                    self.last_update_ts = time.time()
                    self.log_event("[OK] fallback snapshot fetched via /snapshot")
        except Exception as e:
            self.log_event(f"[FAIL] fallback poll also failed ({e}); showing last-known-good snapshot")

    def log_event(self, text: str):
        try:
            self.query_one("#event_log", Log).write_line(text)
        except Exception:
            pass

    def render_snapshot(self):
        snap = self.last_snapshot
        status = self.query_one("#status", Static)
        age = time.time() - self.last_update_ts if self.last_update_ts else None

        if not snap:
            status.update("[yellow]Waiting for first snapshot...[/yellow]")
            return

        stale = age is not None and age > 5
        mode = "LIVE (WebSocket)" if (self.connected and not stale) else "STALE / DISCONNECTED (last-known-good)"
        color = "green" if (self.connected and not stale) else "red"
        status.update(f"[{color}]{mode}[/{color}]  |  endpoint={self.endpoint}  |  age={round(age,1) if age else 0}s")

        proc = snap.get("process", {})
        gql = snap.get("graphql_composition", {})
        gc_info = snap.get("gc", {})

        self.query_one("#rss_display", Static).update(
            f"RSS: {proc.get('rss_mb','?')} MB   Heap: {proc.get('python_heap_current_mb','?')} MB\n"
            f"Tracked objects: {gc_info.get('total_tracked_objects','?')}   "
            f"System avail: {proc.get('system_available_mb','?')} MB"
        )

        pct = gql.get("graphql_fraction_of_shallow_bytes", 0) * 100
        self.query_one("#graphql_bar", ProgressBar).update(progress=pct)
        self.query_one("#graphql_pct", Static).update(
            f"GraphQL memory: {gql.get('graphql_shallow_mb','?')} MB "
            f"({pct:.2f}% of tracked shallow bytes) across {gql.get('graphql_object_count','?')} objects"
        )

        top = snap.get("top_types_by_bytes", [])[:8]
        lines = "\n".join(f"  {t['type']:<20} {t['mb']:>8.3f} MB  ({t['count']} objs)" for t in top)
        self.query_one("#top_types", Static).update("Top types by memory:\n" + lines)

    async def action_spill_full(self):
        self.log_event("[ACTION] Triggering CATASTROPHIC MELTDOWN SPILL (full memory)...")
        try:
            async with self._session.post(self.endpoint + "/spill/full", timeout=30) as resp:
                data = await resp.json()
                self.log_event(f"[OK] meltdown spill written: {data['file']} ({data['objects_dumped']} objects, {data['file_size_bytes']} bytes)")
        except Exception as e:
            self.log_event(f"[FAIL] meltdown spill request failed: {e}")

    async def action_spill_graphql(self):
        self.log_event("[ACTION] Triggering GraphQL-only memory spill...")
        try:
            async with self._session.post(self.endpoint + "/spill/graphql", timeout=30) as resp:
                data = await resp.json()
                self.log_event(f"[OK] graphql spill written: {data['file']} ({data['objects_dumped']} objects, {data['file_size_bytes']} bytes)")
        except Exception as e:
            self.log_event(f"[FAIL] graphql spill request failed: {e}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python mind_reader_client.py <endpoint>")
        print("  endpoint examples:")
        print("    http://192.168.1.50:8787")
        print("    https://random-words.trycloudflare.com")
        sys.exit(1)
    endpoint = sys.argv[1]
    app = MindReaderApp(endpoint)
    app.run()


if __name__ == "__main__":
    main()
