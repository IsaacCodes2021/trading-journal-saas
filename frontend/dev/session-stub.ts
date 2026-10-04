import type { Plugin } from 'vite'

/*
 * DEV ONLY (`apply: 'serve'`): answers the session calls so `pnpm dev` shows the signed-in app with no backend running.
 * It never runs in `vite build`, and nothing here is bundled: the production build has no such code. Set
 * TJ_DEV_API=real in the environment to proxy to the real API on :8000 instead.
 */
const ID = '5b1d3a8e-52c4-4a8e-9a53-0d4c3f6e8a11'

export function devSessionStub(enabled: boolean): Plugin {
  const settings: Record<string, string | null> = {
    timezone: 'America/New_York',
    csv_timezone: null,
    trading_day_timezone: 'America/New_York',
    trading_day_start: '18:00',
    theme: 'dark',
  }
  const now = Date.now()
  const ago = (hours: number) => new Date(now - hours * 3_600_000).toISOString()
  const place = (id: string, device: string, current: boolean, hours: number, mfa: boolean) => ({
    id,
    current,
    device,
    signed_in_at: ago(hours + 5),
    last_active_at: ago(hours),
    expires_at: new Date(now + 85 * 86_400_000).toISOString(),
    mfa,
  })
  let places = [
    place('dev-laptop', 'Chrome on Windows', true, 0, true),
    place('dev-phone', 'Safari on iPhone', false, 3, false),
    place('dev-tablet', 'Chrome on Android', false, 52, false),
  ]
  return {
    name: 'tj-dev-session-stub',
    apply: 'serve',
    configureServer(server) {
      if (!enabled) return
      server.middlewares.use((request, response, next) => {
        const path = (request.url ?? '').split('?')[0] ?? ''
        const send = (status: number, body?: unknown) => {
          response.statusCode = status
          response.setHeader('content-type', 'application/json')
          response.end(body === undefined ? '' : JSON.stringify(body))
        }
        if (path === '/api/v1/session' && request.method === 'GET') {
          return send(200, {
            user: { id: ID, email: 'trader@example.test' },
            settings,
            plan: {
              effective: 'pro',
              source: 'grant',
              expires_at: new Date(Date.now() + 9 * 86_400_000).toISOString(),
            },
            entitlements: {},
            flags: { is_admin: false, mfa_fresh: false },
            csrf_token: 'dev-token',
            app_version: 'dev',
          })
        }
        if (path === '/api/v1/settings' && request.method === 'PATCH') {
          let raw = ''
          request.on('data', (chunk: Buffer) => (raw += chunk.toString()))
          request.on('end', () => {
            try {
              const changes = JSON.parse(raw) as Record<string, string | null>
              Object.assign(settings, changes)
              if (changes['csv_timezone'] === '') settings['csv_timezone'] = null // "" follows the own timezone
              send(200, settings)
            } catch {
              send(422, { type: 'about:blank', title: 'Unprocessable', status: 422 })
            }
          })
          return
        }
        if (path.startsWith('/api/v1/sessions')) {
          // Where the account is signed in: three sample places, so the Security section can be tried with no backend.
          if (request.method === 'DELETE') {
            const id = path.split('/')[4]
            const before = places.length
            places = places.filter((item) => item.current || (id !== undefined && item.id !== id))
            return send(200, { sessions: places, ended: before - places.length })
          }
          return send(200, { sessions: places, ended: 0 })
        }
        if (path === '/auth/logout' && request.method === 'POST') return send(204)
        next()
      })
    },
  }
}
