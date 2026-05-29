"""
OpenFeature provider tests.

Skipped when openfeature-sdk is not installed.  Tests that the provider
returns the correct FlagResolutionDetails structure for known flag states.
"""

import pytest

try:
    import openfeature  # noqa: F401

    _OF_AVAILABLE = True
except ImportError:
    _OF_AVAILABLE = False


@pytest.mark.skipif(not _OF_AVAILABLE, reason="openfeature-sdk not installed")
class TestAvsbOpenFeatureProvider:
    def _make_server(self, datafile_dict: dict) -> object:
        from avsb.client import AvsbServer

        return AvsbServer(sdk_key="test", datafile=datafile_dict)

    def _make_provider(self, server) -> object:
        from avsb.openfeature_provider import AvsbOpenFeatureProvider

        return AvsbOpenFeatureProvider(server)

    def _minimal_df(self, flags=None, audiences=None) -> dict:
        return {
            "version": 2,
            "projectType": "FEATURE_FLAG",
            "sdkKey": "sdk-test",
            "environmentKey": "test",
            "publishedAt": "2026-01-01T00:00:00Z",
            "collectEndpoint": "https://ingest.avsb.cloud/v1/events",
            "flags": flags or [],
            "audiences": audiences or [],
        }

    def test_resolve_not_found_returns_default(self):
        server = self._make_server(self._minimal_df())
        provider = self._make_provider(server)
        result = provider.resolve_boolean_details("missing-flag", False)
        assert result.value is False

    def test_resolve_boolean_enabled_flag(self):
        flags = [
            {
                "id": "flag_1",
                "key": "my-bool-flag",
                "type": "boolean",
                "enabled": True,
                "defaultVariationId": "var_on",
                "variations": [
                    {"id": "var_on", "key": "on", "value": True},
                    {"id": "var_off", "key": "off", "value": False},
                ],
                "overrides": [],
                "rules": [],
            }
        ]
        server = self._make_server(self._minimal_df(flags=flags))
        provider = self._make_provider(server)
        result = provider.resolve_boolean_details("my-bool-flag", False)
        assert result.value is True

    def test_resolve_string_flag(self):
        flags = [
            {
                "id": "flag_str",
                "key": "color-theme",
                "type": "string",
                "enabled": True,
                "defaultVariationId": "var_dark",
                "variations": [
                    {"id": "var_dark", "key": "dark", "value": "dark"},
                    {"id": "var_light", "key": "light", "value": "light"},
                ],
                "overrides": [],
                "rules": [],
            }
        ]
        server = self._make_server(self._minimal_df(flags=flags))
        provider = self._make_provider(server)
        result = provider.resolve_string_details("color-theme", "light")
        assert result.value == "dark"

    def test_metadata_name(self):
        server = self._make_server(self._minimal_df())
        provider = self._make_provider(server)
        assert provider.name == "AvsB"

    def test_disabled_flag_reason(self):
        from openfeature.flag_evaluation import Reason

        flags = [
            {
                "id": "flag_off",
                "key": "off-flag",
                "type": "boolean",
                "enabled": False,
                "defaultVariationId": "var_off",
                "variations": [
                    {"id": "var_off", "key": "off", "value": False},
                ],
                "overrides": [],
                "rules": [],
            }
        ]
        server = self._make_server(self._minimal_df(flags=flags))
        provider = self._make_provider(server)
        result = provider.resolve_boolean_details("off-flag", True)
        assert result.reason == Reason.DISABLED
