import DOMPurify from 'dompurify'

// The allow-list from doc 04 (S6). The server sanitizes on write; this is the second layer on render.
const ALLOWED_TAGS = [
  'p',
  'br',
  'b',
  'strong',
  'i',
  'em',
  'u',
  's',
  'ul',
  'ol',
  'li',
  'blockquote',
  'h1',
  'h2',
  'h3',
  'a',
]
const ALLOWED_ATTR = ['href']

export function sanitize(html: string): string {
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOWED_URI_REGEXP: /^(?:https?|mailto):/i,
  })
}

/** The ONLY place in the app allowed to inject raw HTML. */
export function SafeHtml({ html, className }: { html: string; className?: string }) {
  // eslint-disable-next-line react/no-danger -- sanitized above; every other file is forbidden from this
  return <div className={className} dangerouslySetInnerHTML={{ __html: sanitize(html) }} />
}
