import Decimal from 'decimal.js'

const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })

/**
 * Display an exact decimal string. Intl.NumberFormat accepts a decimal *string*, so the value is never
 * parsed into a float on the way. The cast is the only place that fact has to be spelled out for the compiler.
 */
export function formatMoney(value: string): string {
  return usd.format(value as unknown as number)
}

/** The one place a money string becomes a float: handing a series to a chart. Never use it for arithmetic. */
export function toChartNumber(value: string): number {
  return Number(value)
}

/** Exact client-side addition for the rare case the API does not supply a total. */
export function sumMoney(values: readonly string[]): string {
  return values.reduce((total, v) => total.plus(v), new Decimal(0)).toFixed(2)
}

/**
 * What a person typed as an amount of money ("$1,234.5", "1234", " 50 ") as an exact decimal string with cents
 * ("1234.50"), or null when the box is empty. Throws an Error with a message fit to show when it is not an amount.
 * Never negative: a loss is a different field, not a minus sign.
 */
export function parseAmount(text: string): string | null {
  const cleaned = text
    .trim()
    .replaceAll(',', '')
    .replace(/^\$\s*/, '')
  if (cleaned === '') return null
  if (!/^\d+(?:\.\d{0,2})?$/.test(cleaned) && !/^\.\d{1,2}$/.test(cleaned)) {
    throw new Error('Type an amount like 2500 or 2,500.50.')
  }
  return new Decimal(cleaned).toFixed(2)
}
