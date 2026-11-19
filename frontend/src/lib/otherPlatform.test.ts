import { describe, expect, it } from 'vitest'
import {
  CONTRACTS,
  IMPORT_PROMPT,
  OTHER_COLUMNS,
  checkStatement,
  parseStatement,
  statementProblem,
} from './otherPlatform'

describe('the statement figure', () => {
  it.each([
    ['347.08', '347.08'],
    ['$1,234.5', '1234.50'],
    [' 1 234 ', '1234.00'],
    ['-125', '-125.00'],
    ['−125', '-125.00'],
    ['-$125.00', '-125.00'],
    ['$-125.00', '-125.00'],
    ['($475.00)', '-475.00'],
    ['(475)', '-475.00'],
    ['.5', '0.50'],
    ['0', '0.00'],
  ])('reads %j as %s', (text, expected) => {
    expect(parseStatement(text)).toBe(expected)
  })

  it.each(['', 'lots', '--5', '(-5)', '(5', '5)', '1.2.3', '$', '-', '1e3'])('refuses %j', (text) => {
    expect(parseStatement(text)).toBeNull()
  })

  it('says why a figure cannot be used, but not while the box is empty', () => {
    expect(statementProblem('')).toBeUndefined()
    expect(statementProblem('  ')).toBeUndefined()
    expect(statementProblem('-125')).toBeUndefined()
    expect(statementProblem('lots')).toBe('Type an amount like 1234.50, or -125 for a loss.')
  })
})

describe("the file's net P&L against the statement", () => {
  it('matches to the cent', () => {
    expect(checkStatement('347.08', '347.08')).toEqual({ state: 'match', statement: '347.08' })
    expect(checkStatement('-109.50', '(109.50)')).toEqual({ state: 'match', statement: '-109.50' })
  })

  it('differs by even a cent, and says by how much', () => {
    expect(checkStatement('347.08', '347.09')).toEqual({ state: 'differs', statement: '347.09', difference: '-0.01' })
    expect(checkStatement('347.08', '-350')).toEqual({ state: 'differs', statement: '-350.00', difference: '697.08' })
  })

  it('waits for a figure, and refuses one that is not an amount', () => {
    expect(checkStatement('347.08', '')).toEqual({ state: 'empty' })
    expect(checkStatement('347.08', 'abc')).toEqual({ state: 'invalid' })
  })
})

describe('the prompt', () => {
  it('names the version, the header row and every contract with its dollars per point', () => {
    expect(IMPORT_PROMPT).toContain("Convert it into Roundturn's import file, version 1")
    expect(IMPORT_PROMPT).toContain(`\n${OTHER_COLUMNS.join(',')}\n`)
    expect(IMPORT_PROMPT).toContain('roundturn_version: 1 on every row')
    for (const [root, value] of CONTRACTS) expect(IMPORT_PROMPT).toMatch(new RegExp(`[:,] ${root} ${value}[,.]`))
    expect(IMPORT_PROMPT).toContain('named roundturn-import.csv')
  })

  it('asks the chatbot to check the total with the trader before giving the file', () => {
    expect(IMPORT_PROMPT.indexOf('STEP 4: CHECK WITH ME')).toBeLessThan(
      IMPORT_PROMPT.indexOf('STEP 5: GIVE ME THE FILE'),
    )
    expect(IMPORT_PROMPT).toContain("compare the total net P&L with my platform's statement")
  })
})
