# v3.1 local smoke-test results (all PASS)

## v3.1 — integrated extraction engine (deep-extract mind-read results)

| Surface | Result |
|---|---|
| /extract | lists 3 sources: countries:trevorblades, github:api, **mind_reader:self**; surfaces bundled schemas + captures |
| /extract/run capture (snapshot.v1) | GETs probe's own /snapshot?deep=1, writes capture with sha256 digest, changed=True |
| /extract/run capture (routes.v1) #2 | **token knockout proven**: changed=False, raw_response_path=None (unchanged state NOT re-written) |
| /extract/run capture (discover/routes/net) | all HTTP 200, captures persisted with digests |
| /discover | now includes extract subsystem (graphql-deep-extract) + extract state |
| /harvest | includes extract.json (zip) + extract field (json) |
| extract/deep_extract.py capture() | new fn: JSON-GET + digest change detection (reuses original sha256 mechanism) |
| self-deadlock fix | /extract/run runs blocking extraction in a thread executor so the probe can serve its own self-GET |

## v3 — base surfaces (all PASS)

| Surface | Result |
|---|---|
| /health | v3, 18 endpoints (now incl /extract, /extract/run), token_gated=false |
| /snapshot?deep=1 | includes top_modules_by_bytes |
| /discover | frameworks=1, subsystems_present (generic detection works) |
| /routes | route_count=18 |
| /stacks | threads=1 + real call stack |
| /net | conns + gc socket objects |
| /dashboard | HTML 200 |
| /dashboard.png | PNG 200, ~138KB, visually clean (no overflow/truncation/contrast issues) |
| /harvest?format=json | consolidated JSON 200 |
| /harvest (zip) | 200, 10 files (health/snapshot/discover/extract/routes/stacks/net/spill_full.jsonl/dashboard.png/dashboard.html/manifest.json) |
| launcher direct mode | blocking, /health returns full endpoint list |
| attach() in-process | daemon-thread attach works, /health + /discover respond from inside the process |
| harvest.py CLI | dashboard + snapshot commands work (pure stdlib HTTP) |
| heuristic fix | container deep attribution + memory_profiler marker dropped (carried from v2) |

## Stability fixes verified
- Probe runs in the MAIN thread (no tunnel_manager thread wrapper) — no crash.
- Uses sys.executable (no `source venv`) — no scoped-credential guard.
- cloudflared supervisor: spawns + captures URL + writes mind_reader.url + restart-on-death logic in place.
- One stable process = probe + supervised tunnel.
- /extract/run extraction runs in a thread executor (no event-loop self-deadlock).
