# avsb

Feature flags and experimentation for Python servers, from [A vs B](https://app.avsb.cloud).

Evaluation is local and in-process: the SDK holds your published configuration in memory and decides in microseconds. There is no proxy to run and no network call per flag.

Python 3.10 or later. The synchronous client has **no dependencies at all**.

---

## 1. Install

```bash
pip install avsb
```

Optional extras, each pulling in exactly one thing:

```bash
pip install "avsb[async]"       # AsyncAvsbServer, on httpx
pip install "avsb[fastapi]"     # FastAPI and Starlette middleware
pip install "avsb[flask]"       # Flask extension
pip install "avsb[django]"      # Django middleware
pip install "avsb[redis]"       # Redis sticky-bucket store
pip install "avsb[aws]"         # DynamoDB sticky-bucket store
pip install "avsb[postgres]"    # PostgreSQL sticky-bucket store
pip install "avsb[openfeature]" # OpenFeature provider
pip install "avsb[streaming]"   # SSE transport (not yet wired into the client)
```

The package ships `py.typed`, so every signature below reaches mypy, pyright, and your editor.

---

## 2. Quickstart

```python
import os
from avsb import AvsbServer, SingleContext

server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
server.on_ready()

ctx = SingleContext.user(key="u_1", plan="pro")

if server.get_bool_flag("checkout-v2", False, ctx):
    serve_new_checkout()

server.track("checkout_completed", ctx, revenue=49.99)

server.close()
```

Or as a context manager, which readies and closes for you:

```python
with AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"]) as server:
    ...
```

Your SDK key comes from the A vs B dashboard: **Settings**, then **Environments**. It looks like `sdk_production_ttqm0eaj4vth1krcb2xn`. Keep it in an environment variable.

---

## 3. Lifecycle

```python
def on_ready(self, timeout: float | None = None) -> InitResult
def refresh_datafile(self) -> bool
def flush(self) -> None
def close(self) -> None
def is_ready(self) -> bool
def is_polling(self) -> bool
def get_init_result(self) -> InitResult | None
```

`on_ready()` fetches your configuration and starts two background threads: one that refreshes it every 30 seconds (jittered), and one that delivers events every 5 seconds. Call it once at startup, and call `close()` at shutdown so queued events are delivered rather than lost.

**It never raises.** A failed fetch comes back inside the result:

```python
class InitResult:
    success: bool
    source: Literal["network", "bootstrap", "timeout", "error"]
    error: Exception | None = None
    degraded: bool = False
```

```python
result = server.on_ready()
if not result.success:
    logging.error("A vs B could not start: %s", result.error)
    # Flags now serve your default values, and polling keeps retrying.
    # A blip at deploy time does not brick anything until restart.
```

Before a configuration is loaded, every flag returns the default value you passed, with source `not_ready`. That is deliberately distinct from `not_found`: one means the SDK has not started, the other means no such flag exists, and the fixes are different.

---

## 4. Contexts

A context is who you are evaluating: their kind, their stable key, and any attributes you target on.

```python
class SingleContext:
    kind: str
    key: str
    name: str | None = None
    attributes: dict[str, Any] = {}

    @classmethod
    def user(cls, key: str, **attributes: Any) -> SingleContext
    @classmethod
    def of(cls, kind: str, key: str, **attributes: Any) -> SingleContext
```

```python
from avsb import SingleContext

ctx = SingleContext.user(key="u_123", plan="pro", beta_tester=True)
org = SingleContext.of("organization", "org_acme", tier="enterprise")
```

Bucketing is a pure function of the key, so the same key always lands in the same variation.

### Multi-context

Evaluate against several entities at once. A rule keyed on `organization.key` then buckets every user in that organization identically, which is what makes an org-wide rollout possible.

```python
from avsb import MultiContext, SingleContext

ctx = MultiContext.of(
    SingleContext.user(key="u_123"),
    SingleContext.of("organization", "org_acme", tier="enterprise"),
)

flag = server.get_bool_flag("enterprise-feature", False, ctx)
```

```python
class MultiContext:
    kind: Literal["multi"]
    contexts: dict[str, SingleContext] = {}

    @classmethod
    def of(cls, *contexts: SingleContext) -> MultiContext
```

`EvalContext` is the union of the two: `EvalContext = SingleContext | MultiContext`.

### Private attributes

Attributes named in `_meta.private_attributes` are never written to a decision log or an exposure event.

```python
from avsb import ContextMeta, SingleContext

ctx = SingleContext(
    kind="user",
    key="u_123",
    attributes={"plan": "pro", "email": "alice@example.com"},
    _meta=ContextMeta(private_attributes=["email"]),
)
```

---

## 5. Reading flags

```python
def get_bool_flag(self, flag_key: str, default_value: bool,
                  context: EvalContext | None = None, *,
                  decide_options: Iterable[DecideOption] | None = None) -> bool

def get_string_flag(self, flag_key: str, default_value: str,
                    context: EvalContext | None = None, *,
                    decide_options: Iterable[DecideOption] | None = None) -> str

def get_number_flag(self, flag_key: str, default_value: float,
                    context: EvalContext | None = None, *,
                    decide_options: Iterable[DecideOption] | None = None) -> float

def get_json_flag(self, flag_key: str, default_value: Any,
                  context: EvalContext | None = None, *,
                  decide_options: Iterable[DecideOption] | None = None) -> Any

def get_flag(self, flag_key: str, default_value: Any,
             context: EvalContext | None = None, *,
             decide_options: Iterable[DecideOption] | None = None) -> Flag

def get_all_flags(self, context: EvalContext | None = None, *,
                  fire_exposures: bool = False) -> dict[str, Flag]
```

```python
show_banner = server.get_bool_flag("promo-banner", False, ctx)
theme       = server.get_string_flag("ui-theme", "light", ctx)
max_retries = server.get_number_flag("max-retries", 3.0, ctx)
config      = server.get_json_flag("api-config", {"timeout": 5000}, ctx)
```

A `default_value` is required on every call, so a flag read always has a defined answer.

**Type mismatches return your default, and say so.** If the flag is declared as a string in the dashboard and you call `get_bool_flag`, you get your default and a warning naming the right getter. Nothing is coerced: `bool("false")` is `True` in Python, and silently shipping that was a real bug.

`get_json_flag` returns whatever the variation holds. Annotate the call site to get a concrete type:

```python
limits: dict[str, int] = server.get_json_flag("limits", {}, ctx)
```

### The full envelope

```python
@dataclass(frozen=True)
class Flag:
    value: Any
    variation_key: str | None
    source: EvaluationSource
    rule_id: str | None
    rule_type: RuleType | None
    reasons: tuple[str, ...]
    evaluated_at: int    # ms since the Unix epoch
    duration_micros: int

    def is_enabled(self) -> bool
    def exists(self) -> bool
```

```python
flag = server.get_flag("checkout-v2", False, ctx)
print(flag.value, flag.variation_key, flag.source)
for reason in flag.reasons:
    print(reason)
```

`reasons` is the ordered decision trail, always collected. It is the fastest way to answer "why did this user get that?":

```
rule "rule_paid_users" skipped (audience not matched)
rule "rule_ab_split" matched (ab_test), bucketed into variation "treatment" (hash 4821/5000)
```

### `Flag.source`, and what each value means

```python
EvaluationSource = Literal[
    "datafileOverride", "runtimeOverride", "sticky", "rule", "holdout",
    "bandit", "default", "not_found", "disabled", "not_ready",
]
```

A **decision** produced the value:

| Value | Meaning |
|---|---|
| `datafileOverride` | An override pinned to this context in the dashboard. |
| `runtimeOverride` | An override set in code with `set_override`. |
| `sticky` | A stored assignment replayed from your sticky-bucket store. |
| `rule` | A targeting rule matched. `rule_type` says which kind. |
| `holdout` | Held out for measurement, so the flag stays at its default variation. |
| `bandit` | A bandit rule chose the variation. |

A **fallback** produced the value, and `is_enabled()` is always `False`:

| Value | Meaning | What to do |
|---|---|---|
| `default` | The flag exists and no rule matched. | Nothing; this is normal. |
| `not_found` | No flag with that key in this environment. | Check the key, and that it is published. |
| `disabled` | The flag is turned off for this environment. | Turn it on in the dashboard. |
| `not_ready` | No configuration loaded yet, or the client was closed. | Call `on_ready()` at startup. |

```python
RuleType = Literal["targeted_delivery", "ab_test", "holdout", "bandit"]
```

### `is_enabled()` and `exists()`

`is_enabled()` is `True` when a real decision produced the value **and** the value is truthy. Truthy follows JavaScript's rules, identically in every A vs B SDK, so a flag reads the same from Python, Node, Go, and the rest:

- Falsy: `False`, `0`, `""`, `None`, `NaN`
- Truthy: everything else, **including `[]` and `{}`**, which Python would call falsy

A flag serving its default variation is not enabled, however truthy that default is: nobody decided anything.

`exists()` is `False` only for `not_found` and `not_ready`. A flag you turned off still exists.

### Reading without recording an exposure

```python
from avsb import DecideOption

flag = server.get_flag(
    "checkout-v2", False, ctx,
    decide_options=[DecideOption.DISABLE_EXPOSURE],
)
```

```python
class DecideOption(str, Enum):
    DISABLE_EXPOSURE = "DISABLE_EXPOSURE"
    INCLUDE_REASONS = "INCLUDE_REASONS"   # accepted for symmetry; reasons are always on
```

`get_all_flags` suppresses exposures by default, so rendering a debug table does not enrol the viewer in every experiment at once.

---

## 6. Request-scoped evaluation

Bind the context once at the top of a handler:

```python
def for_user(self, context: EvalContext) -> UserBoundClient
```

```python
avsb = server.for_user(SingleContext.user(key=user_id, plan=plan))

if avsb.get_bool_flag("checkout-v2", False):
    ...
avsb.track("checkout_started")
```

`UserBoundClient` mirrors every getter plus `get_all_flags`, `track`, and `manual_exposure`, all without the context argument. It is a binding, not a second client: nothing to close, and a configuration refresh is visible immediately.

Purchases stay on the client itself (`server.track_purchase(visitor_id, order)`), because an order sends immediately and its shape differs between the sync and async clients.

---

## 7. Recording events

```python
def track(self, event_key: str, context: EvalContext | None = None, *,
          revenue: float | None = None,
          value: float | None = None,
          properties: Mapping[str, Any] | None = None) -> None

def track_purchase(self, visitor_id: str, order: PurchaseOrder) -> None

def manual_exposure(self, flag_key: str, context: EvalContext) -> None
```

```python
server.track("signup_completed", ctx)
server.track("checkout_completed", ctx, revenue=49.99)
server.track("items_in_basket", ctx, value=3)
```

- `revenue` is money, in decimal major units.
- `value` is the number behind an average-value metric.
- `properties` is accepted for symmetry with exposures, but conversion events have no properties field, so it cannot be stored. Passing it logs one warning naming the drop rather than pretending it arrived. Put the number in `value`, or put the dimension on the evaluation context so it rides the exposure as a registered attribute.

Attribution happens server-side: A vs B links a conversion to the experiments that visitor was exposed to, so you never pass an experiment id.

### Purchases

```python
from avsb import OrderItem, PurchaseOrder

server.track_purchase(
    "u_123",
    PurchaseOrder(
        order_id="ORD-1001",
        total=99.95,
        currency="USD",
        items=[OrderItem(sku="SKU-1", price=99.95, quantity=1)],
    ),
)
```

Orders send immediately rather than waiting for the batch timer, because an order is the event you least want buffered.

### Exposures

Exposures are recorded automatically when a rule, bandit, holdout, or sticky assignment decides a flag. Call `manual_exposure` only when you read a flag early (to warm a cache, say) and the value reached a person later.

### Delivery

Events are batched, retried on a jittered backoff curve, and flushed on `close()`. Every event carries an id stamped once at enqueue time, so a retried batch is deduplicated rather than double-counted.

```python
from avsb import TrackerOptions

server = AvsbServer(
    sdk_key=...,
    tracker_options=TrackerOptions(
        flush_interval_seconds=5.0,
        batch_size=50,
        max_queue_size=1000,
        retry_attempts=3,
        request_timeout_seconds=10.0,
    ),
)
```

A full queue drops the **oldest** events and logs how many. Delivery failures log the endpoint, the status, and a curl to reproduce. Nothing disappears quietly.

Set `events_enabled=False` to evaluate without recording anything. Results pages will then have no data for server-side experiments, so do it deliberately.

---

## 8. The async client

```bash
pip install "avsb[async]"
```

```python
import os
from avsb import SingleContext
from avsb.async_client import AsyncAvsbServer

async def main() -> None:
    async with AsyncAvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"]) as server:
        ctx = SingleContext.user(key="u_1", plan="pro")
        if server.get_bool_flag("checkout-v2", False, ctx):
            ...
        await server.track("checkout_started", ctx)
```

Every network operation is a coroutine on `httpx.AsyncClient`, and the refresh loop is an `asyncio` task rather than an OS thread.

**Awaited:** `on_ready`, `refresh_datafile`, `track`, `track_purchase`, `flush`, `close`.

**Not awaited:** every getter, `get_all_flags`, `for_user`, `manual_exposure`, and the override methods. Evaluation is a dict lookup and a hash against an in-memory configuration, with no I/O; making it a coroutine would add an event-loop round trip per flag read and buy nothing. It also means ordinary synchronous helpers inside an async handler can read flags.

Everything else matches `AvsbServer` name for name, so porting between the two is mechanical.

Share your application's HTTP client if you want one connection pool:

```python
server = AsyncAvsbServer(sdk_key=..., http_client=my_httpx_client)
```

A client you pass is left open by `close()`; one the SDK opened is closed.

---

## 9. Framework integrations

Build one client per process and hand it to the integration. A client owns two background threads, so one per request would leak both.

### FastAPI and Starlette

```python
from fastapi import FastAPI, Request
from avsb import AvsbServer, SingleContext
from avsb.middleware.fastapi import AvsbFastApiMiddleware

app = FastAPI()
server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])

@app.on_event("startup")
def start_avsb() -> None:
    server.on_ready()

@app.on_event("shutdown")
def stop_avsb() -> None:
    server.close()

app.add_middleware(AvsbFastApiMiddleware, server=server)

@app.get("/checkout")
async def checkout(request: Request) -> dict[str, bool]:
    ctx = SingleContext.user(key=request.headers["x-user-id"])
    return {"new_checkout": request.state.avsb.get_bool_flag("checkout-v2", False, ctx)}
```

Pass `context_from` to get a bound client on every request:

```python
app.add_middleware(
    AvsbFastApiMiddleware,
    server=server,
    context_from=lambda scope: SingleContext.user(
        key=dict(scope["headers"]).get(b"x-user-id", b"").decode()
    ),
)

@app.get("/checkout")
async def checkout(request: Request) -> dict[str, bool]:
    return {"new_checkout": request.state.avsb_user.get_bool_flag("checkout-v2", False)}
```

`AvsbAsgiMiddleware` from `avsb.middleware.asgi` is the same class under a framework-neutral name, and works with any ASGI app.

### Flask

```python
from flask import Flask, g
from avsb import AvsbServer, SingleContext
from avsb.middleware.flask import AvsbFlask

app = Flask(__name__)
server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
server.on_ready()
AvsbFlask(app, server=server)

@app.get("/checkout")
def checkout():
    ctx = SingleContext.user(key=g.user_id)
    return {"new_checkout": g.avsb.get_bool_flag("checkout-v2", False, ctx)}
```

### Django

```python
# settings.py
from avsb import AvsbServer

AVSB_SERVER = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
AVSB_SERVER.on_ready()

MIDDLEWARE = [..., "avsb.middleware.django.AvsbDjangoMiddleware"]
```

```python
def checkout(request):
    ctx = SingleContext.user(key=str(request.user.pk))
    if request.avsb.get_bool_flag("checkout-v2", False, ctx):
        ...
```

Set `AVSB_CONTEXT_FROM` to a callable taking the request, and `request.avsb_user` becomes a bound client.

### Plain WSGI

```python
from avsb.middleware.wsgi import AvsbWsgiMiddleware

application = AvsbWsgiMiddleware(application, server=server)
# environ["avsb.server"], and environ["avsb.user"] when context_from is passed.
```

---

## 10. OpenFeature

```bash
pip install "avsb[openfeature]"
```

```python
from openfeature import api
from avsb import AvsbServer
from avsb.openfeature_provider import AvsbOpenFeatureProvider

server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
server.on_ready()
api.set_provider(AvsbOpenFeatureProvider(server))

client = api.get_client()
show_new_checkout = client.get_boolean_value("checkout-v2", False)
```

Multi-kind evaluation contexts map to A vs B multi-contexts, so organization-keyed targeting keeps working through OpenFeature:

```python
from openfeature.evaluation_context import EvaluationContext

ctx = EvaluationContext(
    targeting_key="u_1",
    attributes={"organization": {"key": "org_acme", "tier": "enterprise"}},
)
client.get_boolean_value("enterprise-feature", False, ctx)
```

Source mapping: `rule`, `holdout`, and `bandit` become `TARGETING_MATCH`; overrides become `STATIC`; `sticky` becomes `CACHED`; `default` and `not_found` become `DEFAULT`; `disabled` becomes `DISABLED`; and `not_ready` becomes an error with code `PROVIDER_NOT_READY`, so an uninitialised SDK is visible to OpenFeature's own error handling.

---

## 11. Sticky bucketing

Bucketing is already stable from the context key. A sticky store matters when an assignment must survive a change to the experiment: a new traffic split, or reweighted variations, would otherwise move people between arms mid-test.

```python
import redis
from avsb import AvsbServer
from avsb.sticky.redis import RedisStickyBucketService

server = AvsbServer(
    sdk_key=os.environ["AVSB_SDK_KEY"],
    sticky_bucket_service=RedisStickyBucketService(redis.Redis()),
)
```

Also available: `avsb.sticky.memory.InMemoryStickyBucketService` (single process and tests), `avsb.sticky.dynamodb.DynamoDBStickyBucketService`, and `avsb.sticky.postgres.PostgresStickyBucketService`.

Assignments are saved only for A/B tests. A rollout is not an assignment, and a holdout is not a decision worth remembering. A store outage degrades bucketing stability and logs it; it never breaks an evaluation.

Bring your own by implementing two methods:

```python
class StickyBucketService(Protocol):
    def lookup(self, user_id: str, flag_key: str) -> StickyAssignment | None: ...
    def save(self, user_id: str, flag_key: str, assignment: StickyAssignment) -> None: ...
```

---

## 12. Overrides and the decision log

### Runtime overrides

```python
server.set_override("checkout-v2", "treatment")
server.set_override_for_context("checkout-v2", "u_123", "control")
server.clear_override("checkout-v2")
server.clear_all_overrides()
```

Applied ahead of everything else and reported with source `runtimeOverride`. For local development and incident response: an override lives in the process, so it disappears on restart and does not exist for any other instance.

### Decision log

```python
log = server.flush_decision_log()
for entry in log.entries:
    print(entry.flag_key, entry.source, entry.value)
if log.dropped:
    print(f"{log.dropped} entries were dropped before this flush")
```

Every evaluation is recorded with its reasons, its context summary (kinds and keys only, never attributes), and private attribute values replaced with `[REDACTED]`. The collector keeps the most recent 10,000 entries and says so when it starts dropping; size it with `decision_log_limit`.

---

## 13. Diagnostics

### Logging

The default logger prints warnings to stderr in development and stays silent in production, where your application's logging configuration governs.

Development is anything whose `AVSB_ENV`, `ENVIRONMENT`, `ENV`, `PYTHON_ENV`, `APP_ENV`, `FLASK_ENV`, or `DJANGO_ENV` is not `production`, and an unconfigured process counts as development.

```bash
export AVSB_LOG_LEVEL=debug   # or info, warn, error, none
```

Pass any object with `debug`, `info`, `warn`, and `error`:

```python
from avsb import AvsbLogger, NoopLogger

server = AvsbServer(sdk_key=..., logger=AvsbLogger("myapp.flags"))
server = AvsbServer(sdk_key=..., logger=NoopLogger())
```

### What the SDK tells you out loud

- An SDK key that does not look like one, naming what it looks like instead.
- A configuration fetch failure, with the URL tried, the status, and a curl to reproduce.
- Evaluating with no context, once per process, naming the factory call to add.
- Reading a flag with the wrong typed getter, naming the right one.
- A full event queue or decision log, with the count dropped.
- Events lost after every retry, with the endpoint and last error.

---

## 14. Testing

```bash
pip install "avsb[test]"
```

Pass a configuration directly and the client is ready with no network call:

```python
import pytest
from avsb import AvsbServer, SingleContext
from avsb.test_utils import build_boolean_flag_entry, build_minimal_datafile

@pytest.fixture()
def server():
    datafile = build_minimal_datafile(
        flags=[build_boolean_flag_entry("checkout-v2", default_value=True)]
    )
    client = AvsbServer(
        "sdk_test_conformancefixture01",
        datafile=datafile,
        polling_enabled=False,
        events_enabled=False,
    )
    yield client
    client.close()

def test_new_checkout_is_on(server):
    assert server.get_bool_flag("checkout-v2", False, SingleContext.user(key="u_1"))
```

`polling_enabled=False` and `events_enabled=False` keep the test run off the network entirely.

Helpers in `avsb.test_utils`: `build_minimal_datafile`, `build_boolean_flag_entry`, `build_ab_test_rule`, `build_targeted_delivery_rule`, `InMemoryDatafileLoader`, `FakeClock`, and `TEST_SDK_KEY`.

To assert on what would be sent, inject a recorder:

```python
from avsb.tracker import SendResult

sent = []

def recorder(url: str, body: bytes, timeout: float) -> SendResult:
    sent.append((url, body))
    return SendResult(status=202)

server = AvsbServer("sdk_test_conformancefixture01", datafile=df, event_sender=recorder)
```

---

## 15. Configuration reference

```python
AvsbServer(
    sdk_key: str,
    *,
    datafile: FlagDatafile | Mapping[str, Any] | None = None,
    datafile_url: str | None = None,
    cdn_host: str | None = None,
    polling_interval_seconds: float = 30.0,
    polling_enabled: bool = True,
    init_timeout_seconds: float = 10.0,
    events_enabled: bool = True,
    tracker_options: TrackerOptions | None = None,
    sticky_bucket_service: StickyBucketService | None = None,
    bandit_model_store: BanditModelStore | None = None,
    runtime_overrides: Mapping[str, str] | None = None,
    decision_log_limit: int | None = None,
    logger: Logger | None = None,
    transport: DatafileTransport | None = None,
    event_sender: EventSender | None = None,
    heartbeat_sender: HeartbeatSender | None = None,
)
```

`AsyncAvsbServer` takes the same parameters, minus `transport`, `event_sender`, and `heartbeat_sender`, plus `http_client: httpx.AsyncClient | None`.

Polling is floored at 5 seconds and jittered by plus or minus 20 percent, so a fleet that deployed together does not fetch in lockstep.

---

## 16. Migrating

### From the LaunchDarkly Python SDK

```python
# LaunchDarkly
import ldclient
from ldclient.config import Config

ldclient.set_config(Config("sdk-key"))
client = ldclient.get()
value = client.variation("checkout-v2", {"key": "alice", "custom": {"plan": "pro"}}, False)
```

```python
# A vs B
from avsb import AvsbServer, SingleContext

server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
server.on_ready()
value = server.get_bool_flag("checkout-v2", False, SingleContext.user(key="alice", plan="pro"))
```

Multi-context is a constructor, not a builder:

```python
ctx = MultiContext.of(
    SingleContext.user(key="alice"),
    SingleContext.of("organization", "acme", tier="enterprise"),
)
```

Differences worth knowing: every getter requires an explicit default; `get_flag` returns the full envelope with `reasons` and helpers, so there is no separate detail call; and a type mismatch returns your default rather than coercing.

### From the Statsig Python SDK

```python
# Statsig
import statsig
from statsig import StatsigUser

statsig.initialize("secret-key")
gate = statsig.check_gate(StatsigUser(user_id="alice", custom={"plan": "pro"}), "checkout-v2")
statsig.log_event(user, "purchase", value=49.99)
```

```python
# A vs B
from avsb import AvsbServer, SingleContext

server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
server.on_ready()

ctx = SingleContext.user(key="alice", plan="pro")
gate = server.get_bool_flag("checkout-v2", False, ctx)
server.track("purchase", ctx, revenue=49.99)
```

---

## Links

- [Documentation](https://avsb.cloud/docs)
- [Dashboard](https://app.avsb.cloud)
- [Changelog](CHANGELOG.md)
- [Issues](https://github.com/avsbhq/avsb-python/issues)

MIT licensed.
