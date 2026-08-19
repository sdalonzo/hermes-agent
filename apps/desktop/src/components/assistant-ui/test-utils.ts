import type { ThreadMessage } from '@assistant-ui/react'

/** Fixed clock for message fixtures, so nothing sorts by "now". */
export const createdAt = new Date('2026-05-01T00:00:00.000Z')

/** Give jsdom a viewport that the thread's virtualizer treats as scrollable.
 *
 *  jsdom reports every `offsetWidth`/`offsetHeight` as 0, which makes the
 *  message list measure itself as having no room and skip the rendering paths
 *  these tests are about. The stub falls through to a real value when one
 *  exists, so a test that sets its own dimensions still wins. */
export function stubThreadViewportSize() {
  const stub = (prop: 'offsetHeight' | 'offsetWidth', clientProp: 'clientHeight' | 'clientWidth', fallback: number) => {
    const previous = Object.getOwnPropertyDescriptor(HTMLElement.prototype, prop)

    Object.defineProperty(HTMLElement.prototype, prop, {
      configurable: true,
      get() {
        return previous?.get?.call(this) || (this as HTMLElement)[clientProp] || fallback
      }
    })
  }

  stub('offsetWidth', 'clientWidth', 800)
  stub('offsetHeight', 'clientHeight', 600)
}

export function userMessage(): ThreadMessage {
  return {
    id: 'user-1',
    role: 'user',
    content: [{ type: 'text', text: 'edit me please' }],
    attachments: [],
    createdAt,
    metadata: { custom: {} }
  } as ThreadMessage
}

export function assistantMessage(): ThreadMessage {
  return {
    id: 'assistant-1',
    role: 'assistant',
    content: [{ type: 'text', text: 'done' }],
    status: { type: 'complete', reason: 'stop' },
    createdAt,
    metadata: { unstable_state: null, unstable_annotations: [], unstable_data: [], steps: [], custom: {} }
  } as ThreadMessage
}
