# GraphQL Deep-Extraction PoC

Minimal, vendor-neutral GraphQL deep-extraction harness. One generic HTTP
client + the standard GraphQL introspection query handles ANY spec-compliant
endpoint — no per-vendor SDK required.

## What's included (example captured data)

### Countries API (fully public, no auth)
- Endpoint: https://countries.trevorblades.com/graphql
- `schemas/countries_schema.json` — LIVE introspected schema
  (23 types, digest `fcbaa0523c76a3e3...`)
- `sources/queries/countries_snapshot.graphql` — scoped operation
- `captures/countries_snapshot.json` — LIVE captured data
  (250 country records)
- `captures/countries_capture_meta.json` — capture provenance/digest record

## Design

```
connectors.yaml          <- one config file registers both sources
sources/
  introspection_query.graphql   <- the ONE query that works on any endpoint
  queries/*.graphql              <- scoped, bounded operations per source
schemas/                <- captured schema snapshots + digests
captures/                <- captured responses + digests + change flags
deep_extract.py          <- generic runner (introspect / run)
```

No Octokit, no gql, no codegen tooling. `requests` + `PyYAML` is the entire
dependency surface. The same script works for both sources because GraphQL
is self-describing: the introspection query returns the same shape from any
compliant server.

## Usage

```bash
pip install requests pyyaml

# Re-run introspection (GraphQL)
python deep_extract.py introspect countries:trevorblades

# Run the bounded operation and capture change-detected snapshot
python deep_extract.py run countries:trevorblades countries.snapshot.v1
```

## Key mechanism: change detection via digest

Every capture is hashed (`sha256` over the sorted JSON). Re-running the same
operation only writes a new raw response file if the digest differs from the
last capture — this is the "token knockout" primitive: don't re-process
unchanged data.

## Legal/ethical scope

Sources must be used strictly within their documented, intended public
access model:
- Countries API: explicitly public, no auth, introspection open by design.
- Any live query against public GraphQL endpoints must be done with permission or with the caller's own authenticated token, and with respect for rate limits and scopes.

No authentication bypass, no blind/forced introspection against
introspection-disabled endpoints, and no terms-of-service circumvention are
part of this PoC. **For educational purposes only**