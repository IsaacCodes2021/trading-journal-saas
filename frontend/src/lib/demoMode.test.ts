import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { go } from './auth'
import { enterDemo, exitDemo, isDemo } from './demoMode'

let leaving: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  sessionStorage.clear()
  leaving = vi.spyOn(go, 'to').mockImplementation(() => undefined)
})

afterEach(() => {
  vi.restoreAllMocks()
  sessionStorage.clear()
})

describe('the switch', () => {
  it('is off until someone asks for the demo', () => {
    expect(isDemo()).toBe(false)
  })

  it('is on for this tab after entering, and the page starts again from the dashboard', () => {
    enterDemo()
    expect(isDemo()).toBe(true)
    expect(sessionStorage.getItem('roundturn-demo')).toBe('1')
    expect(leaving).toHaveBeenCalledExactlyOnceWith('/')
  })

  it('is off again after leaving, and the page starts again from the dashboard', () => {
    enterDemo()
    leaving.mockClear()
    exitDemo()
    expect(isDemo()).toBe(false)
    expect(sessionStorage.getItem('roundturn-demo')).toBeNull()
    expect(leaving).toHaveBeenCalledExactlyOnceWith('/')
  })

  it('is on only for the exact value it writes', () => {
    for (const other of ['0', 'true', '', ' 1', 'yes'])
      expect((sessionStorage.setItem('roundturn-demo', other), isDemo()), other).toBe(false)
  })

  it('is kept in the tab only: it is never written where another tab or a later visit would see it', () => {
    enterDemo()
    expect(localStorage.getItem('roundturn-demo')).toBeNull()
  })
})

describe('a browser that will not keep it', () => {
  const blockStorage = () =>
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError')
    })

  it('stays on the person’s own data: entering does nothing, and nothing says it worked', () => {
    blockStorage()
    enterDemo()
    expect(isDemo()).toBe(false)
    expect(leaving).not.toHaveBeenCalled()
  })

  it('does not say it worked when storage took the write but did not keep it', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => undefined) // no error, and nothing stored
    enterDemo()
    expect(isDemo()).toBe(false)
    expect(leaving).not.toHaveBeenCalled()
  })

  it('reads as off when storage cannot be read at all', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError')
    })
    expect(isDemo()).toBe(false)
  })

  it('still lets a person leave, whatever storage does', () => {
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError')
    })
    exitDemo()
    expect(leaving).toHaveBeenCalledExactlyOnceWith('/')
  })
})
