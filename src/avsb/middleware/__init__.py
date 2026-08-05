"""
Framework integrations: one client per process, attached to every request.

Each module imports its framework lazily, so ``pip install avsb`` pulls in
nothing and importing one integration never requires the others:

* :mod:`avsb.middleware.asgi` for any ASGI app (``scope["state"]``).
* :mod:`avsb.middleware.fastapi` for FastAPI and Starlette.
* :mod:`avsb.middleware.wsgi` for any WSGI app (``environ["avsb.server"]``).
* :mod:`avsb.middleware.flask` for Flask (``flask.g``).
* :mod:`avsb.middleware.django` for Django (``request.avsb``).

None of them create or close the client. A client owns a polling thread and an
event-flush thread, so one per request would leak both: you build one at startup
and hand it in.
"""
