"""HERMES_LANGFUSE_CAPTURE resolves per profile, through the profile secret scope."""
from __future__ import annotations

import importlib
import sys

import pytest


class TestCaptureModeProfileScope:
    """HERMES_LANGFUSE_CAPTURE is a per-profile setting (it sits in the profile-isolable env
    list next to SAMPLE_RATE), so it resolves through the profile secret scope like the
    plugin's other settings. Reading ``os.environ`` instead gives every profile served by a
    multiplexed process the launch profile's mode: a profile that chose ``metadata`` would
    export prompt and response content."""

    def _fresh_plugin(self):
        sys.modules.pop("plugins.observability.langfuse", None)
        return importlib.import_module("plugins.observability.langfuse")

    @pytest.fixture
    def multiplex(self):
        from agent import secret_scope

        secret_scope.set_multiplex_active(True)
        try:
            yield secret_scope
        finally:
            secret_scope.set_multiplex_active(False)

    @staticmethod
    def _under(secret_scope, mapping, fn):
        token = secret_scope.set_secret_scope(mapping)
        try:
            return fn()
        finally:
            secret_scope.reset_secret_scope(token)

    def test_two_scopes_in_one_process_each_resolve_their_own_mode(self, monkeypatch, multiplex):
        mod = self._fresh_plugin()
        monkeypatch.setenv("HERMES_LANGFUSE_CAPTURE", "full")  # the launch profile's choice
        quiet = {"HERMES_LANGFUSE_CAPTURE": "metadata"}
        unset: dict[str, str] = {}

        # Interleaved, as a multiplexed gateway runs turns for two profiles.
        seen = [self._under(multiplex, scope, mod._capture_mode) for scope in (quiet, unset, quiet, unset)]

        assert seen == ["metadata", "sanitized", "metadata", "sanitized"]
        prompt = "my ssn is 123-45-6789"
        assert self._under(multiplex, quiet, lambda: mod._capture_content(prompt))["omitted"] is True

    def test_routed_scope_without_a_value_never_inherits_the_host_mode(self, monkeypatch, tmp_path):
        import hermes_constants
        from agent import secret_scope

        mod = self._fresh_plugin()
        monkeypatch.setenv("HERMES_LANGFUSE_CAPTURE", "full")
        launch, served = tmp_path / "launch", tmp_path / "profiles" / "served"
        launch.mkdir()
        served.mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(launch))
        monkeypatch.setattr(hermes_constants, "_PINNED_PROCESS_HERMES_HOME", None)

        token = secret_scope.set_secret_scope({}, profile_home=str(served))
        try:
            assert secret_scope.serves_routed_profile()
            assert mod._capture_mode() == "sanitized"
            assert mod._capture_content("ordinary text sk-" + "a" * 20) != "ordinary text sk-" + "a" * 20
        finally:
            secret_scope.reset_secret_scope(token)

    def test_single_profile_mode_still_reads_os_environ(self, monkeypatch):
        from agent import secret_scope

        mod = self._fresh_plugin()
        monkeypatch.setenv("HERMES_LANGFUSE_CAPTURE", "metadata")
        assert not secret_scope.is_multiplex_active()
        assert mod._capture_mode() == "metadata"
        # The launch profile's own .env overlay without the key falls through to the process env.
        assert self._under(secret_scope, {}, mod._capture_mode) == "metadata"

    def test_invalid_scoped_value_falls_back_to_sanitized(self, monkeypatch, multiplex):
        mod = self._fresh_plugin()
        monkeypatch.setenv("HERMES_LANGFUSE_CAPTURE", "metadata")
        assert self._under(multiplex, {"HERMES_LANGFUSE_CAPTURE": "everything"}, mod._capture_mode) == "sanitized"
