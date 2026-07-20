"""
deep_extract.py — Minimal generic GraphQL deep-extraction harness.

One HTTP client. One introspection query. One digest-based diff mechanism.
Works against ANY spec-compliant GraphQL endpoint (public or authorized+token).

Usage:
    python deep_extract.py introspect <source_id>
    python deep_extract.py run <source_id> <operation_id> [--vars key=val ...]
"""

import sys
import os
import json
import hashlib
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).parent
CONNECTORS = yaml.safe_load((ROOT / "connectors.yaml").read_text())
INTROSPECTION_QUERY = (ROOT / "sources" / "introspection_query.graphql").read_text()


def sha256(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()


def get_source(source_id: str) -> dict:
    for s in CONNECTORS["sources"]:
        if s["id"] == source_id:
            return s
    raise ValueError(f"Unknown source: {source_id}")


def build_headers(source: dict) -> dict:
    headers = {"Content-Type": "application/json"}
    auth = source.get("auth", "none")
    if auth.startswith("bearer_token_env:"):
        env_var = auth.split(":", 1)[1]
        token = os.environ.get(env_var)
        if not token:
            raise RuntimeError(
                f"Missing token: set env var {env_var} to use source {source['id']}"
            )
        headers["Authorization"] = f"Bearer {token}"
    return headers


def post_graphql(endpoint: str, query: str, variables: dict, headers: dict) -> dict:
    resp = requests.post(
        endpoint,
        json={"query": query, "variables": variables},
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def introspect(source_id: str):
    source = get_source(source_id)
    headers = build_headers(source)
    result = post_graphql(source["endpoint"], INTROSPECTION_QUERY, {}, headers)

    if "errors" in result:
        print(f"Introspection failed (may be disabled): {result['errors']}")
        return

    schema = result["data"]["__schema"]
    digest = sha256(schema)

    out_dir = ROOT / "schemas"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{source_id.replace(':', '_')}_schema.json"
    out_path.write_text(json.dumps({
        "source_id": source_id,
        "endpoint": source["endpoint"],
        "schema_digest": digest,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "type_count": len(schema["types"]),
        "schema": schema,
    }, indent=2))

    print(f"Introspected {source_id}: {len(schema['types'])} types, digest={digest[:16]}")
    print(f"Written to {out_path}")


def find_operation(source: dict, operation_id: str) -> dict:
    for op in source.get("operations", []):
        if op["id"] == operation_id:
            return op
    raise ValueError(f"Unknown operation {operation_id} for source {source['id']}")


def resolve_endpoint(source: dict) -> str:
    """For mind_reader:self sources, honor MIND_READER_SELF_URL env so the
    engine can ingest the probe's own surfaces whether local or behind a tunnel."""
    if source.get("kind") in ("mind_reader", "json"):
        return os.environ.get("MIND_READER_SELF_URL", source.get("endpoint", "http://localhost:8787"))
    return source["endpoint"]


def capture(source_id: str, operation_id: str):
    """Deep-extract a mind-read result (JSON GET surface) with digest change detection.

    This is the 'token knockout' primitive applied to the probe's own outputs:
    GET a surface (/snapshot, /discover, /harvest, ...), hash it (sha256 over
    sorted JSON), and only persist a new raw capture if the digest changed since
    the last capture. So unchanged mind-read state is never re-processed.
    """
    source = get_source(source_id)
    operation = find_operation(source, operation_id)
    endpoint = resolve_endpoint(source)
    path = operation.get("path", "")
    url = endpoint.rstrip("/") + ("/" + path.lstrip("/") if path else "")

    headers = {"Accept": "application/json"}
    # self-ingestion: pass the probe's own token so gated surfaces (/harvest) work
    if source.get("kind") in ("mind_reader", "json"):
        tok = os.environ.get("MIND_READER_TOKEN")
        if tok:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}token={tok}"
    resp = requests.get(url, headers=headers, timeout=60)
    resp.raise_for_status()
    try:
        result = resp.json()
    except Exception:
        result = {"_raw": resp.text}

    response_digest = sha256(result)

    captures_dir = ROOT / "captures"
    captures_dir.mkdir(exist_ok=True)
    meta_path = captures_dir / f"{operation_id.replace('.', '_')}_meta.json"
    prior_digest = None
    if meta_path.exists():
        prior_meta = json.loads(meta_path.read_text())
        prior_digest = prior_meta.get("response_digest")

    changed = response_digest != prior_digest
    raw_path = captures_dir / f"{operation_id.replace('.', '_')}_response.json"
    if changed:
        raw_path.write_text(json.dumps(result, indent=2))

    meta = {
        "source_id": source_id,
        "operation_id": operation_id,
        "kind": source.get("kind"),
        "url": url,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "response_digest": response_digest,
        "changed_since_last_capture": changed,
        "raw_response_path": str(raw_path.relative_to(ROOT)) if changed else None,
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    print(f"Captured {operation_id} from {source_id}: changed={changed}, digest={response_digest[:16]}")


def run(source_id: str, operation_id: str, variables: dict):
    source = get_source(source_id)
    operation = find_operation(source, operation_id)
    headers = build_headers(source)

    query_path = ROOT / operation["file"]
    query = query_path.read_text()

    result = post_graphql(source["endpoint"], query, variables, headers)
    response_digest = sha256(result)

    captures_dir = ROOT / "captures"
    captures_dir.mkdir(exist_ok=True)

    # --- change detection: only write a new capture if content actually changed ---
    meta_path = captures_dir / f"{operation_id.replace('.', '_')}_meta.json"
    prior_digest = None
    if meta_path.exists():
        prior_meta = json.loads(meta_path.read_text())
        prior_digest = prior_meta.get("response_digest")

    changed = response_digest != prior_digest

    raw_path = captures_dir / f"{operation_id.replace('.', '_')}_response.json"
    if changed:
        raw_path.write_text(json.dumps(result, indent=2))

    meta = {
        "source_id": source_id,
        "operation_id": operation_id,
        "variables": variables,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "response_digest": response_digest,
        "changed_since_last_capture": changed,
        "raw_response_path": str(raw_path.relative_to(ROOT)) if changed else None,
    }
    meta_path.write_text(json.dumps(meta, indent=2))

    if "errors" in result:
        print(f"GraphQL errors: {result['errors']}")
    else:
        print(f"Ran {operation_id} on {source_id}: changed={changed}, digest={response_digest[:16]}")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    source_id = sys.argv[2]

    if cmd == "introspect":
        introspect(source_id)
    elif cmd == "capture":
        operation_id = sys.argv[3]
        capture(source_id, operation_id)
    elif cmd == "run":
        operation_id = sys.argv[3]
        variables = {}
        for arg in sys.argv[4:]:
            if arg.startswith("--vars"):
                continue
            if "=" in arg:
                k, v = arg.split("=", 1)
                variables[k] = v
        run(source_id, operation_id, variables)
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()