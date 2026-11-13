"""The client that sent a turn (``client`` / ``form_factor`` on ``prompt.submit``) reaches plugins.

One session is reached from a desktop app, a phone and a browser tab, so which one sent a turn is a
fact about that turn, never the session: ``session.create``'s ``client`` names only the creator. It
rides ``surface_v1`` through canonical admission (and the legacy sidecar's session dict), and
``pre_llm_call`` receives it as ``turn_client``. It is a self-declared label: closed shapes only, and
core renders no prompt text from it.
"""
import threading
import types
from types import SimpleNamespace

import pytest

from hermes_state_runtime import RuntimeStoreError
from tests.gateway.test_surface_admission import _authority

PHONE = {'client': {'name': 'hermes-web', 'version': '1.2.0'}, 'form_factor': 'phone'}


@pytest.mark.asyncio
async def test_turn_client_is_admitted_and_scoped_to_its_own_turn(tmp_path, monkeypatch):
    from gateway.session_controls import AuthorityConnection
    from gateway.session_surface import surface_turn_client, surface_turn_note
    from hermes_state_runtime import list_session_admissions

    seen = []
    async def answer(event):
        seen.append((event.text, surface_turn_client(), surface_turn_note(SimpleNamespace(valid_tool_names=set()))))
        return 'ok'
    authority = await _authority(tmp_path, monkeypatch, answer)
    connection = AuthorityConnection(authority, object(), {'user_id': 'owner'})
    try:
        await connection.dispatch({'id': 1, 'method': 'session.resume', 'params': {'session_id': 's'}})
        for rid, extra in (('phone', PHONE), ('plain', {})):
            reply = await connection.dispatch({'id': 2, 'method': 'prompt.submit', 'params': {
                'session_id': 's', 'submission_id': rid, 'text': rid, **extra}})
            assert reply['result']['status'] == 'queued', reply
            await authority.sessions['s'].task
        for rid, bad in (('spaced', {'client': {'name': 'My Client'}}),
                         ('extra-key', {'client': {'name': 'x', 'build': '7'}}),
                         ('null-version', {'client': {'name': 'x', 'version': None}}),
                         ('watch', {'form_factor': 'watch'})):
            rejected = await connection.dispatch({'id': 3, 'method': 'prompt.submit', 'params': {
                'session_id': 's', 'submission_id': rid, 'text': 'x', **bad}})
            assert rejected['error']['message'] == 'invalid_params', (rid, rejected)
    finally:
        await connection.close()

    rows = {row['request_id']: row for row in list_session_admissions(authority.db, session_id='s', pending_only=False)}
    assert rows['phone']['payload']['surface_v1'] == PHONE
    assert 'surface_v1' not in rows['plain']['payload']
    assert set(rows) == {'phone', 'plain'}, 'no refused submit was admitted'
    assert seen == [
        ('phone', {'name': 'hermes-web', 'version': '1.2.0', 'form_factor': 'phone'}, ''),
        ('plain', {}, ''),
    ], 'the label never becomes a model-bound note, and the next turn does not inherit it'


def test_a_committed_turn_client_survives_the_managed_worker_boundary():
    from gateway.session_surface import admit_surface, restore_surface

    committed = admit_surface({**PHONE, 'surface': 'hud'})['surface_v1']
    assert restore_surface(committed) == committed
    for forged in ({**committed, 'client': {'name': 'Not A Label'}}, {**committed, 'form_factor': 'watch'}):
        with pytest.raises(RuntimeStoreError):
            restore_surface(forged)


def test_arming_projects_the_turn_client_and_clears_a_stale_one():
    from gateway.session_surface import admit_surface, arm_surface_turn, surface_turn_scope

    agent = SimpleNamespace(valid_tool_names=set())
    with surface_turn_scope(admit_surface({'form_factor': 'tablet'})['surface_v1']):
        arm_surface_turn(agent)
    assert agent._turn_client == {'form_factor': 'tablet'}
    with surface_turn_scope(None):
        arm_surface_turn(agent)
    assert agent._turn_client == {}, 'a reused agent must not replay the previous turn client'


def test_pre_llm_call_receives_the_turn_client(monkeypatch):
    from agent.turn_context import _collect_pre_llm_call_context

    calls = []
    monkeypatch.setattr('hermes_cli.lifecycle.invoke_hook', lambda name, **kw: calls.append((name, kw)) or [])
    for agent, expected in ((SimpleNamespace(session_id='s', model='m', _turn_client={'name': 'hermes-desktop'}),
                             {'name': 'hermes-desktop'}),
                            (SimpleNamespace(session_id='s', model='m'), {})):
        _collect_pre_llm_call_context(agent, effective_task_id='t', turn_id='u', original_user_message='hi',
                                      messages=[], conversation_history=[])
        name, kwargs = calls.pop()
        assert name == 'pre_llm_call' and kwargs['turn_client'] == expected


def test_a_narrow_legacy_hook_is_not_handed_the_new_field():
    from hermes_cli.plugins_dispatch import PluginDispatchMixin

    def legacy(session_id, model):
        return None
    assert PluginDispatchMixin._hook_callback_kwargs(
        legacy, {'session_id': 's', 'model': 'm', 'turn_client': {'name': 'x'}}) == {'session_id': 's', 'model': 'm'}


class TestLegacySidecar:
    """The legacy ``prompt.submit`` path records the turn client on the session; the turn takes it."""

    @pytest.fixture
    def busy_session(self):
        from tui_gateway import server

        session = {'agent': types.SimpleNamespace(valid_tool_names=set()), 'session_key': 'session-key',
                   'history': [], 'history_lock': threading.Lock(), 'history_version': 0, 'running': True,
                   'transport': None, 'attached_images': []}
        server._sessions['sid'] = session
        yield session
        server._sessions.pop('sid', None)

    def _submit(self, **params):
        from tui_gateway import server
        return server._methods['prompt.submit'](
            'r1', {'session_id': 'sid', 'text': 'what is this?', 'queued': True, **params})

    def test_submit_records_it_and_the_next_submit_rewrites_it(self, busy_session):
        self._submit(**PHONE)
        assert busy_session['turn_client'] == {'name': 'hermes-web', 'version': '1.2.0', 'form_factor': 'phone'}
        self._submit()
        assert busy_session['turn_client'] == {}

    def test_an_invalid_label_is_dropped_like_an_unknown_surface(self, busy_session):
        self._submit(client={'name': 'My Client'}, form_factor='phone')
        assert busy_session['turn_client'] == {}
