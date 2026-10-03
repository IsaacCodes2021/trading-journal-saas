/**
 * PLACEHOLDER. Hand-written to the session and settings contract in docs/design/07-frontend-and-api.md until the
 * API exists (M2). Replace it with the generated file: `pnpm gen:api` (it reads ../backend/openapi.json). From then
 * on CI regenerates it and fails on any diff, so this file must never be edited by hand again.
 */

type NoParams = { query?: never; header?: never; path?: never; cookie?: never }

export interface paths {
  '/api/v1/session': {
    parameters: NoParams
    get: {
      parameters: NoParams
      requestBody?: never
      responses: {
        200: { headers: { [name: string]: unknown }; content: { 'application/json': components['schemas']['SessionOut'] } }
      }
    }
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch?: never
    trace?: never
  }
  '/api/v1/settings': {
    parameters: NoParams
    get?: never
    put?: never
    post?: never
    delete?: never
    options?: never
    head?: never
    patch: {
      parameters: NoParams
      requestBody: { content: { 'application/json': components['schemas']['SettingsPatch'] } }
      responses: {
        200: { headers: { [name: string]: unknown }; content: { 'application/json': components['schemas']['SettingsOut'] } }
      }
    }
    trace?: never
  }
}

export type webhooks = Record<string, never>

export interface components {
  schemas: {
    SettingsOut: {
      timezone: string
      trading_day_timezone: string
      /** HH:MM */
      trading_day_start: string
      theme: 'dark' | 'light'
    }
    SettingsPatch: {
      timezone?: string
      trading_day_timezone?: string
      trading_day_start?: string
      theme?: 'dark' | 'light'
    }
    SessionOut: {
      user: { id: number; email: string; display_name: string }
      settings: components['schemas']['SettingsOut']
      plan: { effective: string; source: 'override' | 'grant' | 'subscription' | 'free'; expires_at: string | null }
      entitlements: { [key: string]: number | boolean }
      flags: { is_admin: boolean; mfa_fresh: boolean }
      csrf_token: string
      app_version: string
    }
  }
  responses: never
  parameters: never
  requestBodies: never
  headers: never
  pathItems: never
}

export type $defs = Record<string, never>
export type operations = Record<string, never>
