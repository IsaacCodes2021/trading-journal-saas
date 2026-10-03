/** The time zones a person can choose: every one the browser knows (IANA names), UTC, and any already in use. */
export function timeZones(inUse: readonly string[] = []): string[] {
  const known = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : []
  return [...new Set([...known, 'UTC', ...inUse.filter(Boolean)])].sort((a, b) => a.localeCompare(b))
}

/** "America/New_York" to "America / New York", for a reader; the stored value never changes. */
export function zoneLabel(zone: string): string {
  return zone.replaceAll('_', ' ').replaceAll('/', ' / ')
}

/** "HH:MM" with zero-padded hours and minutes: what the server stores for the start of a trading day. */
export function isClockTime(text: string): boolean {
  return /^(?:[01]\d|2[0-3]):[0-5]\d$/.test(text)
}
