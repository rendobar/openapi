"""Deterministic mock of the Rendobar API, driven by the published OpenAPI document.

Part of the SDK conformance suite (SCENARIOS.md). Every Rendobar SDK runs its
scenarios against this server, so all languages are held to one behaviour.

Responses are synthesized from the spec's own response schemas, so any generator's
deserializer sees data the spec says is valid. Scenario ids exercise behaviour:

  GET /jobs/job_404      -> 404 error envelope
  GET /jobs/job_flaky    -> 429 (Retry-After: 1) on the first hit, 200 after
  GET /jobs/job_503      -> 503 every time (retry exhaustion)
  GET /jobs/job_503_ra   -> 503 (Retry-After: 1) on the first hit, 200 after
  GET /jobs/job_ra_long  -> 429 (Retry-After: 3600) every time
  GET /jobs/job_ra_date  -> 429 on the first hit, Retry-After an HTTP-date 1 to 2 s ahead; 200 after
  GET /jobs/job_501      -> 501 every time (must not be retried)
  GET /jobs/job_409      -> 409 every time (must not be retried)
  GET /jobs/job_slow     -> 200 after 6 s (client timeout test)
  GET /jobs/job_future   -> 200 with status "paused" (new enum value), terminal false, + unknown field
  GET /jobs/job_future_done -> 200 with status "archived" (new enum value), terminal true, + unknown field
  GET /jobs/job_done     -> 200, a complete job with an output file
  GET /jobs/job_failed   -> 200, a failed job with an error code and message
  GET /jobs/job_stuck    -> 200, a running job, every time
  GET /jobs/job_cancelled -> 200, a cancelled job (JobCancelled, which has no error object)
  GET /jobs/<created id> -> a job POST /jobs created: running on the first poll, complete after
                            (cancelled after, for a job created after /__reset?cancel_created=1,
                            archived after, for a job created after /__reset?archive_created=1)
  GET /jobs?limit&offset -> 120 jobs total, paginated, limit clamped to 100; after
                            /__reset?phantom_total=1 the total stays 120 but no job
                            exists from offset 60
  GET /jobs/{id}/download -> 1 KiB of bytes, video/mp4
  POST /jobs              -> 400 VALIDATION_ERROR with details [{path: [segments], message}] without
                             a string `type`, with non-object `params`, or with the
                             type "no_such_type"; dedupes on idempotencyKey like the
                             real API, and a reused key whose type, inputs or params differ is
                             a 409 CONFLICT with details {jobId}; the first submit after
                             /__reset?lose_submit=1 is created and then answered with a
                             502 (lost response); the first submit after
                             /__reset?stall_submit=1 is created and then answered only
                             after 6 s (a client timeout cuts it off)
  deprecated operations    -> Deprecation: @<x-deprecated-since> (RFC 9745)
  every response           -> X-Request-Id: req_conformance_<n>
  POST /__reset           -> clears hit counters, created jobs and the request list, and
                             sets the flags named =1 in its query: lose_submit,
                             stall_submit, phantom_total, cancel_created, archive_created
  GET /__created          -> {data: {count}}: jobs POST /jobs created since the reset
  GET /__requests         -> {data: [{method, path, query, idempotencyKey (from the body)}]}: every
                             request since the reset except /__ control calls, with the
                             path exactly as sent (a doubled slash stays doubled)
Every job carries terminal: true for complete, failed, cancelled (and archived), false otherwise.
Every request is appended to the log file (method, path, query, selected headers).

Run: python3 -I mock_server.py <spec> <port> <logfile>
"""
import email.utils, hashlib, json, sys, time, re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

SPEC = json.load(open(sys.argv[1]))
PORT = int(sys.argv[2])
LOG = sys.argv[3]
HITS = {}
CREATED = {}  # idempotency key -> (job id, hash of the submit's type, inputs and params)
ENDINGS = {}  # created job id -> the status it ends in when not "complete"
REQUESTS = []  # every non-control request since the last reset
FLAGS = ("lose_submit", "stall_submit", "phantom_total", "cancel_created", "archive_created")
VARIANTS = {"waiting": "JobWaiting", "dispatched": "JobDispatched", "running": "JobRunning",
            "complete": "JobComplete", "failed": "JobFailed", "cancelled": "JobCancelled"}
