"""Deterministic mock of the Rendobar API, driven by the published OpenAPI document.

Part of the SDK conformance suite (SCENARIOS.md). Every Rendobar SDK runs its
scenarios against this server, so all languages are held to one behaviour.

Responses are synthesized from the spec's own response schemas, so any generator's
deserializer sees data the spec says is valid. Scenario ids exercise behaviour:

  GET /jobs/job_404      -> 404 error envelope
  GET /jobs/job_flaky    -> 429 (Retry-After: 1) on the first hit, 200 after
  GET /jobs/job_503      -> 503 every time (retry exhaustion)
  GET /jobs/job_409      -> 409 every time (must not be retried)
  GET /jobs/job_slow     -> 200 after 6 s (client timeout test)
  GET /jobs/job_future   -> 200 with status "paused" (new enum value) + unknown field
  GET /jobs/job_done     -> 200, a complete job with an output file
  GET /jobs/job_failed   -> 200, a failed job with an error code and message
  GET /jobs/<created id> -> a job POST /jobs created: running on the first poll, complete after
  GET /jobs?limit&offset -> 120 jobs total, paginated
  GET /jobs/{id}/download -> 1 KiB of bytes, video/mp4
  POST /jobs              -> 400 without a string `type` or with non-object `params`;
                             dedupes on idempotencyKey like the real API; the
                             first submit after /__reset?lose_submit=1 is
                             created and then answered with a 502 (lost response)
  deprecated operations    -> Deprecation: @<x-deprecated-since> (RFC 9745)
  every response           -> X-Request-Id: req_conformance_<n>
  POST /__reset           -> clears hit counters and created jobs
Every request is appended to the log file (method, path, query, selected headers).

Run: python3 -I mock_server.py <spec> <port> <logfile>
"""
import json, sys, time, re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

SPEC = json.load(open(sys.argv[1]))
PORT = int(sys.argv[2])
LOG = sys.argv[3]
HITS = {}
CREATED = {}
STATE = {"lose_submit": False, "seq": 0}


def resolve(s):
    if "$ref" in s:
        name = s["$ref"].split("/")[-1]
        return SPEC["components"]["schemas"][name]
    return s


def job_of(status):
    """A synthesized job in one status, from that status's own variant schema."""
    name = {"waiting": "JobWaiting", "running": "JobRunning", "complete": "JobComplete", "failed": "JobFailed"}[status]
    return synth({"$ref": f"#/components/schemas/{name}"})


def synth(s, key="", depth=0):
    if depth > 12:  # a recursive schema must not hang the mock
        return {}
    s = resolve(s)
    if "example" in s:
        return s["example"]
    if "default" in s and not isinstance(s.get("default"), dict):
        return s["default"]
    for k in ("oneOf", "anyOf"):
        if k in s:
            return synth(s[k][0], key, depth + 1)
    if "allOf" in s:
        out = {}
        for part in s["allOf"]:
            v = synth(part, key, depth + 1)
            if isinstance(v, dict):
                out.update(v)
        return out
    if "enum" in s:
        return s["enum"][0]
    if "const" in s:
        return s["const"]
    t = s.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "null")
    if t == "object" or "properties" in s:
        return {k: synth(v, k, depth + 1) for k, v in s.get("properties", {}).items()}
    if t == "array":
        return [synth(s.get("items", {}), key, depth + 1)]
    timestamp = key.endswith("At") or key.endswith("_at")
    if t == "integer":
        return 1731000000000 if timestamp else 1
    if t == "number":
        return 1731000000000 if timestamp else 1.5
    if t == "boolean":
        return True
    if t == "null":
        return None
    if t == "string":
        if s.get("format") == "uri" or key.lower().endswith("url"):
            return "https://cdn.rendobar.com/example.mp4"
        if key == "id":
            return "job_abc123"
        return f"{key or 'value'}"
    return {}


ROUTES = []
for path, item in SPEC["paths"].items():
    rx = "^" + re.sub(r"\{[^}]+\}", r"([^/]+)", path) + "$"
    for method, op in item.items():
        if isinstance(op, dict) and "responses" in op:
            ROUTES.append((method.upper(), re.compile(rx), path, op))
# Longer literal paths first so /jobs/types wins over /jobs/{id}.
ROUTES.sort(key=lambda r: (r[2].count("{"), -len(r[2])))


def success(op):
    for code in ("200", "201", "202", "204"):
        if code in op["responses"]:
            r = op["responses"][code]
            sch = r.get("content", {}).get("application/json", {}).get("schema")
            return int(code), (synth(sch) if sch else None)
    return 200, None


