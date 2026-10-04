import { describe, expect, it } from 'vitest'
import { liveData } from './live-data'

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
