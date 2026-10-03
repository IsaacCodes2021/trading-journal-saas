import { sanitize } from '../../components/SafeHtml'

/*
 * Notes travel as HTML (the server keeps what the rich-text editor makes). Until that editor is installed, notes are
 * edited as plain text, and these two functions convert. Formatting does not survive an edit here (bold becomes plain),
 * so this is for the sample data only: the editor must replace it before notes of a real journal are editable.
 */

const BULLET = /^[•*-] /

const escapeHtml = (text: string) =>
  text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;')

/** Plain text to HTML: blank lines separate paragraphs, a block of "• " lines is a list, single line breaks stay. */
export function textToHtml(text: string): string {
  return text
    .replaceAll('\r\n', '\n')
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean)
    .map((block) => {
      const lines = block.split('\n').map((line) => line.trim())
      if (lines.every((line) => BULLET.test(line))) {
        return `<ul>${lines.map((line) => `<li>${escapeHtml(line.replace(BULLET, ''))}</li>`).join('')}</ul>`
      }
      return `<p>${lines.map(escapeHtml).join('<br>')}</p>`
    })
    .join('')
}

/** HTML to the plain text the editor shows: paragraphs separated by a blank line, list items as "• item". */
export function htmlToText(html: string): string {
  const doc = new DOMParser().parseFromString(sanitize(html), 'text/html')
  doc.querySelectorAll('br').forEach((br) => br.replaceWith('\n'))
  const blocks = [...doc.body.childNodes].map((node) => {
    if (node instanceof HTMLElement && (node.tagName === 'UL' || node.tagName === 'OL')) {
      const ordered = node.tagName === 'OL'
      return [...node.children].map((item, i) => `${ordered ? `${i + 1}. ` : '• '}${item.textContent ?? ''}`).join('\n')
    }
    return (node.textContent ?? '').trim()
  })
  return blocks.filter(Boolean).join('\n\n')
}
