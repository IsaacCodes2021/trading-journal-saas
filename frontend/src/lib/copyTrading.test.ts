import { describe, expect, it } from 'vitest'
import { truncatedId } from './copyTrading'

describe('truncatedId', () => {
  it('keeps the first three and last three characters', () => {
    expect(truncatedId('APEX-123456-07')).toBe('APE...-07')
    expect(truncatedId('LTT1234567')).toBe('LTT...567')
  })

  it('shows a short name whole', () => {
    expect(truncatedId('Apex 07')).toBe('Apex 07')
  })
})
