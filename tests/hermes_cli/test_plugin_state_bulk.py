"""Public bulk state API, exercised through the plugin context."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from hermes_cli import plugins_state
from hermes_cli import plugins as plugins_mod
import utils


@pytest.fixture
def state(tmp_path):
    token = set_hermes_home_override(tmp_path / "profile")
    try:
        yield PluginContext(PluginManifest(name="bulk-fixture"), PluginManager()).state
    finally:
        reset_hermes_home_override(token)


def test_update_batches_sets_and_deletes_under_one_lock_and_write(state):
    state.set("expired", 1)
    state.set("keep", {"value": 2})
    original_lock = plugins_state._locked_plugin_state
    locks = []

    @contextmanager
    def counted_lock(path):
        locks.append(path)
        with original_lock(path):
            yield

    with patch.object(plugins_state, "_locked_plugin_state", counted_lock), patch.object(
        utils, "atomic_write_bytes", wraps=utils.atomic_write_bytes
    ) as write, patch.object(state, "_read_unlocked", wraps=state._read_unlocked) as read:
        state.update({f"record_{i}": i for i in range(100)}, delete=["expired", "missing"])
        assert len(locks) == 1
        assert read.call_count == 1
        assert write.call_count == 1

    assert state.get("expired") is None
    assert state.get("keep") == {"value": 2}
    assert state.get("record_99") == 99


@pytest.mark.parametrize("key", ["../escape", "", 1, "x" * 129])
@pytest.mark.parametrize("operation", ["set", "delete"])
def test_update_validates_every_key_before_writing(state, key, operation):
    state.set("keep", "safe")
    before = state.path.read_bytes()
    values = {"valid": 2, key: 3} if operation == "set" else {"valid": 2}
    delete = ["keep", key] if operation == "delete" else []
    with pytest.raises(ValueError, match="Plugin state keys"):
        state.update(values, delete=delete)
    assert state.path.read_bytes() == before


def test_update_checks_quota_of_final_file(state, monkeypatch):
    state.set("keep", "safe")
    before = state.path.read_bytes()
    monkeypatch.setattr(plugins_state, "_PLUGIN_STATE_QUOTA_BYTES", 100)
    with pytest.raises(ValueError, match="quota"):
        state.update({"a": "x" * 60, "b": "x" * 60})
    assert state.path.read_bytes() == before
    # Deleting existing values frees quota in the same transaction.
    state.update({"replacement": "x" * 60}, delete=["keep"])
    assert len(state.path.read_bytes()) <= 100


def test_update_writes_the_exact_payload_checked_against_quota(state, monkeypatch):
    class ChangingDict(dict):
        calls = 0

        def items(self):
            self.calls += 1
            return {"value": "x" * (10 if self.calls == 1 else 2000)}.items()

    monkeypatch.setattr(plugins_state, "_PLUGIN_STATE_QUOTA_BYTES", 1000)
    state.update({"record": ChangingDict(value="initial")})
    assert len(state.path.read_bytes()) <= 1000
    assert state.get("record") == {"value": "x" * 10}


def test_update_rejects_non_json_values_without_writing(state):
    state.set("keep", "safe")
    before = state.path.read_bytes()
    with pytest.raises(ValueError, match="not JSON-serializable"):
        state.update({"valid": 2, "invalid": object()}, delete=["keep"])
    assert state.path.read_bytes() == before


def test_update_rejects_overlapping_keys(state):
    state.set("keep", "safe")
    before = state.path.read_bytes()
    with pytest.raises(ValueError, match="both"):
        state.update({"keep": "new"}, delete=["keep"])
    assert state.path.read_bytes() == before


def test_empty_update_does_not_create_state_file(state):
    with patch.object(utils, "atomic_write_bytes", wraps=utils.atomic_write_bytes) as write:
        state.update({})
        assert write.call_count == 0
    assert not state.path.exists()


def test_snapshot_reads_all_values_once_and_returns_detached_data(state):
    assert state.snapshot() == {}
    state.set("nested", {"items": [1]})
    state.set("other", 2)
    with patch.object(state, "_read_unlocked", wraps=state._read_unlocked) as read:
        snapshot = state.snapshot()
        assert read.call_count == 1
    assert snapshot == {"nested": {"items": [1]}, "other": 2}
    snapshot["nested"]["items"].append(3)
    assert state.get("nested") == {"items": [1]}


@pytest.mark.parametrize("delete", ["keep", b"keep"])
def test_update_rejects_string_deletion_argument(state, delete):
    state.set("k", 1)
    before = state.path.read_bytes()
    with pytest.raises(TypeError, match="delete"):
        state.update({}, delete=delete)
    assert state.path.read_bytes() == before


@pytest.mark.parametrize("failure", ["atomic_replace", "fsync"])
def test_update_io_failure_preserves_previous_file(state, failure):
    state.set("keep", "safe")
    before = state.path.read_bytes()
    target = utils if failure == "atomic_replace" else utils.os
    with patch.object(target, failure, side_effect=OSError("injected write failure")):
        with pytest.raises(OSError, match="injected write failure"):
            state.update({"new": 2}, delete=["keep"])
    assert state.path.read_bytes() == before
    assert not list(state.data_dir.glob("*.tmp"))


@pytest.mark.parametrize("raw", ['{"unfinished":', '[]'])
def test_update_preserves_corrupt_state(state, raw):
    state.data_dir.mkdir(parents=True)
    state.path.write_text(raw, encoding="utf-8")
    with pytest.raises(RuntimeError, match="Cannot parse plugin state"):
        state.update({"new": 1}, delete=["old"])
    assert state.path.read_text(encoding="utf-8") == raw


def test_delete_only_releases_quota_and_is_idempotent(state):
    state.set("expired", "x" * 200)
    state.update({}, delete=["expired", "expired", "missing"])
    assert state.snapshot() == {}
    assert state.path.read_bytes() == b"{}"
    state.update({}, delete=["expired"])
    assert state.snapshot() == {}


def test_bulk_and_single_key_writers_preserve_cross_process_updates(state):
    script = """
