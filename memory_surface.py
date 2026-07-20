"""
memory_surface.py -- Real Live Memory Introspection Core (v3)

v3 additions over v2:
  - discover_frameworks(): generic web-framework route discovery.
    Detects live aiohttp Application / Flask app / Starlette|FastAPI /
    Django url resolver instances in the heap and walks whichever router
    is present -> a unified endpoint graph. No more aiohttp-only hardcoding.
  - discover_subsystems(): scan sys.modules for GraphQL / DB / HTTP-client /
    cache / task-queue stacks so the operator sees what the agent actually runs.
  - snapshot() still emits top_types_by_bytes + top_modules_by_bytes.
  - _is_graphql_object(deep_scan) container attribution + dropped
    "memory_profiler" marker (from v2).
"""
import gc
import sys
import time
import tracemalloc
import psutil
import os as _os

try:
    from pympler import asizeof
    _HAS_PYMPLER = True
except Exception:
    _HAS_PYMPLER = False

GRAPHQL_MODULE_MARKERS = ("introspector", "async_extractor", "extractor", "fuse_entities", "deep_extract", "graphql_deep_extract")
GRAPHQL_STRING_MARKERS = ("__schema", "query {", "IntrospectionQuery", "graphql")
_CONTAINER_CAP = 64

SUBSYSTEM_PROBES = {
    "graphql-core": "graphql",
    "strawberry-graphql": "strawberry",
    "ariadne": "ariadne",
    "graphene": "graphene",
    "sqlalchemy": "sqlalchemy",
    "tortoise-orm": "tortoise",
    "httpx": "httpx",
    "aiohttp": "aiohttp",
    "requests": "requests",
    "redis": "redis",
    "celery": "celery",
    "pympler": "pympler",
    "textual": "textual",
    "fastapi": "fastapi",
    "flask": "flask",
    "django": "django",
    "starlette": "starlette",
    "graphql-deep-extract": "extract.deep_extract",
    "deep_extract": "deep_extract",
}


def _is_graphql_object(obj, deep_scan: bool = False) -> bool:
    try:
        mod = type(obj).__module__ or ""
        if any(marker in mod for marker in GRAPHQL_MODULE_MARKERS):
            return True
        cls_name = type(obj).__name__
        if cls_name in ("SchemaIndex", "GraphNode", "GraphEdge", "LRUNodeCache", "AsyncDeepExtractor"):
            return True
        if isinstance(obj, str) and len(obj) < 20000:
            if any(marker in obj for marker in GRAPHQL_STRING_MARKERS):
                return True
        if deep_scan and isinstance(obj, (dict, list, tuple, set, frozenset)):
            try:
                vals = obj.values() if isinstance(obj, dict) else obj
                n = 0
                for v in vals:
                    if n >= _CONTAINER_CAP:
                        break
                    n += 1
                    if isinstance(v, str) and len(v) < 20000 and any(m in v for m in GRAPHQL_STRING_MARKERS):
                        return True
            except Exception:
                pass
    except Exception:
        pass
    return False


