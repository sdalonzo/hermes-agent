// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'

import { formFactor, turnClientFields } from './turn-client'

function pointer(coarse: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({ matches: coarse && query === '(pointer: coarse)', media: query }))
}

function screenSize(width: number, height: number) {
  vi.spyOn(window.screen, 'width', 'get').mockReturnValue(width)
  vi.spyOn(window.screen, 'height', 'get').mockReturnValue(height)
}

describe('formFactor', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    document.documentElement.removeAttribute('data-hermes-desktop-host')
  })

  it('is a desktop when a mouse or trackpad is the primary pointer, even on a small screen', () => {
    pointer(false)
    screenSize(390, 844)
    expect(formFactor()).toBe('desktop')
  })

  it('is a phone for a touch-first device under 600px on its short side, in either orientation', () => {
    pointer(true)
    screenSize(390, 844)
    expect(formFactor()).toBe('phone')
    screenSize(844, 390)
    expect(formFactor()).toBe('phone')
  })

  it('is a tablet for a touch-first device at or above 600px on its short side', () => {
    pointer(true)
    screenSize(820, 1180)
    expect(formFactor()).toBe('tablet')
  })

  it('names the browser-hosted renderer apart from the native app', () => {
    pointer(true)
    screenSize(390, 844)
    expect(turnClientFields()).toEqual({ client: { name: 'hermes-desktop' }, form_factor: 'phone' })
    document.documentElement.dataset.hermesDesktopHost = 'browser'
    expect(turnClientFields()).toEqual({ client: { name: 'hermes-web' }, form_factor: 'phone' })
  })
})
