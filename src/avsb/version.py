"""
Package identity, read from installed metadata rather than hand-maintained.

``avsb-python/1.0.0`` used to be a literal in the datafile fetcher's
User-Agent. A literal drifts the first time the version is bumped and
nobody notices, so every version string in this SDK (the User-Agent, the
heartbeat body, error messages) resolves through here.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _installed_version

#: Distribution name on PyPI.
PACKAGE_NAME = "avsb"

#: What this SDK calls itself in the heartbeat body. The dashboard's
#: Environments page prints it verbatim, and the wire contract fixes the
#: spelling, so it is not derived from ``PACKAGE_NAME``.
SDK_TYPE = "avsb-python"

#: Reported when the package is not installed as a distribution, which
#: happens when the source tree is on PYTHONPATH directly (editable-less
#: development, or a vendored copy). Honest about not knowing rather than
#: claiming a version.
UNKNOWN_VERSION = "0.0.0+unknown"


def _resolve_version() -> str:
    try:
        return _installed_version(PACKAGE_NAME)
    except PackageNotFoundError:
        return UNKNOWN_VERSION


#: The installed package version.
SDK_VERSION: str = _resolve_version()

#: Sent on every datafile fetch, so a support thread can identify the
#: client from an access log alone.
USER_AGENT: str = f"{SDK_TYPE}/{SDK_VERSION}"
