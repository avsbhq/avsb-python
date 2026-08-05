# Changelog

All notable changes to the `avsb` Python SDK.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project uses [semantic versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Pre-launch overhaul. The SDK could not fetch a configuration, had no way to
record an event, and never refreshed. All three are fixed, along with the
semantics that made Python disagree with the other A vs B SDKs.

### Added

- **Event recording.** `track`, `track_purchase`, and `manual_exposure`, plus
  automatic exposure recording on rule, bandit, holdout, and sticky decisions.
  Delivery is batched with a bounded queue, retried on a jittered backoff curve,
  and flushed on `close()`. Event ids are stamped once at enqueue time, so a
  retried batch is deduplicated rather than double-counted.
- **Background refresh.** A polling thread with plus or minus 20 percent jitter,
  a five-second floor, and a graceful stop that joins rather than kills.
- **`AsyncAvsbServer`**, a real asyncio client on httpx, behind the new
  `avsb[async]` extra. Awaitable I/O, an `asyncio` refresh task, and no
  `asyncio.get_event_loop()`. It mirrors the synchronous surface method for
  method, and shares the same evaluation core.
- **Heartbeats**, so the dashboard's Environments page can show this SDK as
  connected. Sent after each successful configuration load, throttled to at most
  one every two minutes.
- **`py.typed`**, so the SDK's annotations reach mypy, pyright, and editors.
- **`for_user(context)`**, returning a `UserBoundClient` that carries the context
  for a whole request.
- **`get_all_flags(context)`**, with exposures suppressed by default.
- **Runtime overrides**: `set_override`, `set_override_for_context`,
  `clear_override`, `clear_all_overrides`, reported with source
  `runtimeOverride`.
- **Bandit dispatch.** `bandit`-typed rules route through the bandit engine, with
  a deterministic cold-start model when no learned model is supplied. Every skip
  path appends a reason naming the rule.
- **Commerce and alias audience operators.** `viewed_any`, `not_viewed_any`,
  `has_purchased`, `has_not_purchased`, `lifetime_spend_gt`, `order_count_gt`,
  `between`, `last_purchase_older_than`, and the dashboard aliases `is`,
  `is_not`, `does_not_contain`, `does_not_exist`. An audience using any of them
  used to evaluate false for everyone, silently.
- **Loud failures**: an SDK key that does not look like one, a fetch failure with
  the URL and a reproduction curl, a once-per-process empty-context warning, a
  wrong-getter warning naming the right one, and drop counts on every bounded
  queue.
- **Test helpers**: `build_ab_test_rule`, `build_targeted_delivery_rule`,
  `TEST_SDK_KEY`, and injectable transports and senders.
- Conformance suites for the endpoint, wire-shape, and semantics fixture
  families, alongside the existing evaluation corpus.

### Changed

- **The datafile URL is correct.** It was `{cdn}/datafiles/{key}.json`, which
  404s. It is now `{cdn}/{key}/datafile.json`, built by `avsb.endpoints` and
  nowhere else. Datafile reads are keyless by path: no header, no query string.
- **Evaluating before initialisation no longer raises.** It returns the caller's
  default with source `not_ready`, a new value distinct from `not_found`.
- **`is_enabled()` is defined identically in every A vs B SDK**: a decided
  source and a truthy value, where truthy follows JavaScript's rules. `[]` and
  `{}` are truthy here, unlike in Python generally.
- **`exists()` is false for `not_ready`** as well as `not_found`.
- **Initialisation is synchronous.** `server.on_ready()` replaces
  `asyncio.run(server.on_ready())` on the synchronous client.
- **Contexts can be passed positionally**:
  `get_bool_flag(key, default, context)`. The keyword form still works.
- **Typed getters no longer coerce.** Reading a string flag with
  `get_bool_flag` returns your default and warns, instead of returning
  `bool("false") is True`.
- **Sticky bucketing is wired**, at the client layer: lookup before the
  evaluator, save on A/B tests only, and the variation id (never its key) is what
  gets stored.
- **Traffic-allocation and holdout thresholds compare floats**, never a
  truncated integer, matching every other SDK at the bucket boundary.
- **The decision log is bounded** at 10,000 entries, reports what it dropped,
  and warns once when dropping starts. It used to grow forever.
- **The ASGI middleware attaches to `scope["state"]`**, which is where
  `request.state.avsb` reads from. It attached to `scope["avsb"]` while the docs
  promised `request.state.avsb`, so the documented path raised `AttributeError`.
- **The OpenFeature provider subclasses `AbstractProvider`** and implements
  `get_provider_hooks`, which the SDK calls on every evaluation. Multi-kind
  contexts now map to `MultiContext`, so organization-keyed targeting survives.
- **The default logger is loud in development**, silent in production, and
  overridable with `AVSB_LOG_LEVEL`.
- **`SingleContext.user` and `MultiContext.of` are real classmethods**, not
  monkey-patched at import, so autocomplete and type checkers can see them.
- The datafile wire types moved to `avsb.core.datafile_types`. They are still
  exported from `avsb` itself, which is the supported import path.
- Version strings come from installed package metadata rather than a hardcoded
  literal.

### Fixed

- The module docstring's `EvalContext.user(...)` example raised
  `AttributeError`: `EvalContext` is a type alias, not a class. Contexts are now
  built with `SingleContext.user(...)`.
- The README documented constructor callbacks, an `identify` method, a
  `close_sync` method, and an iterable decision log, none of which existed. It
  now documents the real surface.
- `DecisionLog` is iterated through `.entries`, and that is what the docs say.
- Bandit contextual-model lookup resolves `key` and `name` on a sub-context,
  matching the reference engine.

### Removed

- The big-segment module. Nothing consulted it: types and stores with no
  evaluator behind them.
- `DecideOption.EXCLUDE_VARIABLES`, `IGNORE_STICKY_BUCKET`, and
  `ENABLED_FLAGS_ONLY`, which were never implemented.

[Unreleased]: https://github.com/avsbhq/avsb-python/compare/v1.0.0...HEAD
