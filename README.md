# avsb: Python SDK

Server-side Python SDK for the [A vs B](https://app.avsb.cloud) feature-flag and experimentation platform.

Python 3.10+. Sync (`AvsbServer`) and async (`AsyncAvsbServer`) APIs. Framework integrations for FastAPI, Flask, and Django.

---

## 1. Install

```bash
pip install avsb
```

Optional extras:

```bash
pip install avsb[fastapi]     # FastAPI / ASGI middleware
pip install avsb[flask]       # Flask extension
pip install avsb[django]      # Django middleware
pip install avsb[redis]       # Redis sticky-bucket backend
pip install avsb[aws]         # DynamoDB sticky-bucket backend
pip install avsb[postgres]    # PostgreSQL sticky-bucket backend
pip install avsb[streaming]   # SSE datafile streaming (httpx)
pip install avsb[openfeature] # OpenFeature provider
```

**Runtime requirement:** Python 3.10 or later.

---

## 2. Quickstart

```python
import asyncio
import os
from avsb import AvsbServer, SingleContext

server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
asyncio.run(server.on_ready())

ctx = SingleContext.user(key="u_1", plan="pro")
show_new_checkout = server.get_bool_flag("checkout-v2", False, context=ctx)

if show_new_checkout:
    # serve new checkout flow
    pass
```

`get_bool_flag` returns a raw `bool`. Use `get_flag` when you need the full evaluation envelope:

```python
flag = server.get_flag("checkout-v2", False, context=ctx)
print(flag.value)          # True / False
print(flag.variation_key)  # "on" / "off" / ...
print(flag.source)         # "rule" / "default" / "not_found" / ...
print(flag.is_enabled())   # True when source=="rule" and value is truthy
```

### Async variant

`AsyncAvsbServer` inherits all flag-evaluation methods synchronously. The only async surface is `on_ready()`, which fetches the datafile without blocking the event loop:

```python
import asyncio
import os
from avsb import AsyncAvsbServer, SingleContext

async def main():
    server = AsyncAvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
    await server.on_ready()

    ctx = SingleContext.user(key="u_1", plan="pro")
    show_banner = server.get_bool_flag("promo-banner", False, context=ctx)

asyncio.run(main())
```

---

## 3. SDK keys

Get a **server SDK key** from your A vs B project's Environments page. Store it in an environment variable; never hard-code it.

```bash
export AVSB_SDK_KEY=sdk-...
```

```python
import os
sdk_key = os.environ["AVSB_SDK_KEY"]
```

Never use the public (client-side) SDK key in server code. Server SDK keys carry elevated trust and must not be exposed in browser bundles or client responses.

---

## 4. Identity

### `SingleContext.user(key, **attrs)`: build a user context

```python
from avsb import SingleContext

ctx = SingleContext.user(key="u_123", plan="pro", beta_tester=True)
```

### `on_ready()` / `on_error()`: lifecycle callbacks (Ruby-style)

`AvsbServer` fires the `on_ready` and `on_error` callbacks supplied at construction when the datafile is loaded or when an error occurs:

```python
def handle_ready(result):
    print(f"SDK ready, source={result['source']}")

def handle_error(err):
    print(f"SDK init error: {err}")

server = AvsbServer(
    sdk_key=os.environ["AVSB_SDK_KEY"],
    on_ready=handle_ready,
    on_error=handle_error,
)
```

### `identify(context)`: notify downstream integrations

```python
server.identify(ctx)
```

### `flush_decision_log()`: retrieve and clear the decision log buffer

```python
log = server.flush_decision_log()
for entry in log:
    print(entry.flag_key, entry.source, entry.value)
```

---

## 5. Multi-context

A vs B supports multi-context evaluation: bucketing a flag against both a user and an organisation simultaneously.

```python
from avsb import SingleContext, MultiContext

ctx = MultiContext(
    kind="multi",
    contexts={
        "user": SingleContext(kind="user", key="u_123"),
        "organization": SingleContext(
            kind="organization",
            key="org_acme",
            attributes={"tier": "enterprise"},
        ),
    },
)

flag = server.get_bool_flag("enterprise-feature", False, context=ctx)
```

When the rule specifies `hashAttribute: "organization.key"`, users in the same organisation always land in the same variation regardless of their individual user key.

---

## 6. Reading flags

### Typed shortcut methods

```python
show_banner  = server.get_bool_flag("promo-banner", False, context=ctx)
theme        = server.get_string_flag("ui-theme", "light", context=ctx)
max_retries  = server.get_number_flag("max-retries", 3.0, context=ctx)
config       = server.get_json_flag("api-config", {"timeout": 5000}, context=ctx)
```

Each shortcut returns the raw typed value. A `default_value` is required on every call.

### `get_flag`: full `Flag` envelope

```python
from avsb import Flag

flag: Flag = server.get_flag("checkout-v2", False, context=ctx)

flag.value           # T: the evaluated value
flag.variation_key   # str | None: matched variation key
flag.source          # EvaluationSource literal
flag.rule_id         # str | None
flag.rule_type       # RuleType literal | None
flag.reasons         # tuple[str, ...]
flag.evaluated_at    # int: ms epoch
flag.duration_micros # int: microseconds spent in evaluator
flag.is_enabled()    # bool: source=="rule" and value is truthy
flag.exists()        # bool: source != "not_found"
```

### `flush_decision_log`: all flags evaluated this request

```python
entries = server.flush_decision_log()
```

Each `DecisionLogEntry` contains the same fields as `Flag` plus `context_summary` (kind + key only; attributes are never logged).

---

## 7. Tracking events

```python
from avsb.core.types import TrackPayload

server.track(
    "purchase_completed",
    context=ctx,
    value=49.99,
    properties={"plan": "pro", "currency": "usd"},
)
```

`value` maps to the numeric metric quantity (generalises `revenue`). `properties` is forwarded as-is to the collection endpoint. Both are optional.

If you use a framework middleware, the bound client on the request object exposes the same signature:

```python
# FastAPI
request.state.avsb.track("button_clicked", context=ctx)
```

---

## 8. Error handling

### Logger

`AvsbServer` accepts any object with `debug`, `info`, `warn`, and `error` methods. By default it uses the `AvsbLogger` wrapper around Python's stdlib `logging`:

```python
import logging
from avsb import AvsbServer
from avsb.logger import AvsbLogger

logging.basicConfig(level=logging.WARNING)
server = AvsbServer(
    sdk_key=os.environ["AVSB_SDK_KEY"],
    logger=AvsbLogger("myapp.avsb"),
)
```

Pass any `logging.Logger`-compatible object or a custom object that implements `debug(msg)`, `info(msg)`, `warn(msg)`, `error(msg)`.

### `on_error` callback

```python
server = AvsbServer(
    sdk_key=os.environ["AVSB_SDK_KEY"],
    on_error=lambda err: sentry_sdk.capture_exception(err),
)
```

### `on_ready` degraded mode

When the initial datafile fetch fails, `on_ready` is called with `{"success": False, "source": "error", "degraded": True}`. The SDK continues to return default values and retries on the next polling cycle. No exception is raised.

---

## 9. Pre-evaluating flags for server-rendered responses

For request-scoped rendering (Django views, Flask routes, FastAPI handlers), pre-evaluate flag state at the top of the handler and pass the resolved values to your template. This avoids evaluating the same flag multiple times per request.

```python
# FastAPI example
from fastapi import FastAPI, Request
from avsb.middleware.asgi import AvsbAsgiMiddleware
from avsb import AvsbServer, SingleContext

app = FastAPI()
server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
app.add_middleware(AvsbAsgiMiddleware, server=server)

@app.get("/checkout")
async def checkout(request: Request):
    ctx = SingleContext.user(key=request.state.user_id, plan=request.state.plan)
    flags = {
        "new_checkout": server.get_bool_flag("checkout-v2", False, context=ctx),
        "one_click":    server.get_bool_flag("one-click-buy", False, context=ctx),
    }
    # Pass flags to your template renderer.
    return templates.TemplateResponse("checkout.html", {"request": request, **flags})
```

The decision log accumulates all evaluations made during the request. Call `flush_decision_log()` at the end of the request to send the log to your analytics pipeline.

### Framework middleware

**FastAPI / ASGI:**

```python
from avsb.middleware.asgi import AvsbAsgiMiddleware

app.add_middleware(AvsbAsgiMiddleware, server=server)
# Access via request.scope["avsb"] (raw) or request.state.avsb (starlette).
```

**Flask:**

```python
from avsb.middleware.flask import AvsbFlask

avsb_ext = AvsbFlask(app, server=AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"]))
# Access via flask.g.avsb in request handlers.
```

**Django:**

```python
# settings.py
MIDDLEWARE = [
    "avsb.middleware.django.AvsbDjangoMiddleware",
    ...
]
AVSB_SERVER = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
# Access via request.avsb in views.
```

---

## 10. Graceful shutdown

Use `AvsbServer` as a context manager to ensure the decision log is flushed and background threads are stopped when your application exits:

```python
with AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"]) as server:
    asyncio.run(server.on_ready())
    # ... serve requests ...
# __exit__ calls server.close(), stopping the polling thread.
```

Or call `close()` explicitly at SIGTERM:

```python
import signal

server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])

def shutdown(sig, frame):
    server.close()

signal.signal(signal.SIGTERM, shutdown)
```

For `AsyncAvsbServer`, the same `close()` method is available. Both variants flush the in-memory decision log buffer before stopping.

---

## 11. Testing

Install the test extra:

```bash
pip install avsb[test]
```

Use `InMemoryDatafileLoader` to supply a fake datafile without network calls:

```python
from avsb.test_utils import InMemoryDatafileLoader, build_minimal_datafile, build_boolean_flag_entry
from avsb import AvsbServer, SingleContext

def make_server(**flag_overrides):
    datafile = build_minimal_datafile(
        flags=[
            build_boolean_flag_entry(
                "checkout-v2",
                enabled=True,
                default_value=False,
                **flag_overrides,
            )
        ]
    )
    loader = InMemoryDatafileLoader(datafile)
    server = AvsbServer(sdk_key="sdk-test", datafile=loader.load())
    return server
```

pytest fixture pattern:

```python
import pytest
from avsb import AvsbServer, SingleContext
from avsb.test_utils import build_minimal_datafile, build_boolean_flag_entry

@pytest.fixture
def avsb_server():
    datafile = build_minimal_datafile(flags=[
        build_boolean_flag_entry("new-checkout", enabled=True)
    ])
    return AvsbServer(sdk_key="sdk-test", datafile=datafile)

def test_checkout_flag_enabled(avsb_server):
    ctx = SingleContext.user(key="alice")
    flag = avsb_server.get_flag("new-checkout", False, context=ctx)
    assert flag.exists()
    assert flag.source in ("rule", "default")
```

`build_minimal_datafile` returns a valid datafile dict. `build_boolean_flag_entry` builds a minimal flag entry with on/off variations. Both accept keyword overrides for every field so you can target specific rules or audiences inline.

`FakeClock` controls time in tests that assert on `evaluated_at` or assignment age:

```python
from avsb.test_utils import FakeClock

clock = FakeClock(start_ms=1_700_000_000_000)
clock.advance(ms=5000)
print(clock.now_ms)  # 1_700_000_005_000
```

---

## 12. Migration from LaunchDarkly / Statsig

### From LaunchDarkly Python SDK

```python
# LaunchDarkly
import ldclient
from ldclient.config import Config

ldclient.set_config(Config("sdk-key"))
client = ldclient.get()

user = {"key": "alice", "custom": {"plan": "pro"}}
value = client.variation("checkout-v2", user, False)
```

```python
# A vs B
import os
from avsb import AvsbServer, SingleContext

server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])

ctx = SingleContext.user(key="alice", plan="pro")
value = server.get_bool_flag("checkout-v2", False, context=ctx)
```

```python
# LaunchDarkly (multi-context, LDContext)
from ldclient import Context
ctx = Context.multi_builder()\
    .add(Context.builder("alice").kind("user").build())\
    .add(Context.builder("acme").kind("organization").set("tier", "enterprise").build())\
    .build()
```

```python
# A vs B (multi-context)
from avsb import SingleContext, MultiContext

ctx = MultiContext(
    kind="multi",
    contexts={
        "user": SingleContext(kind="user", key="alice"),
        "organization": SingleContext(kind="organization", key="acme", attributes={"tier": "enterprise"}),
    },
)
```

Key differences:
- `get_bool_flag` requires an explicit `default_value`; there is no untyped `.variation()`.
- `get_flag` returns a `Flag` dataclass with `value`, `variation_key`, `source`, `reasons`, and helpers `is_enabled()` / `exists()`. No separate detail call is needed.
- Multi-context is a first-class constructor, not a builder pattern.

### From Statsig Python SDK

```python
# Statsig
import statsig
from statsig import StatsigUser

statsig.initialize("secret-key")
user = StatsigUser(user_id="alice", custom={"plan": "pro"})
gate = statsig.check_gate(user, "checkout-v2")
```

```python
# A vs B
from avsb import AvsbServer, SingleContext

server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
ctx = SingleContext.user(key="alice", plan="pro")
gate = server.get_bool_flag("checkout-v2", False, context=ctx)
```

```python
# Statsig (track event)
statsig.log_event(user, "purchase", value=49.99, metadata={"plan": "pro"})
```

```python
# A vs B (track event)
server.track("purchase", context=ctx, value=49.99, properties={"plan": "pro"})
```
