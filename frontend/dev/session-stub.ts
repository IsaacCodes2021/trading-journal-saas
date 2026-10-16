import type { Plugin } from 'vite'
import { answerRuleTemplates } from './rule-templates'

/*
 * DEV ONLY (`apply: 'serve'`): answers the session calls so `pnpm dev` shows the signed-in app with no backend running.
 * It never runs in `vite build`, and nothing here is bundled: the production build has no such code. Set
 * TJ_DEV_API=real in the environment to proxy to the real API on :8000 instead.
 */
const ID = '5b1d3a8e-52c4-4a8e-9a53-0d4c3f6e8a11'

/** What `GET /api/v1/billing` would say in each state, or null for billing switched off. */
function devBilling(state: string, now: number): Record<string, unknown> | null {
  const at = (days: number) => new Date(now + days * 86_400_000).toISOString()
  const base = {
    price: '15.00',
    currency: 'USD',
    interval: 'month',
    price_text: 'USD 15 a month, plus applicable tax',
    ends_at: null,
    renews_at: null,
    first_charge_pending: false,
    can_subscribe: false,
    billing_starts_at: null,
    can_manage: true,
    reason: null,
  }
  const states: Record<string, Record<string, unknown>> = {
    trial: { ends_at: at(2.5), can_subscribe: true, billing_starts_at: at(2.5), can_manage: false },
    covered: { ends_at: at(25.5), can_subscribe: true, billing_starts_at: at(25.5), can_manage: false },
    active: { renews_at: at(29) },
    cancelling: { ends_at: at(29) },
    past_due: { ends_at: at(5) },
    read_only: { ends_at: at(-6), can_subscribe: true, reason: 'expired', can_manage: false },
  }
  return states[state] ? { ...base, state, ...states[state] } : null
}

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
            access: {
              state: 'active',
              ends_at: new Date(Date.now() + 9 * 86_400_000).toISOString(),
              reason: null,
            },
            entitlements: {},
            flags: { is_admin: true, mfa_fresh: true, recently_authenticated: true },
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
        if (answerRuleTemplates(request, path, send)) return
        if (path.startsWith('/api/admin/v1/')) {
          // Sample accounts for the operator screens, so they can be tried with no backend. Changes are echoed, not kept.
          const day = (days: number) => new Date(now - days * 86_400_000).toISOString()
          const sample = (
            n: number,
            email: string,
            extra: Record<string, unknown> = {},
            plan: Record<string, unknown> = { effective: 'free', source: 'free', expires_at: null },
          ) => ({
            id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
            email,
            status: 'active',
            is_admin: false,
            created_at: day(40 + n),
            last_login_at: day(n),
            deletion_scheduled_at: null,
            plan,
            usage: {
              accounts: n % 5,
              trades: n * 173,
              screenshots: n,
              storage_bytes: n * 6_400_000,
              last_import_at: null,
            },
            ...extra,
          })
          const people = [
            sample(1, 'owner@example.com', { is_admin: true }),
            sample(2, 'dana.k@example.com', {}, { effective: 'pro', source: 'override', expires_at: day(-90) }),
            sample(3, 'marcus.t@example.com'),
            sample(4, 'priya.n@example.com', {}, { effective: 'pro', source: 'grant', expires_at: day(-30) }),
            sample(5, 'leo.w@example.com', { status: 'suspended' }),
            sample(6, 'sam.r@example.com', { status: 'pending_deletion', deletion_scheduled_at: day(-6) }),
          ]
          // Sample codes, events and numbers for the Codes & audit screen. Making or revoking one is echoed, not kept.
          const invites = [
            {
              id: 4,
              label: 'Beta testers, October',
              email: null,
              max_uses: 20,
              used: 7,
              expires_at: day(-14),
              revoked_at: null,
              created_at: day(9),
              grant_code_id: 2,
              grant_label: 'Beta testers, 90 days of Pro',
              status: 'works',
            },
            {
              id: 3,
              label: 'Dana K.',
              email: 'dana.k@example.com',
              max_uses: 1,
              used: 1,
              expires_at: null,
              revoked_at: null,
              created_at: day(31),
              grant_code_id: null,
              grant_label: null,
              status: 'used_up',
            },
            {
              id: 2,
              label: 'Podcast listeners',
              email: null,
              max_uses: 50,
              used: 12,
              expires_at: day(3),
              revoked_at: null,
              created_at: day(40),
              grant_code_id: null,
              grant_label: null,
              status: 'expired',
            },
            {
              id: 1,
              label: 'Sent to the wrong person',
              email: null,
              max_uses: 1,
              used: 0,
              expires_at: null,
              revoked_at: day(44),
              created_at: day(45),
              grant_code_id: null,
              grant_label: null,
              status: 'revoked',
            },
          ]
          const grantCodes = [
            {
              id: 2,
              label: 'Beta testers, 90 days of Pro',
              plan: 'pro',
              days: 90,
              max_redemptions: 20,
              used: 7,
              expires_at: day(-60),
              created_by: 'admin:x',
              created_at: day(9),
              revoked_at: null,
              status: 'works',
            },
            {
              id: 1,
              label: 'Conference giveaway',
              plan: 'pro',
              days: 30,
              max_redemptions: null,
              used: 31,
              expires_at: day(20),
              created_by: 'cli:owner',
              created_at: day(60),
              revoked_at: null,
              status: 'expired',
            },
          ]
          const person = (email: string) => ({ user_id: '00000000-0000-4000-8000-000000000002', user_email: email })
          const events = [
            {
              id: 9,
              at: day(0.1),
              actor: 'admin',
              action: 'admin_invite_created',
              actor_email: 'owner@example.com',
              user_id: null,
              user_email: null,
              meta: { invite: 4, label: 'Beta testers, October', max_uses: 20, grant: 2 },
            },
            {
              id: 8,
              at: day(0.4),
              actor: 'admin',
              action: 'admin_viewed_user',
              actor_email: 'owner@example.com',
              ...person('dana.k@example.com'),
              meta: {},
            },
            {
              id: 7,
              at: day(1),
              actor: 'user',
              action: 'login',
              actor_email: null,
              ...person('dana.k@example.com'),
              meta: { mfa: true },
            },
            {
              id: 6,
              at: day(2),
              actor: 'admin',
              action: 'admin_plan_override_set',
              actor_email: 'owner@example.com',
              ...person('dana.k@example.com'),
              meta: { plan: 'pro', reason: 'Beta tester, import bug goodwill' },
            },
            {
              id: 5,
              at: day(3),
              actor: 'user',
              action: 'grant_redeemed',
              actor_email: null,
              ...person('priya.n@example.com'),
              meta: { plan: 'pro', days: 30 },
            },
            {
              id: 4,
              at: day(5),
              actor: 'user',
              action: 'grant_refused',
              actor_email: null,
              ...person('marcus.t@example.com'),
              meta: { why: 'not_found' },
            },
            {
              id: 3,
              at: day(8),
              actor: 'admin',
              action: 'admin_suspended',
              actor_email: 'owner@example.com',
              ...person('leo.w@example.com'),
              meta: { reason: 'Chargeback dispute' },
            },
          ]
          if (path === '/api/admin/v1/metrics/overview' && request.method === 'GET') {
            return send(200, {
              accounts: 64,
              signups_7d: 18,
              active_7d: 41,
              active: 60,
              suspended: 2,
              pending_deletion: 2,
              pro: 7,
              free: 57,
              open_invites: 1,
              open_grant_codes: 1,
            })
          }
          if (path === '/api/admin/v1/invites' && request.method === 'GET') {
            return send(200, { invites, total: invites.length, limit: 25, offset: 0 })
          }
          if (path === '/api/admin/v1/grant-codes' && request.method === 'GET') {
            return send(200, { grant_codes: grantCodes, total: grantCodes.length, limit: 25, offset: 0 })
          }
          if (path === '/api/admin/v1/audit' && request.method === 'GET') {
            const query = new URL(request.url ?? '', 'http://x').searchParams
            const action = query.get('action')
            const q = (query.get('q') ?? '').toLowerCase()
            const hits = events.filter(
              (e) =>
                (!action || e.action === action) &&
                (!q || `${e.actor_email ?? ''} ${e.user_email ?? ''} ${e.action}`.toLowerCase().includes(q)),
            )
            return send(200, { events: hits, next_before: null })
          }
          if (path === '/api/admin/v1/invites' && request.method === 'POST') {
            return send(200, { code: 'INV-SAMPLE-CODE1', invite: { ...invites[0], id: 5, used: 0 } })
          }
          if (path === '/api/admin/v1/grant-codes' && request.method === 'POST') {
            return send(200, { code: 'GR-SAMPLE-CODE', grant_code: { ...grantCodes[0], id: 3, used: 0 } })
          }
          if (path.endsWith('/revoke') && path.includes('/invites/'))
            return send(200, { ...invites[0], status: 'revoked' })
          if (path.endsWith('/revoke') && path.includes('/grant-codes/')) {
            return send(200, { ...grantCodes[0], status: 'revoked' })
          }
          if (path === '/api/admin/v1/users' && request.method === 'GET') {
            const query = new URL(request.url ?? '', 'http://x').searchParams
            const q = (query.get('q') ?? '').toLowerCase()
            const status = query.get('status')
            const hits = people.filter((p) => (!q || p.email.includes(q)) && (!status || p.status === status))
            return send(200, { users: hits, total: hits.length, limit: 25, offset: 0 })
          }
          const found = people.find((p) => path.includes(p.id))
          if (!found)
            return send(404, {
              type: 'about:blank',
              title: 'Not Found',
              status: 404,
              detail: 'There is no such account.',
            })
          if (request.method === 'GET') {
            return send(200, {
              user: found,
              active_sessions: 2,
              overrides:
                found.plan.source === 'override'
                  ? [
                      {
                        id: 1,
                        plan: 'pro',
                        expires_at: day(-90),
                        reason: 'Beta tester, import bug goodwill',
                        created_by: 'admin:x',
                        created_at: day(12),
                        revoked_at: null,
                        revoked_by: null,
                        revoke_reason: null,
                      },
                    ]
                  : [],
              redemptions: [],
              events: [
                { id: 3, at: day(0.1), actor: 'admin', action: 'admin_viewed_user', by: ID, meta: {} },
                { id: 2, at: day(2), actor: 'user', action: 'login', by: null, meta: {} },
              ],
            })
          }
          return path.endsWith('/revoke-sessions') ? send(200, { ended: 2 }) : send(200, found)
        }
        if (path === '/api/v1/grants/redeem' && request.method === 'POST') {
          // Only GR-TEST-CODE works in the stub; anything else gets the one sentence the server gives.
          let raw = ''
          request.on('data', (chunk: Buffer) => (raw += chunk.toString()))
          request.on('end', () => {
            const code = (JSON.parse(raw || '{}') as { code?: string }).code ?? ''
            if (code.trim().toUpperCase() === 'GR-TEST-CODE') {
              return send(200, {
                effective: 'pro',
                source: 'grant',
                expires_at: new Date(now + 90 * 86_400_000).toISOString(),
              })
            }
            send(422, {
              type: 'about:blank',
              title: 'Unprocessable',
              status: 422,
              detail: 'That code did not work. It may be wrong, already used, expired, or revoked.',
            })
          })
          return
        }
        if (path === '/api/v1/activity' && request.method === 'GET') {
          // A few sample events, newest first, so the Recent activity card can be seen with no backend.
          const event = (hours: number, kind: string, extra: Record<string, unknown> = {}) => ({
            at: ago(hours),
            kind,
            ended: null,
            mfa: null,
            ...extra,
          })
          return send(200, {
            events: [
              event(0.2, 'sign_in', { mfa: true }),
              event(3, 'sign_in', { mfa: false }),
              event(5, 'sign_out_everywhere', { ended: 2 }),
              event(30, 'sign_in_refused'),
              event(52, 'export'),
              event(200, 'sign_up', { mfa: false }),
            ],
          })
        }
        if (path === '/api/v1/account/deletion' && request.method === 'POST') {
          // Nothing is deleted in the stub: it only answers with the date a real server would give.
          return send(200, { scheduled_for: new Date(now + 7 * 86_400_000).toISOString() })
        }
        if (path.startsWith('/__dev/billing/')) {
          // Open /__dev/billing/<state> (trial, covered, active, cancelling, past_due, read_only, or off) to see that
          // state: it sets the cookie below and goes to Plan & billing.
          const state = path.slice('/__dev/billing/'.length)
          response.statusCode = 302
          response.setHeader(
            'set-cookie',
            devBilling(state, now) ? `tj-dev-billing=${state}; path=/` : 'tj-dev-billing=; path=/; max-age=0',
          )
          response.setHeader('location', '/settings/plan')
          return response.end()
        }
        if (path === '/api/v1/billing' || path.startsWith('/api/v1/billing/')) {
          // Off (a 404, as in production today) unless the browser has a `tj-dev-billing` cookie naming a state, so each
          // Plan & billing screen can be seen with no backend (set by /__dev/billing/<state>, above).
          const state = /(?:^|;\s*)tj-dev-billing=(\w+)/.exec(request.headers.cookie ?? '')?.[1] ?? ''
          const body = devBilling(state, now)
          if (!body) return send(404, { title: 'Not Found', status: 404, detail: 'Not Found', errors: null })
          if (request.method === 'GET') return send(200, body)
          if (path.endsWith('/checkout')) return send(200, { pay_url: '/settings/plan?checkout=done' })
          if (path.endsWith('/portal')) return send(200, { url: 'https://example.com/' })
          const after = path.endsWith('/cancel') ? 'cancelling' : 'active'
          response.setHeader('set-cookie', `tj-dev-billing=${after}; path=/`)
          return send(200, devBilling(after, now))
        }
        if (path === '/auth/logout' && request.method === 'POST') return send(204)
        next()
      })
    },
  }
}
