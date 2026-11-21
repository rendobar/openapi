# Rendobar OpenAPI

The published OpenAPI 3.1 document for the [Rendobar](https://rendobar.com) API, and the conformance suite every Rendobar SDK is held to.

- `openapi.json`: a snapshot of `https://api.rendobar.com/openapi.json`, refreshed hourly. `CHANGELOG.md` records each change.
- `conformance/`: the behaviour every SDK must have on top of the spec, in every language.
  - `SCENARIOS.md`: the scenario ids (retry-safe submits, `Retry-After`, typed errors, request ids, deprecation notices, ...).
  - `mock_server.py`: a dependency-free mock API driven by `openapi.json`. Run `python3 mock_server.py openapi.json 4100 requests.jsonl`.
  - `webhook-vectors.json`: signature cases every webhook verifier must agree with.
  - `live_check.py`: calls the live API and checks each response against this document.

Generate a client for any language from `openapi.json`. Each live job type has a typed submit variant (`SubmitFfprobeJob`, ...) and errors share one `ErrorResponse`.

Official SDKs: [TypeScript](https://github.com/rendobar/rendobar-typescript), [Python](https://github.com/rendobar/rendobar-python), [Go](https://github.com/rendobar/rendobar-go).

MIT. Rendobar is a product of Craftobar LLC.
