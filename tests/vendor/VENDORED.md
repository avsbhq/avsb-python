# Vendored conformance fixtures

Do not edit anything in `fixtures/`. It is a copy of
`packages/avsb-conformance/fixtures/`, produced by
`npm run vendor` in that package, so the published repo carries the
corpus its conformance suite needs.

Engine that generated this corpus: @avsbhq/core 1.4.0

Why here: pytest resolves fixtures relative to the test file.

To refresh: run `npm run generate` then `npm run vendor` in
packages/avsb-conformance and commit the result.