import sys
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
state = PluginContext(PluginManifest(name='bulk-fixture'), PluginManager()).state
prefix = sys.argv[1]
for i in range(20):
    if prefix == 'single':
        state.set(f'{prefix}_{i}', i)
    else:
        state.update({f'{prefix}_{i}': i, f'{prefix}_extra_{i}': i})
"""
    env = dict(os.environ, HERMES_HOME=str(state.data_dir.parent.parent))
    processes = [
        subprocess.Popen([sys.executable, "-c", script, prefix],
                         cwd=Path(__file__).resolve().parents[2], env=env)
        for prefix in ("a", "b", "single")
    ]
    try:
        assert [process.wait(timeout=30) for process in processes] == [0, 0, 0]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    expected = {f"single_{i}": i for i in range(20)}
    for prefix in ("a", "b"):
        expected.update({f"{prefix}_{i}": i for i in range(20)})
        expected.update({f"{prefix}_extra_{i}": i for i in range(20)})
    assert json.loads(state.path.read_text(encoding="utf-8")) == expected


@pytest.mark.parametrize("isolation", ["in_process", "host"])
def test_bulk_state_round_trips_during_real_plugin_discovery(state, monkeypatch, isolation):
    home = state.data_dir.parent.parent
    plugin = home / "plugins" / "bulk-fixture"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: bulk-fixture\nversion: 1.0\n", encoding="utf-8")
    (plugin / "__init__.py").write_text(
        "def register(ctx):\n"
        "    ctx.state.update({'first': 1, 'second': {'items': [2]}})\n"
        "    snapshot = ctx.state.snapshot()\n"
        "    assert snapshot['second']['items'] == [2]\n"
        "    ctx.state.update({'copied': snapshot['second']}, delete=['first'])\n",
        encoding="utf-8",
    )
    (home / "config.yaml").write_text(
        f"plugins:\n  enabled: [bulk-fixture]\n  isolation: {isolation}\n",
        encoding="utf-8",
    )
    bundled = home / "empty-bundled"
    bundled.mkdir()
    monkeypatch.setenv("HOME", str(home.parent / "os-home"))
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(plugins_mod, "get_bundled_plugins_dir", lambda: bundled)
    manager = PluginManager()
    try:
        manager.discover_and_load()
        assert manager._plugins["bulk-fixture"].error is None
        assert state.snapshot() == {"second": {"items": [2]}, "copied": {"items": [2]}}
    finally:
        manager.unload()
        if isolation == "host":
            manager._plugin_host().shutdown()
