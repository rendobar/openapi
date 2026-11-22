# SDK conformance scenarios

Every Rendobar SDK, in every language, passes all of these against
`mock_server.py` before it can release. They encode behaviour, not API shape:
the shape is the OpenAPI document, and these are the promises an SDK adds on top.

Run the mock: `python3 mock_server.py openapi.json <port> <requests.jsonl>`.
`POST /__reset` before each scenario (`?lose_submit=1` for C13, and the flag a
row names for the others).

## Mock controls

| call | does |
|---|---|
| `POST /__reset` | clears hit counters, created jobs and the request list. Each flag set to `1` in the query arms one behaviour: `lose_submit`, `stall_submit`, `phantom_total`, `cancel_created`, `archive_created` |
| `GET /__created` | `{data: {count}}`, the jobs `POST /jobs` created since the reset |
| `GET /__requests` | `{data: [{method, path, query, idempotencyKey}]}`, every request since the reset except `/__` calls, in arrival order, with the path exactly as sent |

Every job the mock returns carries `terminal`, as the API does: `true` for
`complete`, `failed`, `cancelled` and the unknown `archived`, `false` for
`waiting`, `dispatched`, `running` and the unknown `paused`. Counts, bytes,
nanodollars and timestamps are integers, as the spec types them.

"No request" in a row means `GET /__requests` returns an empty list. A count of
requests to a path also comes from `/__requests`.

## Applicability

A scenario applies to every SDK unless its `not for` cell names that SDK.
`typescript` there means the published `@rendobar/sdk`, which only takes
additive changes. An SDK reports an id that is not for it as `"n/a"`. CI
accepts `n/a` only for an id whose row names that SDK and fails on it
anywhere else. A cell that names only some cases (C39) means that SDK runs
the scenario, skips those cases and reports `pass`. A webhook vector that is not for an SDK carries
`"notFor": [...]` with the same names, and that SDK's verifier test skips the
case.

## Scenarios

Timings are wall clock, measured around the SDK call.

