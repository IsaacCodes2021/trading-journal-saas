import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { SafeHtml, sanitize } from './SafeHtml'

const payloads = [
  '<script>alert(1)</script><p>ok</p>',
  '<img src=x onerror=alert(1)>',
  '<a href="javascript:alert(1)">click</a>',
  '<p style="background:url(javascript:alert(1))">styled</p>',
  '<svg onload=alert(1)></svg>',
  '<iframe src="https://evil.test"></iframe>',
  '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">x</a>',
]

describe('SafeHtml', () => {
  it.each(payloads)('removes everything dangerous from %s', (payload) => {
    const clean = sanitize(payload).toLowerCase()
    for (const bad of ['<script', 'onerror', 'onload', 'javascript:', 'data:', '<iframe', '<svg', 'style=']) {
      expect(clean).not.toContain(bad)
    }
  })

  it('keeps the formatting the editor produces', () => {
    const { container } = render(<SafeHtml html="<p>a <strong>b</strong> <em>c</em></p><ul><li>d</li></ul>" />)
    expect(container.querySelector('strong')?.textContent).toBe('b')
    expect(container.querySelector('li')?.textContent).toBe('d')
  })

  it('keeps safe links', () => {
    expect(sanitize('<a href="https://example.com">x</a>')).toContain('href="https://example.com"')
  })
})
