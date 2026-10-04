import { describe, expect, it } from 'vitest'
import { devApi, liveData } from './live-data'

describe('which data the pages read', () => {
  it('is the real API in every build, whatever the development setting says', () => {
    expect(liveData('build', 'production', false)).toBe(true)
    expect(liveData('build', 'production', true)).toBe(true)
    expect(liveData('build', 'staging', false)).toBe(true)
  })

  it('is the real API in the dev server only when asked for (TJ_DEV_API=real)', () => {
    expect(liveData('serve', 'development', true)).toBe(true)
    expect(liveData('serve', 'development', false)).toBe(false)
  })

  it('is never the real API in tests', () => {
    for (const command of ['build', 'serve'] as const) {
      for (const realApi of [true, false]) expect(liveData(command, 'test', realApi)).toBe(false)
    }
  })
})

describe('what the dev server talks to', () => {
  it('is no backend at all unless asked', () => {
    for (const value of [undefined, '', 'stub', 'REAL', 'yes', 'true']) expect(devApi(value)).toBe('stub')
  })

  it('is the development-user API for "real" and the real Kinde sign-in for "signin"', () => {
    expect(devApi('real')).toBe('dev-user')
    expect(devApi('signin')).toBe('signin')
  })

  it('reads the real API in both of those, and the dev-user cookie is for the first only', () => {
    for (const value of ['real', 'signin'])
      expect(liveData('serve', 'development', devApi(value) !== 'stub')).toBe(true)
    expect(liveData('serve', 'development', devApi(undefined) !== 'stub')).toBe(false)
  })
})
