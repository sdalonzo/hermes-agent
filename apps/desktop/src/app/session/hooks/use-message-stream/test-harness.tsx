import { QueryClient } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { useEffect, useRef } from 'react'
import { vi } from 'vitest'

import type { ClientSessionState } from '@/app/types'
import { createClientSessionState } from '@/lib/chat-runtime'
import type { RpcEvent } from '@/types/hermes'

import { useMessageStream } from './index'

export interface MessageStreamHarnessOptions extends Partial<Parameters<typeof useMessageStream>[0]> {
  /** Session-state map to mount with, for tests that seed state up front. */
  states?: Map<string, ClientSessionState>
}

export interface MessageStreamHarness {
  /** Feed a gateway event into the mounted hook. */
  handleEvent: (event: RpcEvent) => void
  /** The hook's session-state map, so callers can seed or inspect it directly. */
  states: Map<string, ClientSessionState>
  /** State for a session, blank before the hook has written any. */
  state: (sessionId?: string) => ClientSessionState
  /** Last state written for any session — for assertions about the write itself. */
  latest: () => ClientSessionState | null
}

/** Mount `useMessageStream` with inert dependencies and hand back the event sink
 *  plus the state it produces.
 *
 *  Every event test needs the same wiring — the real hook, stubbed refresh and
 *  hydrate callbacks, a session-state map it owns — and differs only in which
 *  events it sends and what it asserts. `overrides` takes any of the hook's own
 *  options for the tests that need a seeded state map, a shared query client, a
 *  named gateway profile, or a callback they can assert against. Callers still
 *  own `cleanup()`. */
export function renderMessageStream(
  sessionId: string | null,
  { states = new Map<string, ClientSessionState>(), ...overrides }: MessageStreamHarnessOptions = {}
): MessageStreamHarness {
  let dispatch: ((event: RpcEvent) => void) | null = null
  let latest: ClientSessionState | null = null

  function Harness() {
    const activeSessionIdRef = useRef<string | null>(sessionId)
    const sessionStateByRuntimeIdRef = useRef(states)
    const queryClientRef = useRef(new QueryClient())

    const stream = useMessageStream({
      activeSessionIdRef,
      hydrateFromStoredSession: vi.fn(async () => undefined),
      queryClient: queryClientRef.current,
      refreshHermesConfig: vi.fn(async () => undefined),
      refreshSessions: vi.fn(async () => undefined),
      sessionStateByRuntimeIdRef,
      updateSessionState: (id, updater) => {
        const next = updater(states.get(id) ?? createClientSessionState())
        states.set(id, next)
        latest = next

        return next
      },
      ...overrides
    })

    useEffect(() => {
      dispatch = stream.handleGatewayEvent
    }, [stream.handleGatewayEvent])

    return null
  }

  render(<Harness />)

  return {
    handleEvent: event => {
      if (!dispatch) {
        throw new Error('renderMessageStream: the hook never mounted')
      }

      dispatch(event)
    },
    states,
    state: (id = sessionId ?? '') => states.get(id) ?? createClientSessionState(),
    latest: () => latest
  }
}
