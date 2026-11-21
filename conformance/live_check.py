"""Validate live API responses against the published OpenAPI document.

The spec is what every generated SDK is built from, so a spec that disagrees
with the API (POST /jobs once documented {id,status} while returning
{data:{...}}) breaks every client at once and passes every unit test. This
calls the API for real and checks each response body against the schema the
spec declares for that operation and status. Free calls only: public reads,
and authenticated routes without a key, which must still answer with the
documented ErrorResponse.

    python live_check.py --spec openapi.json --base https://api.rendobar.com
Exit 1 on any mismatch.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

CALLS = [
    # (method, path template, concrete path, body)
    ("get", "/jobs/types", "/jobs/types", None),
    ("get", "/jobs/types/{type}/schema", "/jobs/types/ffprobe/schema", None),
    ("get", "/jobs/types/{type}/schema", "/jobs/types/not.a.type/schema", None),
    ("get", "/jobs/{id}", "/jobs/job_conformance_missing", None),
    ("get", "/jobs", "/jobs?limit=1", None),
    ("post", "/jobs", "/jobs", {"type": "ffprobe", "params": {"command": "https://cdn.rendobar.com/assets/examples/sample.mp4"}}),
    ("get", "/account/capabilities", "/account/capabilities", None),
    ("get", "/api-keys", "/api-keys", None),
    ("get", "/webhooks/endpoints", "/webhooks/endpoints", None),
]


def request(base: str, method: str, path: str, body: object | None) -> tuple[int, dict[str, str], bytes]:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method.upper(),
                                 headers={"Content-Type": "application/json", "User-Agent": "rendobar-conformance/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--base", required=True)
    args = ap.parse_args()
    spec = json.load(open(args.spec))
    registry = Registry().with_resource("urn:spec", Resource.from_contents(spec, default_specification=DRAFT202012))
    failures = 0
    for method, template, path, body in CALLS:
        status, headers, raw = request(args.base, method, path, body)
        op = spec["paths"].get(template, {}).get(method)
        if op is None:
            print(f"FAIL {method.upper()} {path}: operation {template} not in the spec"); failures += 1; continue
        responses = op.get("responses", {})
        declared = responses.get(str(status)) or responses.get(f"{str(status)[0]}XX") or responses.get("default")
        if declared is None:
            print(f"FAIL {method.upper()} {path}: status {status} is not documented (documented: {sorted(responses)})"); failures += 1; continue
        content = declared.get("content", {})
        if "application/json" not in content:
            print(f"ok   {method.upper()} {path}: {status}, no JSON body documented"); continue
        try:
            payload = json.loads(raw)
        except ValueError:
            print(f"FAIL {method.upper()} {path}: {status} body is not JSON"); failures += 1; continue
        pointer = "/paths/" + template.replace("~", "~0").replace("/", "~1") + f"/{method}/responses/{status}/content/application~1json/schema"
        validator = Draft202012Validator({"$ref": f"urn:spec#{pointer}"}, registry=registry)
        errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
        if errors:
            failures += 1
            e = errors[0]
            print(f"FAIL {method.upper()} {path}: {status} body violates the spec at {'/'.join(map(str, e.path)) or '<root>'}: {e.message[:160]}")
        else:
            rid = headers.get("X-Request-Id") or headers.get("x-request-id")
            print(f"ok   {method.upper()} {path}: {status} matches the spec" + ("" if rid else " (no X-Request-Id!)"))
            if not rid: failures += 1
    print(f"{failures} mismatch(es)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
