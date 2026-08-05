"""
FastAPI and Starlette integration.

``AvsbFastApiMiddleware`` is :class:`avsb.middleware.asgi.AvsbAsgiMiddleware`
under a name that matches how FastAPI users look for it, wired for
``app.add_middleware``::

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
        return {
            "new_checkout": request.state.avsb.get_bool_flag(
                "checkout-v2", False, ctx
            )
        }

There is deliberately no ``server_factory`` option. A client per request would
create a polling thread and an event-flush thread per request; one client for
the process is the only correct shape, and the middleware takes the one you
built.

Starlette's ``add_middleware`` passes the wrapped app positionally and
everything else as keywords, which is exactly this constructor's shape, so no
adapter is needed.
"""

from __future__ import annotations

from .asgi import AvsbAsgiMiddleware

#: FastAPI-flavoured alias. Identical behaviour, discoverable name.
AvsbFastApiMiddleware = AvsbAsgiMiddleware

__all__ = ["AvsbFastApiMiddleware"]