| id | scenario | must | not for |
|---|---|---|---|
| C01 | `listJobTypes` with no API key | succeed (public endpoint) | |
| C02 | `getJob("job_x")` | return the job unwrapped from `{data}`, typed | |
| C03 | submit `{type:"ffprobe", params:{command}}` | return the created job id | |
| C04 | iterate all jobs from `listJobs` | yield all 120 through the SDK's pager | |
| C05 | `getJob("job_404")` | raise a typed error exposing `code == "NOT_FOUND"` and status 404 | |
| C06 | `getJob("job_flaky")` (429 + `Retry-After: 1`, then 200) | retry once, waiting about 1 s, then succeed | |
| C07 | `getJob("job_503")` | retry with backoff, then raise a typed 503 error | |
| C08 | `getJob("job_slow")` with a 2 s timeout | fail at about 2 s | |
| C09 | `getJob("job_future")` (`status:"paused"`, unknown field) | not raise, and expose the unknown status | |
| C10 | `downloadJobOutput("job_x")` | return the 1024 bytes | |
| C11 | every request | send `X-Rendobar-Client: <lang>/<version>` | |
| C12 | `getJob("job_404")` | the error carries `requestId` from `X-Request-Id` | |
| C13 | submit with no key after `/__reset?lose_submit=1` | send one generated key on every retry, and `GET /__created` reports exactly 1 job | |
| C14 | call a deprecated operation twice through the SDK (TS: `billing.checkoutPro()`) | surface the `Deprecation` header once, and stay silent on a current endpoint | |
| C15 | client with no key while `RENDOBAR_API_KEY` is set | send that key | |
| C16 | `run` an `ffprobe` job (the mock reports it `running`, then `complete`) | return the complete job, with its output file URL | |
| C17 | `wait("job_failed")` with failures raised | raise a typed job-failed error carrying the job's error code | |
| C18 | `getJob("job_409")` | raise a typed 409 error after exactly one request (a conflict is not retried) | |
| C19 | client with no key and `RENDOBAR_API_KEY` unset, then `getJob("job_x")` | raise a typed error with code `MISSING_API_KEY` whose message names `RENDOBAR_API_KEY` and the SDK's key option. No request | |
| C20 | client with key `sk_x`, then `getJob("job_x")` | raise a typed error with code `INVALID_API_KEY`. No request | typescript |
| C21 | client with base URL `<mock>/` (trailing slash), then `getJob("job_x")` and one `listJobs` page | succeed. The request paths are exactly `/jobs/job_x` and `/jobs`, and no path contains `//` | |
| C22 | after `/__reset?stall_submit=1` (the first submit is created, then answered after 6 s), submit `{type:"ffprobe", params:{command:"https://x/a.mp4"}}` with a 2 s timeout and retries off, then submit the same body again with the key the error carries | the first submit raises at 1.8 to 4 s with an error carrying `idempotencyKey` equal to the key in the request body. The second returns the job id of the first (`job_0001`). `GET /__created` reports 1 | typescript: its abort and timeout errors stay `DOMException` and cannot carry the key, so callers pass their own from `generateIdempotencyKey` |
| C23 | submit `{type:"ffprobe", params:{command:"https://x/a.mp4"}}` with key `conformance-c23`, then the same key with `params:{command:"https://x/b.mp4"}` | the second raises a typed 409 error with code `CONFLICT` and details `jobId` equal to the first job's id, after exactly one `POST /jobs` for that call. `GET /__created` reports 1 | |
| C24 | submit `{type:"ffprobe", params:{command:"https://x/a.mp4"}}` twice with key `conformance-c24` | both return the same job id. The second answer is the replay (200 with `idempotent: true`), and an SDK with a created-result type reports it as a replay. `GET /__created` reports 1 | |
| C25 | `getJob("job_503_ra")` (503 + `Retry-After: 1`, then 200) | succeed after exactly 2 requests, taking 0.9 to 3 s | |
| C26 | `getJob("job_ra_long")` (429 + `Retry-After: 3600`) | raise a typed 429 error with `retryAfter` 3600 seconds, after exactly 1 request and within 2 s (3600 s is above the 60 s cap, so the SDK stops instead of sleeping) | |
| C27 | `getJob("job_ra_date")` (429 + `Retry-After` as an HTTP-date 1 to 2 s ahead, then 200) | succeed after exactly 2 requests, taking 0.9 to 3.5 s | |
| C28 | `getJob("job_501")` | raise a typed 501 error after exactly 1 request (501 is not retried) | |
| C29 | iterate all jobs with page size 500 (the mock clamps `limit` to 100) | yield 120 distinct ids from exactly 2 `GET /jobs` requests, at offset 0 (or no offset) and offset 100 | |
| C30 | after `/__reset?phantom_total=1` (total 120, no jobs from offset 60), iterate all jobs with page size 100 | yield 60 jobs and stop, after exactly 2 `GET /jobs` requests at offset 0 (or no offset) and offset 60 | |
| C31 | `listJobs` with `limit: -1`, then `offset: -1`, then `limit: 0` | each raises a typed invalid-argument error. No request. An SDK whose documented "not set" value is 0 (Go) skips the `limit: 0` call | |
| C32 | `getJob` with ids `""`, `"a/b"` and `".."` | each raises a typed invalid-argument error. No request | |
| C33 | submit `{type:"no_such_type", params:{}}` (the mock answers 400 with `details: [{path, message}]`, `path` an array of segments like the API's) | raise a typed 400 error with code `VALIDATION_ERROR` whose `problems[0].path` is the array `["type"]` and `problems[0].message` is `Unknown job type "no_such_type"` | |
| C34 | `wait("job_stuck")` with a 3 s timeout (the job stays `running`) | raise a typed wait-timeout error at 2.5 to 5 s carrying job id `job_stuck`, last status `running` and the last job read (id `job_stuck`) | |
| C35 | `wait("job_cancelled")` with default options, then `run` an `ffprobe` job after `/__reset?cancel_created=1` (the job reads `running`, then `cancelled`) | `wait` returns the job with status `cancelled`. `run` raises a typed job-failed error with code `JOB_CANCELLED` carrying the job, status `cancelled`. A cancelled job has no `error` object, so the SDK supplies the code | |
| C36 | `getJob("job_slow")` with a 2 s timeout and default retries | raise a timeout error at 1.8 to 4 s after exactly 1 request (a timed-out attempt is not retried) | typescript |
| C37 | `wait("job_future")` with a 3 s timeout (status `paused`, unknown to the SDK, `terminal: false`) | not raise on the unknown status. Raise the wait-timeout error at 2.5 to 5 s with last status `paused` | |
| C38 | client with app name `conformance`, then `getJob("job_x")` | send `X-Rendobar-Client: <lang>/<version> (<runtime>/<runtime version>; app/conformance)`, where `<lang>` is the C11 prefix and `<version>` equals the version in the package manifest (`package.json`, `pyproject.toml`, Go `version.go`) | |
| C39 | every case in `webhook-vectors.json`, including the bare-hex signature, the whitespace-only secret, a timestamp 301 s in the future and one exactly 300 s old | return each case's `expect`. Skip a case whose `notFor` names the SDK | typescript: the bare-hex case only |
| C40 | `wait("job_future_done")` (status `archived`, unknown to the SDK, `terminal: true`), then `run` an `ffprobe` job after `/__reset?archive_created=1` (the job reads `running`, then `archived` with `terminal: true`) | `wait` ends on `terminal` without waiting out its timeout and returns the job, exposing the unknown status `archived`. `run` raises the typed job-failed error carrying the job, status `archived`, and does not return it as a success. This scenario does not fix the error's code | |

An SDK reports results as JSON `{ "C01": "pass" | "n/a" | "fail: <why>", ... }`
and CI fails on any id that is missing, `fail`, or `n/a` where the row does not
name that SDK.
