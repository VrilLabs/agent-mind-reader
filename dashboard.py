"""
dashboard.py -- visual confirmation rendering for the mind-reader probe.

Renders the live memory snapshot to:
  - HTML (self-contained, screenshot-able by any browser tool)
  - PNG via matplotlib if available, else SVG (pure stdlib, no hard dep)

This makes "return a screenshot to confirm mind-reading success" universal:
the operator (or any screenshot tool) just hits /dashboard or /dashboard.png.
"""
import json

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _HAS_MPL = True
except Exception:
    _HAS_MPL = False


def render_html(snapshot: dict, endpoint_url: str = "") -> str:
    proc = snapshot.get("process", {})
    gc = snapshot.get("gc", {})
    gql = snapshot.get("graphql_composition", {})
    top = snapshot.get("top_types_by_bytes", [])[:10]
    mods = snapshot.get("top_modules_by_bytes", [])[:10]

    def rows(items, cols):
        cells = "".join(
            f"<td>{c}</td>" for c in cols
        )
        return "".join(
            f"<tr>{''.join(f'<td>{x[k]}</td>' for k in cols)}</tr>" for x in items
        )

    top_rows = "".join(
        f"<tr><td class='m'>{t['type']}</td><td>{t['count']:,}</td>"
        f"<td>{t['mb']:.4f}</td></tr>" for t in top
    )
    mod_rows = "".join(
        f"<tr><td class='m'>{m['module']}</td><td>{m['count']:,}</td>"
        f"<td>{m['mb']:.4f}</td></tr>" for m in mods
    )
    gql_pct = gql.get("graphql_fraction_of_shallow_bytes", 0) * 100
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Agent Mind Reader</title>
<style>
  body{{background:#0d1117;color:#c9d1d9;font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:24px}}
  h1{{color:#58a6ff;font-size:20px;margin:0 0 4px}}
  .url{{color:#7d8590;font-family:monospace;font-size:12px;margin-bottom:18px;word-break:break-all}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
  .panel{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}}
  .stat{{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #21262d}}
  .stat .k{{color:#7d8590;font-size:12px}} .stat .v{{color:#e6edf3;font-family:monospace;font-weight:600}}
  table{{width:100%;border-collapse:collapse;font-size:12px}}
  td,th{{text-align:left;padding:4px 6px;border-bottom:1px solid #21262d}}
  th{{color:#7d8590;font-weight:600;font-size:11px;text-transform:uppercase}}
  td.m{{font-family:monospace;color:#79c0ff}}
  .ok{{color:#3fb950;font-weight:600}}
  .bar{{height:10px;background:#21262d;border-radius:5px;overflow:hidden;margin-top:4px}}
  .bar>div{{height:100%;background:#f0883e;width:{gql_pct:.1f}%}}
  footer{{color:#484f58;font-size:11px;margin-top:18px;font-family:monospace}}
</style></head><body>
<h1>Agent Mind Reader — live memory surface</h1>
<div class="url">{endpoint_url} · pid {proc.get('pid','?')} · status <span class="ok">ALIVE</span></div>
<div class="grid">
  <div class="panel">
    <div class="stat"><span class="k">RSS (resident)</span><span class="v">{proc.get('rss_mb','?')} MB</span></div>
    <div class="stat"><span class="k">VMS (virtual)</span><span class="v">{proc.get('vms_mb','?')} MB</span></div>
    <div class="stat"><span class="k">Python heap (current)</span><span class="v">{proc.get('python_heap_current_mb','?')} MB</span></div>
    <div class="stat"><span class="k">Python heap (peak)</span><span class="v">{proc.get('python_heap_peak_mb','?')} MB</span></div>
    <div class="stat"><span class="k">GC-tracked objects</span><span class="v">{gc.get('total_tracked_objects','?'):,}</span></div>
    <div class="stat"><span class="k">Shallow heap</span><span class="v">{gc.get('total_shallow_mb','?')} MB</span></div>
    <div class="stat"><span class="k">System mem used</span><span class="v">{proc.get('system_percent_used','?')}%</span></div>
    <div class="stat"><span class="k">Scan time</span><span class="v">{snapshot.get('scan_duration_s','?')} s</span></div>
    <div style="margin-top:10px"><span class="k">GraphQL memory: {gql.get('graphql_shallow_mb',0)} MB ({gql_pct:.2f}%) · {gql.get('graphql_object_count',0)} objects</span>
      <div class="bar"><div></div></div></div>
  </div>
  <div class="panel">
    <table><tr><th>Type</th><th>Count</th><th>MB</th></tr>{top_rows}</table>
  </div>
</div>
<div class="panel" style="margin-top:20px">
  <table><tr><th>Module</th><th>Count</th><th>MB</th></tr>{mod_rows}</table>
</div>
<footer>probe_server /snapshot?deep=1 · every number is live, not estimated</footer>
</body></html>"""


def render_png(snapshot: dict, endpoint_url: str = "") -> tuple:
    """Return (content_type, bytes). PNG if matplotlib, else SVG."""
    if _HAS_MPL:
        return ("image/png", _png_bytes(snapshot, endpoint_url))
    return ("image/svg+xml", _svg_bytes(snapshot, endpoint_url).encode())


def _png_bytes(snapshot: dict, endpoint_url: str) -> bytes:
    import io
    proc = snapshot.get("process", {})
    gc = snapshot.get("gc", {})
    top = sorted(snapshot.get("top_types_by_bytes", [])[:10], key=lambda x: x["mb"])

    fig = plt.figure(figsize=(13, 7.6), facecolor="#0d1117")
    gs = fig.add_gridspec(1, 2, width_ratios=[1.05, 1.4], wspace=0.28)
    axL = fig.add_subplot(gs[0, 0]); axL.axis("off"); axL.set_facecolor("#0d1117")
    axL.set_xlim(0, 1); axL.set_ylim(0, 1)
    axL.text(0.0, 0.965, "AGENT MIND READER  ·  live memory surface",
             color="#58a6ff", fontsize=13, fontweight="bold", va="top")
    axL.text(0.0, 0.905, endpoint_url or "", color="#7d8590", fontsize=8.5, va="top", family="monospace")
    cards = [
        ("PROCESS PID", f"{proc.get('pid','?')}", "#7ee787"),
        ("RSS (resident)", f"{proc.get('rss_mb','?')} MB", "#7ee787"),
        ("Python heap (current)", f"{proc.get('python_heap_current_mb','?')} MB", "#79c0ff"),
        ("Python heap (peak)", f"{proc.get('python_heap_peak_mb','?')} MB", "#ffa657"),
        ("GC-tracked objects", f"{gc.get('total_tracked_objects','?'):,}", "#d2a8ff"),
        ("Shallow heap", f"{gc.get('total_shallow_mb','?')} MB", "#d2a8ff"),
        ("System mem used", f"{proc.get('system_percent_used','?')}%", "#7d8590"),
        ("Scan time", f"{snapshot.get('scan_duration_s','?')} s", "#7d8590"),
    ]
    y = 0.84
    for label, val, col in cards:
        axL.text(0.0, y, label, color="#7d8590", fontsize=8.5, va="top", fontweight="bold")
        axL.text(0.0, y - 0.034, val, color=col, fontsize=15, va="top", family="monospace", fontweight="bold")
        y -= 0.092
    axL.text(0.0, 0.0, "status: ALIVE", color="#3fb950", fontsize=8.5, va="bottom")

    axR = fig.add_subplot(gs[0, 1])
    labels = [t["type"] for t in top]
    vals = [t["mb"] for t in top]
    axR.barh(labels, vals, color="#1f6feb", edgecolor="#30363d", height=0.66)
    axR.set_facecolor("#0d1117")
    for s in axR.spines.values():
        s.set_color("#30363d")
    axR.tick_params(colors="#c9d1d9", labelsize=9.5)
    axR.set_title("Top object types by shallow bytes", color="#c9d1d9", fontsize=11, pad=10, loc="left")
    axR.set_xlabel("shallow bytes (MB)", color="#7d8590", fontsize=9.5)
    for b, v, t in zip(axR.patches, vals, top):
        axR.text(v + 0.03, b.get_y() + b.get_height() / 2,
                 f"{v:.3f} MB · {t['count']:,} obj", va="center", color="#8b949e", fontsize=8.5, family="monospace")
    axR.set_xlim(0, max(vals) * 1.32 if vals else 1)
    axR.grid(axis="x", color="#21262d", lw=0.8); axR.set_axisbelow(True)
    plt.subplots_adjust(left=0.04, right=0.985, top=0.95, bottom=0.05)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor="#0d1117")
    plt.close(fig)
    return buf.getvalue()


def _svg_bytes(snapshot: dict, endpoint_url: str) -> str:
    """Minimal pure-stdlib SVG fallback (no matplotlib)."""
    proc = snapshot.get("process", {})
    gc = snapshot.get("gc", {})
    top = sorted(snapshot.get("top_types_by_bytes", [])[:8], key=lambda x: x["mb"])
    maxv = max((t["mb"] for t in top), default=1) or 1
    y0 = 60
    bar_h = 26
    total_h = y0 + len(top) * (bar_h + 6) + 40
    rows = ""
    for i, t in enumerate(top):
        y = y0 + i * (bar_h + 6)
        w = max(1, t["mb"] / maxv * 360)
        rows += (f'<text x="10" y="{y-4}" fill="#c9d1d9" font-size="12" font-family="monospace">{t["type"]}</text>'
                 f'<rect x="10" y="{y}" width="{w:.0f}" height="{bar_h}" fill="#1f6feb"/>'
                 f'<text x="{10+w+8:.0f}" y="{y+18}" fill="#8b949e" font-size="11">{t["mb"]:.3f} MB · {t["count"]:,} obj</text>')
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="{total_h}" style="background:#0d1117">
<text x="10" y="24" fill="#58a6ff" font-size="16" font-weight="bold">Agent Mind Reader — live memory surface</text>
<text x="10" y="44" fill="#7d8590" font-size="11" font-family="monospace">{endpoint_url} · pid {proc.get('pid','?')} · RSS {proc.get('rss_mb','?')} MB · {gc.get('total_tracked_objects','?'):,} objects</text>
{rows}
</svg>"""
