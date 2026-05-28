# avsb — A vs B Python SDK

Server-side Python SDK for the A vs B feature-flag and experimentation platform.
Python 3.10+.

## Install

```bash
pip install avsb
# Optional extras
pip install avsb[fastapi]   # FastAPI / ASGI middleware
pip install avsb[flask]     # Flask extension
pip install avsb[django]    # Django middleware
pip install avsb[redis]     # Redis sticky-bucket backend
pip install avsb[aws]       # DynamoDB sticky-bucket backend
pip install avsb[postgres]  # PostgreSQL sticky-bucket backend
pip install avsb[streaming] # SSE datafile streaming (httpx)
pip install avsb[openfeature] # OpenFeature provider
```

## Quickstart

```python
import asyncio
import os
from avsb import AvsbServer, EvalContext

server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
await server.on_ready()

ctx = EvalContext.user(key="u_1", plan="pro")
show_new_checkout = server.get_bool_flag("checkout-v2", False, context=ctx)
```

### Typed flag result

```python
flag = server.get_flag("checkout-v2", False, context=ctx)
print(flag.value)         # True
print(flag.variation_key) # "treatment"
print(flag.source)        # "rule"
print(flag.is_enabled())  # True
```

## Multi-context

```python
from avsb import MultiContext, SingleContext

ctx = MultiContext(
    kind="multi",
    contexts={
        "user": SingleContext(kind="user", key="u_999"),
        "organization": SingleContext(kind="organization", key="org_acme", attributes={"tier": "enterprise"}),
    },
)
flag = server.get_bool_flag("org-enterprise-feature", False, context=ctx)
```

## Bootstrap (no network call)

```python
server = AvsbServer(sdk_key="...", datafile={"version": 2, "flags": [...], ...})
# No on_ready() call needed — datafile already loaded.
```

## Middleware

### FastAPI / ASGI

```python
from fastapi import FastAPI, Request
from avsb.middleware.asgi import AvsbAsgiMiddleware

app = FastAPI()
server = AvsbServer(sdk_key="...")
app.add_middleware(AvsbAsgiMiddleware, server=server)  # type: ignore

@app.get("/")
async def root(request: Request):
    avsb = request.scope["avsb"]
    ...
```

### Flask

```python
from flask import Flask, g
from avsb.middleware.flask import AvsbFlask

app = Flask(__name__)
avsb_ext = AvsbFlask(app, server=AvsbServer(sdk_key="..."))
# Access as g.avsb in request handlers.
```

### Django

In `settings.py`:

```python
from avsb import AvsbServer

MIDDLEWARE = [
    "avsb.middleware.django.AvsbDjangoMiddleware",
    ...
]
AVSB_SERVER = AvsbServer(sdk_key="...")
# Access as request.avsb in views.
```

## Sticky buckets

```python
from avsb.sticky.memory import InMemoryStickyBucketService

svc = InMemoryStickyBucketService()
# Pass to AvsbServer (future extension point).
```

For distributed deployments:

```python
# Redis
from avsb.sticky.redis import RedisStickyBucketService
import redis
r = redis.Redis(host="localhost", port=6379)
svc = RedisStickyBucketService(r)

# DynamoDB
from avsb.sticky.dynamodb import DynamoDBStickyBucketService
import boto3
table = boto3.resource("dynamodb").Table("avsb-sticky-buckets")
svc = DynamoDBStickyBucketService(table)

# PostgreSQL
from avsb.sticky.postgres import PostgresStickyBucketService
import psycopg
conn = psycopg.connect("postgresql://user:pass@host/db")
svc = PostgresStickyBucketService(conn)
```

## OpenFeature provider

```python
from openfeature import api
from avsb.openfeature_provider import AvsbOpenFeatureProvider

api.set_provider(AvsbOpenFeatureProvider(server))
client = api.get_client()
enabled = client.get_boolean_value("my-flag", False)
```

## Decision log

```python
# After each request, flush the decision log.
log = server.flush_decision_log()
for entry in log.entries:
    print(entry.flag_key, entry.source, entry.value)
```

## Testing

```bash
pip install avsb[test]
pytest packages/avsb-python/tests/
```
