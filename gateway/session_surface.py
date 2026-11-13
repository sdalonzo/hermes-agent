"""Per-turn client surface (floating HUD, live voice) carried through canonical admission.

A surface is a fact about ONE turn, never about the session: the same session alternates
between the app window, the HUD and a spoken delegation. It is committed with the admission
row and reaches the model as a note on the user message (api_content sidecar), so the
persisted user row stays the words the user typed and the frozen system prefix is untouched.

The turn's client (``client`` + ``form_factor``) is the same kind of fact: one session is reached
from a desktop app, a phone and a browser, and only the turn knows which one sent it. Unlike the
creating client of ``session.create`` (``gateway.session_client``) it is per turn. It is a
self-declared label for plugins (``pre_llm_call`` ``turn_client``), never an authentication, and
core renders no prompt text from it.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from hermes_state_runtime import RuntimeStoreError

SURFACES = frozenset({'hud', 'voice-live'})
FORM_FACTORS = frozenset({'desktop', 'tablet', 'phone'})
_VOICE_CONTEXT_LIMIT = 6000
_SUBMIT_FIELDS = ('surface', 'voice_context', 'interrupted', 'voice_turn', 'client', 'form_factor')
_surface_turn = ContextVar('surface_turn', default=None)


def submit_surface_fields(params):
    """The raw per-turn surface fields of a ``prompt.submit`` request, forwarded to admission."""
    return {key: params[key] for key in _SUBMIT_FIELDS if key in params}


def admit_turn_client(params):
    """Wire ``client`` / ``form_factor`` -> their committed form (``{}`` when absent). ``client`` has
    the ``session.create`` label shape (``{"name", "version"?}``, ``gateway.session_client``) and
    ``form_factor`` is one of :data:`FORM_FACTORS`; anything else is ``invalid_params``."""
    client, form_factor = params.get('client'), params.get('form_factor')
    if client is not None:
        from gateway.session_client import client_problem
        if client_problem(client) is not None:
            raise RuntimeStoreError('invalid_params')
    if form_factor is not None and form_factor not in FORM_FACTORS:
        raise RuntimeStoreError('invalid_params')
    committed = {}
    if client is not None:
        committed['client'] = dict(client)
    if form_factor is not None:
        committed['form_factor'] = form_factor
    return committed


def lenient_turn_client(params):
    """:func:`turn_client_view` of a submit's ``client`` / ``form_factor`` for the legacy sidecar,
    which drops an invalid label (``{}``) as it drops an unknown surface instead of refusing."""
    try:
        return turn_client_view(admit_turn_client(params))
    except RuntimeStoreError:
        return {}


def turn_client_view(committed):
    """The plugin-facing view of a committed turn client: ``{"name", "version"?, "form_factor"?}``,
    each key present only when the client sent it (``{}`` for none)."""
    committed = committed or {}
    view = dict(committed.get('client') or {})
    if committed.get('form_factor'):
        view['form_factor'] = committed['form_factor']
    return view


def admit_surface(params):
    """Wire ``surface`` / ``voice_context`` / ``interrupted`` / ``voice_turn`` / ``client`` /
    ``form_factor`` -> committed ``surface_v1`` (``{}`` when absent). ``voice_turn`` marks a spoken
    voice-mode turn: it runs on ``auxiliary.voice_chat``."""
    surface, context, interrupted, voice_turn = (
        params.get(key) for key in ('surface', 'voice_context', 'interrupted', 'voice_turn'))
    if surface is not None and surface not in SURFACES:
        raise RuntimeStoreError('invalid_params')
    # The spoken transcript only makes sense for a live-voice delegation; anywhere else it is
    # a client smuggling model input past the persisted row.
    if context is not None and (surface != 'voice-live' or not isinstance(context, str)):
        raise RuntimeStoreError('invalid_params')
    if any(flag is not None and type(flag) is not bool for flag in (interrupted, voice_turn)):
        raise RuntimeStoreError('invalid_params')
    committed = admit_turn_client(params)
    if surface:
        committed['surface'] = surface
    if context:
        committed['voice_context'] = context[:_VOICE_CONTEXT_LIMIT]
    if interrupted:
        committed['interrupted'] = True
    if voice_turn:
        committed['voice_turn'] = True
    return {'surface_v1': committed} if committed else {}


def restore_surface(committed):
    """A committed ``surface_v1`` arriving across a process boundary (managed worker), re-checked by
    the admission validator: only an object admission itself could have committed is accepted."""
    if (not isinstance(committed, dict) or not committed or set(committed) - set(_SUBMIT_FIELDS)
            or admit_surface(committed).get('surface_v1', {}) != committed):
        raise RuntimeStoreError('invalid_params')
    return committed


def _hud_note(committed, valid_tool_names):
    from agent.prompt_builder import hud_surface_note
    return hud_surface_note(valid_tool_names)


def _voice_live_note(committed, valid_tool_names):
    from tools.voice_live import voice_live_turn_note
    return voice_live_turn_note(committed.get('voice_context') or '')


_SURFACE_NOTES = {'hud': _hud_note, 'voice-live': _voice_live_note}


def surface_note(committed, valid_tool_names=None):
    """The model-bound note for a committed surface (``""`` for the plain app window): barge-in
    first, then the HUD read-the-window-below prior or the spoken-delegation contract."""
    notes = []
    if committed.get('interrupted'):
        from tools.tts_streaming import SPEECH_INTERRUPTED_NOTE
        notes.append(SPEECH_INTERRUPTED_NOTE)
    render = _SURFACE_NOTES.get(committed.get('surface'))
    if render is not None:
        notes.append(render(committed, valid_tool_names))
    return '\n\n'.join(note for note in notes if note)


@contextmanager
def surface_turn_scope(committed):
    token = _surface_turn.set(committed)
    try:
        yield
    finally:
        _surface_turn.reset(token)


def surface_voice_turn():
    """Whether the executing admission is a spoken voice turn (``auxiliary.voice_chat`` route)."""
    return bool((_surface_turn.get() or {}).get('voice_turn'))


def surface_turn_client():
    """The executing admission's self-declared client (:func:`turn_client_view`), ``{}`` for none."""
    return turn_client_view(_surface_turn.get())


def surface_turn_note(agent):
    """The executing admission's surface note, gated on the tools this agent actually has."""
    committed = _surface_turn.get()
    if not committed:
        return ''
    return surface_note(committed, getattr(agent, 'valid_tool_names', None))


def arm_surface_turn(agent, notes=(), voice_turn=False):
    """Project the executing admission's surface onto the agent's one-turn inputs, the same way in
    process and in a managed worker: the ``auxiliary.voice_chat`` marker and the must-deliver notes
    that ride the current user message (never the system prompt), and the turn's client for
    ``pre_llm_call``. Assigned unconditionally so a reused agent never replays a stale note, route
    or client."""
    agent._voice_turn_pending = bool(voice_turn) or surface_voice_turn()
    agent._turn_client = surface_turn_client()
    agent._gateway_turn_context_notes = '\n\n'.join(
        note for note in (*notes, surface_turn_note(agent)) if note)