# "paused" and "archived" stand for statuses an SDK does not know yet: one still going, one ended.
TERMINAL = {"complete", "failed", "cancelled", "archived"}
STATE = dict({f: False for f in FLAGS}, seq=0)


def resolve(s):
    if "$ref" in s:
        name = s["$ref"].split("/")[-1]
        return SPEC["components"]["schemas"][name]
    return s


def job_of(status):
    """A synthesized job in one status, from that status's own variant schema.

    A status the spec does not know reuses the waiting variant's fields and adds an unknown one.
    """
    job = synth({"$ref": f"#/components/schemas/{VARIANTS.get(status, 'JobWaiting')}"})
    job["status"] = status
    job["terminal"] = status in TERMINAL
    if status not in VARIANTS:
        job["brandNewField"] = {"added": "later"}
    return job


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
        return 1731000000000 if timestamp else 0.5  # in range for a 0..1 fraction such as progress
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


def err(code, msg, details=None):
    body = {"code": code, "message": msg}
    if details is not None:
        body["details"] = details
    return {"error": body}


def invalid(path, msg):
    """A 400 body like the API's: each problem's path is a list of segments (a Zod issue path)."""
    return err("VALIDATION_ERROR", msg, [{"path": path, "message": msg}])


def body_key(body):
    """The idempotencyKey a JSON request body carries, or None."""
    try:
        req = json.loads(body)
    except ValueError:
        return None
    return req.get("idempotencyKey") if isinstance(req, dict) else None


