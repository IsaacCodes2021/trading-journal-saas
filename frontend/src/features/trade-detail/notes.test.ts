import { describe, expect, it } from 'vitest'
import { htmlToText, textToHtml } from './notes'

describe('textToHtml', () => {
  it('makes a paragraph of each block and keeps single line breaks', () => {
    expect(textToHtml('one\ntwo\n\nthree')).toBe('<p>one<br>two</p><p>three</p>')
  })

  it('turns a block of bullet lines into a list', () => {
    expect(textToHtml('• a\n• b')).toBe('<ul><li>a</li><li>b</li></ul>')
    expect(textToHtml('- a\n- b')).toBe('<ul><li>a</li><li>b</li></ul>')
    expect(textToHtml('• a\nnot a bullet')).toBe('<p>• a<br>not a bullet</p>')
  })

  it('escapes anything that looks like markup, so typed text can never become a tag', () => {
    expect(textToHtml('<img src=x onerror=alert(1)> & "quotes"')).toBe(
      '<p>&lt;img src=x onerror=alert(1)&gt; &amp; &quot;quotes&quot;</p>',
    )
  })

  it('gives nothing for empty or blank text, and handles Windows line endings', () => {
    expect(textToHtml('')).toBe('')
    expect(textToHtml('  \n\n  ')).toBe('')
    expect(textToHtml('a\r\n\r\nb')).toBe('<p>a</p><p>b</p>')
  })
})

describe('htmlToText', () => {
  it('reads paragraphs, breaks and lists back as plain text', () => {
    const html =
      '<p><strong>Plan:</strong> go long.</p><p>line<br>break</p><ul><li>one</li><li>two</li></ul><ol><li>x</li></ol>'
    expect(htmlToText(html)).toBe('Plan: go long.\n\nline\nbreak\n\n• one\n• two\n\n1. x')
  })

  it('drops anything the sanitizer would, so a script in saved notes never reaches the box', () => {
    expect(htmlToText('<p>safe</p><script>alert(1)</script><img src=x onerror=alert(1)>')).toBe('safe')
  })

  it('is empty for an empty note', () => {
    expect(htmlToText('')).toBe('')
    expect(htmlToText('<p></p>')).toBe('')
  })

  it('round-trips plain text', () => {
    const text = 'Plan: wait.\n\n• one\n• two\n\nlast line'
    expect(htmlToText(textToHtml(text))).toBe(text)
  })
})
