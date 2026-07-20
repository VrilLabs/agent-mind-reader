"""
extract/ -- bundled GraphQL deep-extraction engine.

Vendor-neutral harness: one generic HTTP client + the standard GraphQL
introspection query handles ANY spec-compliant endpoint, with sha256-digest
change detection (the "token knockout" primitive: don't re-process unchanged
data). This is the extraction core the mind-reader was designed to observe.

Importable as a subpackage:
    from extract import deep_extract
    deep_extract.introspect("countries:trevorblades")
    deep_extract.run("countries:trevorblades", "countries.snapshot.v1", {})

The mind-reader surfaces its live state via GET /extract and can drive it
remotely via POST /extract/run (token-gated).
"""
