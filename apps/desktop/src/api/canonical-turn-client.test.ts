// @vitest-environment node
import { expect, test } from 'vitest'

import { HermesGateway } from './client'

// One session is reached from the native app and from a browser tab on a phone, so every turn says
// which app and which kind of device sent it (`pre_llm_call` `turn_client`). An older owner's closed
// submit params refuse the keys, so the Desktop sends them only where `client.capabilities`
// advertised `turn_client`.
async function submitThrough(capabilities: Record<string, unknown> | null, turns: Record<string, unknown>[]) {
  const wsPackage = 'ws'
  const { WebSocketServer } = await import(wsPackage)
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 })
  await new Promise<void>(resolve => server.once('listening', resolve))
  const submits: Record<string, unknown>[] = []
  let advertised = 0

  server.on('connection', (socket: any) => {
    socket.send(JSON.stringify({ jsonrpc: '2.0', method: 'event', params: { type: 'gateway.ready', payload: {} } }))
    socket.on('message', (bytes: Buffer) => {
      const frame = JSON.parse(bytes.toString())
      const reply = (result: unknown) => socket.send(JSON.stringify({ jsonrpc: '2.0', id: frame.id, result }))

      if (frame.method === 'client.capabilities') {
        if (frame.params.server_requests) { advertised++ }

        return capabilities
          ? reply({ server_requests: ['clarify'], ...capabilities })
          : socket.send(JSON.stringify({ jsonrpc: '2.0', id: frame.id, error: { code: -32601, message: 'unknown method' } }))
      }

      if (frame.method === 'session.resume') {
        return reply({ session_id: 's', revision: 1, execution_generation: 1, replay_epoch: 'e', last_sequence: 0, prompts: [] })
      }

      if (frame.method === 'prompt.submit') { submits.push(frame.params) }

      reply({ admission_id: `a${submits.length}`, ref: { session_id: 's' }, sequence: 1, status: 'queued', outcome: null,
        authority_epoch: 1, execution_generation: 1 })
    })
  })
  const client = new HermesGateway()

  try {
    await client.connect(`ws://127.0.0.1:${(server.address() as { port: number }).port}/api/ws?native_dial=fixture&ticket=t`)
    await expect.poll(() => advertised).toBe(1)
    // Let the capability answer land before the turn, as the app's boot does.
    await new Promise(resolve => setTimeout(resolve, 20))

    for (const turn of turns) {
      await client.request('prompt.submit', { session_id: 's', ...turn })
    }

    return submits
  } finally {
    client.close()

    for (const socket of server.clients) { socket.terminate() }

    await new Promise<void>(resolve => server.close(() => resolve()))
  }
}

test('every turn names its client and device kind to a backend advertising turn_client', async () => {
  const submits = await submitThrough({ turn_client: true }, [{ text: 'one' }, { text: 'two' }])
  // The node environment has no window, so the device kind is the desktop default.
  expect(submits.map(params => [params.client, params.form_factor])).toEqual([
    [{ name: 'hermes-desktop' }, 'desktop'],
    [{ name: 'hermes-desktop' }, 'desktop']
  ])
})

test('fields the caller set itself win over the defaults', async () => {
  const submits = await submitThrough({ turn_client: true }, [{ text: 'one', form_factor: 'phone' }])
  expect(submits.map(params => [params.client, params.form_factor])).toEqual([[{ name: 'hermes-desktop' }, 'phone']])
})

test('an older backend (no field, or no client.capabilities at all) never sees the keys', async () => {
  for (const capabilities of [{ canonical_clarify_batch: true }, null]) {
    const submits = await submitThrough(capabilities, [{ text: 'one' }, { text: 'two' }])
    expect(submits).toHaveLength(2)
    expect(submits.some(params => 'client' in params || 'form_factor' in params)).toBe(false)
  }
})
