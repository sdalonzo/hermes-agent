import { isBrowserHostedDesktop } from './platform'

/** The device kinds `prompt.submit` admits as `form_factor` (`gateway.session_surface.FORM_FACTORS`). */
export type FormFactor = 'desktop' | 'phone' | 'tablet'

/** Below this many CSS pixels on the screen's short side, a touch-first device counts as a phone. */
const PHONE_SHORT_SIDE_PX = 600

/**
 * The kind of device this renderer runs on. A mouse or trackpad as the primary pointer is a desktop,
 * a touchscreen laptop included. A touch-first device is a phone below
 * {@link PHONE_SHORT_SIDE_PX} on its short side, else a tablet.
 */
export function formFactor(): FormFactor {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return 'desktop'
  }

  if (!window.matchMedia('(pointer: coarse)').matches) {
    return 'desktop'
  }

  const shortSide = Math.min(window.screen?.width ?? 0, window.screen?.height ?? 0)

  return shortSide > 0 && shortSide < PHONE_SHORT_SIDE_PX ? 'phone' : 'tablet'
}

/**
 * The per-turn `client` / `form_factor` fields of `prompt.submit`: which app and which kind of
 * device sent this turn. One session alternates between the native app and a browser tab on a phone,
 * so this rides every turn, unlike `session.create`'s `client`. Plugins read it as `pre_llm_call`
 * `turn_client`. It is a label the client chooses, never an authentication.
 */
export function turnClientFields(): { client: { name: string }; form_factor: FormFactor } {
  return {
    client: { name: isBrowserHostedDesktop() ? 'hermes-web' : 'hermes-desktop' },
    form_factor: formFactor()
  }
}