def err(code, msg):
    return {"error": {"code": code, "message": msg}}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def send(self, status, body=None, headers=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else (b"" if body is None else json.dumps(body).encode())
        self.send_response(status)
        if data:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        STATE["seq"] += 1
        self.send_header("X-Request-Id", f"req_conformance_{STATE['seq']}")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def handle_any(self, method):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        HITS[u.path] = HITS.get(u.path, 0) + 1
        with open(LOG, "a") as f:
            f.write(json.dumps({
                "t": time.time(), "m": method, "path": u.path, "q": u.query,
                "hit": HITS[u.path],
                "ua": self.headers.get("User-Agent"),
                "auth": (self.headers.get("Authorization") or "")[:12],
                "client": self.headers.get("X-Rendobar-Client"),
                "idem": self.headers.get("Idempotency-Key"),
                "ctype": self.headers.get("Content-Type"),
                "extra": {k: v for k, v in self.headers.items() if k.lower().startswith(("x-", "idempotency"))},
                "body": body.decode(errors="replace")[:400],
            }) + "\n")

        if u.path == "/__reset":
            HITS.clear(); CREATED.clear()
            STATE["lose_submit"] = "lose_submit=1" in u.query
            return self.send(204)
        if u.path == "/jobs" and method == "POST":
            if not (self.headers.get("Authorization") or "").startswith("Bearer "):
                return self.send(401, err("UNAUTHORIZED", "Missing or invalid API key"))
            try:
                req = json.loads(body or b"{}")
            except ValueError:
                return self.send(400, err("VALIDATION_ERROR", "Body is not JSON"))
            if not isinstance(req, dict) or not isinstance(req.get("type"), str) or not isinstance(req.get("params", {}), dict):
                return self.send(400, err("VALIDATION_ERROR", "A submit needs a string type and an object params"))
            key = req.get("idempotencyKey")
            if key is not None and key in CREATED:
                return self.send(200, {"data": {"id": CREATED[key], "status": "waiting", "idempotent": True}})
            job_id = f"job_{len(CREATED) + 1:04d}"
            CREATED[key if key is not None else f"__nokey_{job_id}"] = job_id
            if STATE["lose_submit"]:
                STATE["lose_submit"] = False
                return self.send(502, err("BAD_GATEWAY", "The response was lost after the job was created"))
            return self.send(201, {"data": {"id": job_id, "status": "waiting"}})
        if u.path == "/__created" and method == "GET":
            return self.send(200, {"data": {"count": len(CREATED)}})

        for m, rx, p, op in ROUTES:
            mm = rx.match(u.path)
            if m != method or not mm:
                continue
            secured = op.get("security", SPEC.get("security")) not in ([], None)
            if secured and not (self.headers.get("Authorization") or "").startswith("Bearer "):
                return self.send(401, err("UNAUTHORIZED", "Missing or invalid API key"))
            arg = mm.group(1) if mm.groups() else None
            if p == "/jobs/{id}" and method == "GET":
                if arg == "job_404":
                    return self.send(404, err("NOT_FOUND", "Job not found"))
                if arg == "job_409":
                    return self.send(409, err("CONFLICT", "The job is in a state that does not allow this"))
                if arg == "job_503":
                    return self.send(503, err("SERVICE_UNAVAILABLE", "Try again"))
                if arg == "job_flaky" and HITS[u.path] == 1:
                    return self.send(429, err("RATE_LIMITED", "Slow down"), {"Retry-After": "1"})
                if arg == "job_slow":
                    time.sleep(6)
                status = {"job_done": "complete", "job_failed": "failed"}.get(arg)
                if status is None and arg in CREATED.values():
                    status = "running" if HITS[u.path] == 1 else "complete"
                if status is not None:
                    job = job_of(status)
                    job["id"] = arg
                    return self.send(200, {"data": job})
            if p == "/jobs/{id}/download":
                return self.send(200, raw=b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 1012, ctype="video/mp4")
            status, payload = success(op)
            if p == "/jobs/{id}" and isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                payload["data"]["id"] = arg
                if arg == "job_future":
                    payload["data"]["status"] = "paused"
                    payload["data"]["brandNewField"] = {"added": "later"}
            if p == "/jobs" and method == "GET" and isinstance(payload, dict):
                qs = parse_qs(u.query)
                limit = int((qs.get("limit") or ["50"])[0])
                offset = int((qs.get("offset") or ["0"])[0])
                item = payload["data"][0]
                count = max(0, min(limit, 120 - offset))
                payload["data"] = [dict(item, id=f"job_{offset + i:04d}") for i in range(count)]
                payload["meta"] = {"total": 120, "page": offset // max(limit, 1) + 1, "limit": limit}
            # Deprecated operations answer with RFC 9745 Deprecation, from the spec's own x-deprecated-since.
            extra = {}
            since = op.get("x-deprecated-since")
            if op.get("deprecated") and isinstance(since, str):
                import datetime
                day = datetime.datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
                extra["Deprecation"] = f"@{int(day.timestamp())}"
            return self.send(status, payload, extra)
        self.send(404, err("NOT_FOUND", f"No route {method} {u.path}"))

    def do_GET(self): self.handle_any("GET")
    def do_POST(self): self.handle_any("POST")
    def do_PATCH(self): self.handle_any("PATCH")
    def do_DELETE(self): self.handle_any("DELETE")
    def do_PUT(self): self.handle_any("PUT")


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
