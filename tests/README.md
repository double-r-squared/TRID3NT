# tests/ — Test harnesses and acceptance suites

**Owner:** `testing` specialist.

Protocol-conformance tests, contract round-trip suites, negative controls, NFR
verification, acceptance records, and regression suites (SRS v0.3 — testing
domain). `infra` owns only the CI plumbing that *runs* this; the test content is
`testing`'s.

The v0.2 `tests/contracts/` suite (from the pre-pivot stack) was deleted in
`job-0012`. The first v0.3 acceptance suite lands in `job-0017`.
