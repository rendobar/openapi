# SDK conformance scenarios

Every Rendobar SDK, in every language, passes all of these against
`mock_server.py` before it can release. They encode behaviour, not API shape:
the shape is the OpenAPI document; these are the promises an SDK adds on top.

Run the mock: `python3 mock_server.py openapi.json <port> <requests.jsonl>`.
`POST /__reset` before each scenario (`?lose_submit=1` for C13).

| id | scenario | must |
|---|---|---|
| C01 | `listJobTypes` with no API key | succeed (public endpoint) |
| C02 | `getJob("job_x")` | return the job unwrapped from `{data}`, typed |
| C03 | submit `{type:"ffprobe", params:{command}}` | return the created job id |
| C04 | iterate all jobs from `listJobs` | yield all 120 through the SDK's pager |
| C05 | `getJob("job_404")` | raise a typed error exposing `code == "NOT_FOUND"` and status 404 |
| C06 | `getJob("job_flaky")` (429 + `Retry-After: 1`, then 200) | retry once, waiting about 1 s, then succeed |
| C07 | `getJob("job_503")` | retry with backoff, then raise a typed 503 error |
| C08 | `getJob("job_slow")` with a 2 s timeout | fail at about 2 s |
| C09 | `getJob("job_future")` (`status:"paused"`, unknown field) | not raise; expose the unknown status |
| C10 | `downloadJobOutput("job_x")` | return the 1024 bytes |
| C11 | every request | send `X-Rendobar-Client: <lang>/<version>` |
| C12 | `getJob("job_404")` | the error carries `requestId` from `X-Request-Id` |
| C13 | submit with no key after `/__reset?lose_submit=1` | send one generated key on every retry; `GET /__created` reports exactly 1 job |
| C14 | call a deprecated operation twice through the SDK (TS: `billing.checkoutPro()`) | surface the `Deprecation` header once, and stay silent on a current endpoint |
| C15 | client with no key while `RENDOBAR_API_KEY` is set | send that key |
| C16 | `run` an `ffprobe` job (the mock reports it `running`, then `complete`) | return the complete job, with its output file URL |
| C17 | `wait("job_failed")` with failures raised | raise a typed job-failed error carrying the job's error code |
| C18 | `getJob("job_409")` | raise a typed 409 error after exactly one request (a conflict is not retried) |

An SDK reports results as JSON `{ "C01": "pass" | "fail: <why>", ... }` and CI
fails on any id that is missing or not `pass`.
