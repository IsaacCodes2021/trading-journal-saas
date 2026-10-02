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