class MemorySurface:
    def __init__(self):
        self._proc = psutil.Process(_os.getpid())
        if not tracemalloc.is_tracing():
            tracemalloc.start()

    # ---------- core snapshot ----------
    def snapshot(self, deep: bool = False) -> dict:
        t0 = time.time()
        objects = gc.get_objects()
        type_counts = {}
        type_bytes = {}
        module_counts = {}
        module_bytes = {}
        graphql_bytes = 0
        graphql_count = 0
        total_bytes_shallow = 0

        for obj in objects:
            try:
                size = sys.getsizeof(obj)
            except Exception:
                size = 0
            total_bytes_shallow += size
            tname = type(obj).__name__
            mname = type(obj).__module__ or "builtins"
            type_counts[tname] = type_counts.get(tname, 0) + 1
            type_bytes[tname] = type_bytes.get(tname, 0) + size
            module_counts[mname] = module_counts.get(mname, 0) + 1
            module_bytes[mname] = module_bytes.get(mname, 0) + size
            if _is_graphql_object(obj, deep_scan=deep):
                graphql_bytes += size
                graphql_count += 1

        mem = self._proc.memory_info()
        heap_current, heap_peak = tracemalloc.get_traced_memory()
        vm = psutil.virtual_memory()

        top_types = sorted(type_bytes.items(), key=lambda kv: -kv[1])[:15]
        top_modules = sorted(module_bytes.items(), key=lambda kv: -kv[1])[:15]

        result = {
            "timestamp": time.time(),
            "scan_duration_s": round(time.time() - t0, 4),
            "process": {
                "pid": _os.getpid(),
                "rss_mb": round(mem.rss / (1024 ** 2), 3),
                "vms_mb": round(mem.vms / (1024 ** 2), 3),
                "python_heap_current_mb": round(heap_current / (1024 ** 2), 3),
                "python_heap_peak_mb": round(heap_peak / (1024 ** 2), 3),
                "system_available_mb": round(vm.available / (1024 ** 2), 3),
                "system_percent_used": vm.percent,
            },
            "gc": {
                "total_tracked_objects": len(objects),
                "total_shallow_bytes": total_bytes_shallow,
                "total_shallow_mb": round(total_bytes_shallow / (1024 ** 2), 3),
            },
            "graphql_composition": {
                "graphql_object_count": graphql_count,
                "graphql_shallow_bytes": graphql_bytes,
                "graphql_shallow_mb": round(graphql_bytes / (1024 ** 2), 3),
                "graphql_fraction_of_tracked_objects": round(graphql_count / max(1, len(objects)), 6),
                "graphql_fraction_of_shallow_bytes": round(graphql_bytes / max(1, total_bytes_shallow), 6),
            },
            "top_types_by_bytes": [
                {"type": t, "count": type_counts[t], "bytes": b, "mb": round(b / (1024**2), 4)}
                for t, b in top_types
            ],
            "top_modules_by_bytes": [
                {"module": m, "count": module_counts[m], "bytes": b, "mb": round(b / (1024**2), 4)}
                for m, b in top_modules
            ],
        }

        if deep and _HAS_PYMPLER:
            graphql_objs = [o for o in objects if _is_graphql_object(o, deep_scan=True)][:500]
            deep_total = 0
            for o in graphql_objs:
                try:
                    deep_total += asizeof.asizeof(o)
                except Exception:
                    pass
            result["graphql_composition"]["graphql_deep_bytes_sampled_500"] = deep_total
            result["graphql_composition"]["graphql_deep_mb_sampled_500"] = round(deep_total / (1024**2), 3)

        del objects
        return result

    # ---------- spills ----------
    def full_meltdown_spill(self, cap: int = None) -> list:
        dump = []
        for obj in gc.get_objects():
            try:
                size = sys.getsizeof(obj)
                r = repr(obj)
                if len(r) > 300:
                    r = r[:300] + "...<truncated>"
            except Exception:
                size, r = 0, "<unreprable>"
            dump.append({
                "type": type(obj).__name__,
                "module": type(obj).__module__,
                "id": id(obj),
                "shallow_bytes": size,
                "repr": r,
                "is_graphql": _is_graphql_object(obj, deep_scan=True),
            })
            if cap is not None and len(dump) >= cap:
                break
        return dump

    def graphql_memory_spill(self) -> list:
        dump = []
        for obj in gc.get_objects():
            if not _is_graphql_object(obj, deep_scan=True):
                continue
            try:
                shallow = sys.getsizeof(obj)
                deep = asizeof.asizeof(obj) if _HAS_PYMPLER else None
                r = repr(obj)
                if len(r) > 500:
                    r = r[:500] + "...<truncated>"
            except Exception:
                shallow, deep, r = 0, None, "<unreprable>"
            dump.append({
                "type": type(obj).__name__,
                "module": type(obj).__module__,
                "id": id(obj),
                "shallow_bytes": shallow,
                "deep_bytes": deep,
                "repr": r,
            })
        return dump

    # ---------- introspection surfaces ----------
    def routes_surface(self, app) -> dict:
        """Walk a live aiohttp Application's router -> endpoint graph."""
        routes = []
        try:
            for res in app.router.resources():
                canonical = getattr(res, "canonical", None)
                rts = []
                iterable = res
                try:
                    iter(iterable)
                except Exception:
                    iterable = getattr(res, "routes", []) or []
                for rt in iterable:
                    handler = getattr(rt, "handler", None)
                    rts.append({
                        "method": getattr(rt, "method", None),
                        "handler": getattr(handler, "__qualname__", None) or str(handler),
                        "module": getattr(handler, "__module__", None),
                        "name": getattr(rt, "name", None),
                    })
                routes.append({"canonical": canonical, "routes": rts})
        except Exception as e:
            return {"error": str(e), "routes": routes}
        return {"framework": "aiohttp", "route_count": len(routes), "routes": routes}

    def refs_surface(self, target_id: int) -> dict:
        target = None
        for obj in gc.get_objects():
            if id(obj) == target_id:
                target = obj
                break
        if target is None:
            return {"error": "no live object with that id"}

        def describe(o):
            try:
                r = repr(o)
                if len(r) > 300:
                    r = r[:300] + "...<truncated>"
            except Exception:
                r = "<unreprable>"
            try:
                sb = sys.getsizeof(o)
            except Exception:
                sb = 0
            return {
                "type": type(o).__name__, "module": type(o).__module__,
                "id": id(o), "shallow_bytes": sb, "repr": r,
                "is_graphql": _is_graphql_object(o, deep_scan=True),
            }

        referrers = gc.get_referrers(target)
        referents = gc.get_referents(target)
        deep = None
        if _HAS_PYMPLER:
            try:
                deep = asizeof.asizeof(target)
            except Exception:
                deep = None
        return {
            "target": describe(target),
            "deep_bytes": deep,
            "referrer_count": len(referrers),
            "referent_count": len(referents),
            "referrers_sample_200": [describe(o) for o in referrers if o is not target][:200],
            "referents_sample_200": [describe(o) for o in referents if o is not target][:200],
        }

    def stacks_surface(self) -> dict:
        import sys as _sys, threading
        frames = _sys._current_frames()
        threads = []
        for t in threading.enumerate():
            threads.append({
                "name": t.name, "ident": t.ident, "daemon": t.daemon,
                "alive": t.is_alive(),
            })
        name_by_ident = {t.ident: t.name for t in threading.enumerate()}
        stacks = []
        for tid, frame in frames.items():
            tb = []
            f = frame
            depth = 0
            while f is not None and depth < 50:
                tb.append({
                    "file": f.f_code.co_filename,
                    "lineno": f.f_lineno,
                    "func": f.f_code.co_name,
                })
                f = f.f_back
                depth += 1
            stacks.append({
                "thread_id": tid,
                "thread_name": name_by_ident.get(tid),
                "stack_depth": len(tb),
                "stack": tb,
            })
        return {"thread_count": len(threads), "threads": threads, "stacks": stacks}

    def net_surface(self) -> dict:
        import socket as _socket
        conns = []
        try:
            for c in self._proc.net_connections(kind="inet"):
                conns.append({
                    "fd": c.fd, "family": str(c.family), "type": str(c.type),
                    "laddr": str(c.laddr) if c.laddr else None,
                    "raddr": str(c.raddr) if c.raddr else None,
                    "status": c.status,
                })
        except Exception:
            pass
        sock_count = 0
        sock_samples = []
        for o in gc.get_objects():
            if isinstance(o, _socket.socket):
                sock_count += 1
                if len(sock_samples) < 50:
                    try:
                        closed = getattr(o, "closed", False)
                        sock_samples.append({
                            "fd": -1 if closed else o.fileno(),
                            "family": str(o.family), "type": str(o.type),
                            "closed": bool(closed),
                            "laddr": str(o.getsockname()) if not closed else None,
                            "raddr": (str(o.getpeername()) if not closed else None),
                        })
                    except Exception:
                        pass
        return {
            "process_net_connection_count": len(conns),
            "process_net_connections": conns,
            "gc_socket_object_count": sock_count,
            "socket_samples": sock_samples,
        }

    # ---------- v3 discovery ----------
    def discover_subsystems(self) -> dict:
        """Scan loaded modules for known agent subsystems."""
        found = {}
        for label, modname in SUBSYSTEM_PROBES.items():
            present = modname in sys.modules
            version = None
            if present:
                m = sys.modules.get(modname)
                version = getattr(m, "__version__", None) or getattr(m, "VERSION", None)
            found[label] = {"present": present, "version": version}
        present_count = sum(1 for v in found.values() if v["present"])
        return {"present_count": present_count, "subsystems": found}

    def discover_frameworks(self) -> dict:
        """Generic web-framework + router discovery across the live heap.

        Returns every detected framework app with its route table, so any
        agent (aiohttp/Flask/FastAPI/Django) is surfaced without hardcoding.
        """
        frameworks = []

        # aiohttp
        try:
            import aiohttp.web as web
            apps = [o for o in gc.get_objects() if isinstance(o, web.Application)]
            for a in apps:
                frameworks.append(self.routes_surface(a))
        except Exception:
            pass

        # Flask
        try:
            import flask
            apps = [o for o in gc.get_objects() if isinstance(o, flask.Flask)]
            for a in apps:
                rules = []
                for r in a.url_map.iter_rules():
                    methods = sorted((r.methods or set()) - {"HEAD", "OPTIONS"})
                    rules.append({
                        "canonical": str(r),
                        "methods": methods,
                        "endpoint": r.endpoint,
                    })
                frameworks.append({
                    "framework": "flask", "app_name": a.name,
                    "route_count": len(rules), "routes": rules,
                })
        except Exception:
            pass

        # Starlette / FastAPI
        try:
            import starlette.applications as sa
            apps = [o for o in gc.get_objects() if isinstance(o, sa.Starlette)]
            for a in apps:
                rules = []
                for r in getattr(a, "routes", []):
                    path = getattr(r, "path", None) or str(r)
                    methods = list(getattr(r, "methods", []) or [])
                    rules.append({"canonical": path, "methods": methods,
                                  "name": getattr(r, "name", None)})
                fw = "fastapi" if "fastapi" in sys.modules else "starlette"
                frameworks.append({
                    "framework": fw, "route_count": len(rules), "routes": rules,
                })
        except Exception:
            pass

        # Django
        try:
            from django.urls import get_resolver
            res = get_resolver()
            rules = []
            try:
                for p in res.url_patterns:
                    rules.append({
                        "canonical": getattr(p, "pattern", None) and str(p.pattern),
                        "name": getattr(p, "name", None),
                    })
            except Exception:
                pass
            frameworks.append({
                "framework": "django", "route_count": len(rules), "routes": rules,
            })
        except Exception:
            pass

        return {"framework_count": len(frameworks), "frameworks": frameworks}

    # ---------- v3.1 extraction-engine surface ----------
    def extract_state_surface(self) -> dict:
        """Live state of the bundled graphql_deep_extract engine.

        Reads the extract/ subpackage's connectors.yaml + schemas/ + captures/
        from disk, and the in-memory CONNECTORS registry if the module is
        imported in-process. Returns the extraction "map": configured sources,
        captured schemas with digests, and capture change-detection state.
        """
        import json as _json
        extract_dir = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "extract")
        state = {
            "extract_dir": extract_dir,
            "loaded_in_process": False,
            "sources": [],
            "schemas": [],
            "captures": [],
            "introspection_query_present": False,
        }

        # in-process live registry (optional)
        try:
            import extract.deep_extract as de  # noqa
            state["loaded_in_process"] = True
            state["sources"] = [
                {"id": s.get("id"), "endpoint": s.get("endpoint"),
                 "auth": s.get("auth"), "introspection": s.get("introspection"),
                 "operations": [op.get("id") for op in s.get("operations", [])]}
                for s in de.CONNECTORS.get("sources", [])
            ]
            state["introspection_query_present"] = bool(getattr(de, "INTROSPECTION_QUERY", None))
        except Exception:
            pass

        # filesystem (durable truth) -- connectors.yaml
        connectors_path = _os.path.join(extract_dir, "connectors.yaml")
        try:
            import yaml as _yaml
            cfg = _yaml.safe_load(open(connectors_path).read()) or {}
            if not state["sources"]:
                state["sources"] = [
                    {"id": s.get("id"), "endpoint": s.get("endpoint"),
                     "auth": s.get("auth"), "introspection": s.get("introspection"),
                     "operations": [op.get("id") for op in s.get("operations", [])]}
                    for s in cfg.get("sources", [])
                ]
        except Exception:
            pass

        # schemas/ -- captured schema snapshots + digests
        schemas_dir = _os.path.join(extract_dir, "schemas")
        try:
            for name in sorted(_os.listdir(schemas_dir)):
                p = _os.path.join(schemas_dir, name)
                if not _os.path.isfile(p):
                    continue
                rec = {"file": name, "size_bytes": _os.getsize(p)}
                try:
                    raw = open(p).read()
                    obj = _json.loads(raw)
                    if isinstance(obj, dict):
                        rec.update({
                            "schema_digest": obj.get("schema_digest"),
                            "type_count": obj.get("type_count"),
                            "endpoint": obj.get("endpoint"),
                            "captured_at": obj.get("captured_at"),
                            "schema_source": obj.get("schema_source"),
                        })
                except Exception:
                    rec["format"] = "sdl_or_other"
                state["schemas"].append(rec)
        except Exception:
            pass

        # captures/ -- change-detection state (token knockout)
        captures_dir = _os.path.join(extract_dir, "captures")
        try:
            for name in sorted(_os.listdir(captures_dir)):
                if not name.endswith("_meta.json"):
                    continue
                try:
                    meta = _json.loads(open(_os.path.join(captures_dir, name)).read())
                    state["captures"].append({
                        "file": name,
                        "source_id": meta.get("source_id"),
                        "operation_id": meta.get("operation_id"),
                        "response_digest": meta.get("response_digest"),
                        "schema_digest": meta.get("schema_digest"),
                        "changed_since_last_capture": meta.get("changed_since_last_capture"),
                        "fetched_at": meta.get("fetched_at"),
                        "record_count": meta.get("record_count"),
                        "raw_response_path": meta.get("raw_response_path"),
                    })
                except Exception:
                    pass
        except Exception:
            pass

        return state
