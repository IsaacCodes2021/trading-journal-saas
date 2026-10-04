/** The five steps of the first-run setup, in order (the design canvas's Onboarding screen). */
export const STEPS = [
  { number: 1, title: 'Confirm timezone' },
  { number: 2, title: 'Add your first account' },
  { number: 3, title: 'Export from Tradovate' },
  { number: 4, title: 'Upload your CSV' },
  { number: 5, title: 'See your dashboard' },
] as const

export type StepNumber = (typeof STEPS)[number]['number']

/** The step named in the address, or the first when it is missing or not one of the five. */
export function parseStep(value: unknown): StepNumber {
  const n = typeof value === 'string' || typeof value === 'number' ? Number(value) : NaN
  return STEPS.some((step) => step.number === n) ? (n as StepNumber) : 1
}

/** The zone this browser says it is in, if it is one the app can use (the list is the browser's own). */
export function detectedZone(): string | null {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone
    const known = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : []
    return zone && (zone === 'UTC' || known.includes(zone)) ? zone : null
  } catch {
    return null
  }
}
