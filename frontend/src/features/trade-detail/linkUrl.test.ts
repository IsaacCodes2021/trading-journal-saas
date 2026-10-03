import { describe, expect, it } from 'vitest'
import { linkUrl } from './linkUrl'

describe('linkUrl', () => {
  it('accepts http, https and mailto as typed', () => {
    expect(linkUrl('https://example.com/a?b=1')).toEqual({ url: 'https://example.com/a?b=1', problem: null })
    expect(linkUrl('HTTP://example.com')).toEqual({ url: 'HTTP://example.com', problem: null })
    expect(linkUrl('mailto:me@example.com')).toEqual({ url: 'mailto:me@example.com', problem: null })
  })

  it('adds https to a bare address, and trims', () => {
    expect(linkUrl('  example.com/page ')).toEqual({ url: 'https://example.com/page', problem: null })
    expect(linkUrl('localhost:3000/x')).toEqual({ url: 'https://localhost:3000/x', problem: null })
    expect(linkUrl('example.com:8080')).toEqual({ url: 'https://example.com:8080', problem: null })
  })

  it('refuses every other scheme, however it is dressed up', () => {
    for (const bad of [
      'javascript:alert(1)',
      'JaVaScRiPt:alert(1)',
      'data:text/html,<script>1</script>',
      'file:///etc/passwd',
      'vbscript:x',
    ]) {
      const result = linkUrl(bad)
      expect(result.url).toBeNull()
      expect(result.problem).toContain('http://')
    }
  })

  it('refuses an empty address and one with spaces', () => {
    expect(linkUrl('   ')).toEqual({ url: null, problem: 'Type an address.' })
    expect(linkUrl('not an address')).toEqual({ url: null, problem: 'An address cannot contain spaces.' })
  })
})
