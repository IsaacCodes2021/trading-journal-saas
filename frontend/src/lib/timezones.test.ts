import { describe, expect, it } from 'vitest'
import { isClockTime, timeZones, zoneLabel } from './timezones'

describe('timeZones', () => {
  it('lists the browser’s zones, sorted, with UTC', () => {
    const zones = timeZones()
    expect(zones).toContain('America/New_York')
    expect(zones).toContain('America/Los_Angeles')
    expect(zones).toContain('Europe/London')
    expect(zones).toContain('UTC')
    expect([...zones].sort((a, b) => a.localeCompare(b))).toEqual(zones)
    expect(new Set(zones).size).toBe(zones.length)
  })

  it('keeps a zone that is already in use even if the browser does not list it', () => {
    expect(timeZones(['Mars/Olympus_Mons', ''])).toContain('Mars/Olympus_Mons')
    expect(timeZones(['Mars/Olympus_Mons', ''])).not.toContain('')
  })
})

describe('zoneLabel', () => {
  it('reads a zone name as words', () => {
    expect(zoneLabel('America/New_York')).toBe('America / New York')
    expect(zoneLabel('America/Argentina/Buenos_Aires')).toBe('America / Argentina / Buenos Aires')
    expect(zoneLabel('UTC')).toBe('UTC')
  })
})

describe('isClockTime', () => {
  it('accepts a zero-padded 24-hour time and nothing else', () => {
    for (const ok of ['00:00', '09:30', '18:00', '23:59']) expect(isClockTime(ok)).toBe(true)
    for (const bad of ['', '9:30', '24:00', '18:60', '18:00:00', '6 pm', '18.00']) expect(isClockTime(bad)).toBe(false)
  })
})