def submit_hash(req):
    return hashlib.sha256(json.dumps([req.get("type"), req.get("inputs"), req.get("params", {})], sort_keys=True).encode()).hexdigest()


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
        # The target exactly as sent. self.path has a leading "//" collapsed to "/" (Python 3.12+),
        # which would hide the doubled slash a base URL with a trailing "/" produces.
        target = self.requestline.split(" ")[1] if self.requestline.count(" ") >= 2 else self.path
        path, _, query = target.partition("?")
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        HITS[path] = HITS.get(path, 0) + 1
        if not path.startswith("/__"):
            REQUESTS.append({"method": method, "path": path, "query": query, "idempotencyKey": body_key(body)})
        with open(LOG, "a") as f:
            f.write(json.dumps({
                "t": time.time(), "m": method, "path": path, "q": query,
                "hit": HITS[path],
                "ua": self.headers.get("User-Agent"),
                "auth": (self.headers.get("Authorization") or "")[:12],
                "client": self.headers.get("X-Rendobar-Client"),
                "idem": self.headers.get("Idempotency-Key"),
                "ctype": self.headers.get("Content-Type"),
                "extra": {k: v for k, v in self.headers.items() if k.lower().startswith(("x-", "idempotency"))},
                "body": body.decode(errors="replace")[:400],
            }) + "\n")

        if path == "/__reset":
            HITS.clear(); CREATED.clear(); ENDINGS.clear(); REQUESTS.clear()
            flags = parse_qs(query)
            for f in FLAGS:
                STATE[f] = flags.get(f) == ["1"]
            return self.send(204)
        if path == "/jobs" and method == "POST":
            if not (self.headers.get("Authorization") or "").startswith("Bearer "):
                return self.send(401, err("UNAUTHORIZED", "Missing or invalid API key"))
            try:
                req = json.loads(body or b"{}")
            except ValueError:
                return self.send(400, invalid([], "Body is not JSON"))
            if not isinstance(req, dict) or not isinstance(req.get("type"), str):
                return self.send(400, invalid(["type"], "A submit needs a string type"))
            if not isinstance(req.get("params", {}), dict):
                return self.send(400, invalid(["params"], "params must be an object"))
            if req["type"] == "no_such_type":
                return self.send(400, invalid(["type"], 'Unknown job type "no_such_type"'))
            key = req.get("idempotencyKey")
            if key is not None and key in CREATED:
                job_id, digest = CREATED[key]
                if digest != submit_hash(req):
                    return self.send(409, err("CONFLICT", f'Idempotency key "{key}" is already bound to job {job_id}, '
                                              "submitted with a different type, inputs or params. Use a new key.", {"jobId": job_id}))
                return self.send(200, {"data": {"id": job_id, "status": "waiting", "idempotent": True}})
            job_id = f"job_{len(CREATED) + 1:04d}"
            CREATED[key if key is not None else f"__nokey_{job_id}"] = (job_id, submit_hash(req))
            if STATE["cancel_created"]:
                ENDINGS[job_id] = "cancelled"
            if STATE["archive_created"]:
                ENDINGS[job_id] = "archived"
            if STATE["lose_submit"]:
                STATE["lose_submit"] = False
                return self.send(502, err("BAD_GATEWAY", "The response was lost after the job was created"))
            if STATE["stall_submit"]:
                STATE["stall_submit"] = False
                time.sleep(6)  # the job exists; the client times out before this answer
            return self.send(201, {"data": {"id": job_id, "status": "waiting"}})
        if path == "/__created" and method == "GET":
            return self.send(200, {"data": {"count": len(CREATED)}})
        if path == "/__requests" and method == "GET":
            return self.send(200, {"data": list(REQUESTS)})

        for m, rx, p, op in ROUTES:
            mm = rx.match(path)
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
                if arg == "job_501":
                    return self.send(501, err("NOT_IMPLEMENTED", "Not implemented"))
                if arg == "job_ra_long":
                    return self.send(429, err("RATE_LIMITED", "Slow down"), {"Retry-After": "3600"})
                first = HITS[path] == 1
                if arg == "job_flaky" and first:
                    return self.send(429, err("RATE_LIMITED", "Slow down"), {"Retry-After": "1"})
                if arg == "job_503_ra" and first:
                    return self.send(503, err("SERVICE_UNAVAILABLE", "Try again"), {"Retry-After": "1"})
                if arg == "job_ra_date" and first:
                    # Two whole seconds after the current second: 1 to 2 s from now.
                    when = email.utils.formatdate(int(time.time()) + 2, usegmt=True)
                    return self.send(429, err("RATE_LIMITED", "Slow down"), {"Retry-After": when})
                if arg == "job_slow":
                    time.sleep(6)
                status = {"job_done": "complete", "job_failed": "failed", "job_stuck": "running",
                          "job_cancelled": "cancelled", "job_future": "paused",
                          "job_future_done": "archived"}.get(arg)
                if status is None and arg in {job_id for job_id, _ in CREATED.values()}:
                    status = "running" if first else ENDINGS.get(arg, "complete")
                if status is not None:
                    job = job_of(status)
                    job["id"] = arg
                    return self.send(200, {"data": job})
            if p == "/jobs/{id}/download":
                return self.send(200, raw=b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 1012, ctype="video/mp4")
            status, payload = success(op)
            if p == "/jobs/{id}" and isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                payload["data"]["id"] = arg
                payload["data"]["terminal"] = payload["data"].get("status") in TERMINAL
            if p == "/jobs" and method == "GET" and isinstance(payload, dict):
                qs = parse_qs(query)
                limit = min(int((qs.get("limit") or ["50"])[0]), 100)  # the API's maximum page size
                offset = int((qs.get("offset") or ["0"])[0])
                item = dict(payload["data"][0], terminal=payload["data"][0].get("status") in TERMINAL)
                present = 60 if STATE["phantom_total"] else 120  # phantom_total: the total counts jobs that are gone
                count = max(0, min(limit, present - offset))
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
        self.send(404, err("NOT_FOUND", f"No route {method} {path}"))

    def answer(self, method):
        try:
            self.handle_any(method)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # the client gave up first (a timeout scenario): nothing left to answer

    def do_GET(self): self.answer("GET")
    def do_POST(self): self.answer("POST")
    def do_PATCH(self): self.answer("PATCH")
    def do_DELETE(self): self.answer("DELETE")
    def do_PUT(self): self.answer("PUT")


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
