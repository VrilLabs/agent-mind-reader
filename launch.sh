#!/usr/bin/env bash
# launch.sh -- thin wrapper around the v3 supervised entrypoint.
#
# Usage:
#   ./launch.sh serve [port] [--no-tunnel]     # one stable process + tunnel (default)
#   ./launch.sh probe direct [port]            # legacy alias: direct mode
#   ./launch.sh probe tunnel [port]            # legacy alias: tunnel mode
#   ./launch.sh client <url>                   # Textual TUI client
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

cmd="${1:-serve}"
shift || true

case "$cmd" in
  serve)
    exec python3 serve.py serve "$@"
    ;;
  probe)
    sub="${1:-tunnel}"; shift || true
    port="${1:-8787}"
    if [ "$sub" = "direct" ]; then
      exec python3 serve.py serve --port "$port" --no-tunnel
    else
      exec python3 serve.py serve --port "$port" --tunnel
    fi
    ;;
  client)
    exec python3 client/mind_reader_client.py "$@"
    ;;
  harvest)
    exec python3 harvest.py harvest "$@"
    ;;
  *)
    echo "usage: ./launch.sh {serve|probe direct|probe tunnel|client|harvest} ..." >&2
    exit 2
    ;;
esac
