const SCHEME = /^[a-z][a-z0-9+.-]*:/i
const ALLOWED = /^(?:https?:\/\/|mailto:)/i

/**
 * What a person typed into the link box, as a safe address: a bare "example.com" becomes https, http, https and mailto
 * pass, and anything else with a scheme (javascript:, data:, ...) is refused, with the reason in words.
 */
export function linkUrl(typed: string): { url: string; problem: null } | { url: null; problem: string } {
  const text = typed.trim()
  if (!text) return { url: null, problem: 'Type an address.' }
  if (ALLOWED.test(text)) return { url: text, problem: null }
  // "localhost:3000" and "example.com:8080" look like a scheme but are a host and a port.
  if (SCHEME.test(text) && !/^[^/\s]+:\d+(?:[/?#]|$)/.test(text)) {
    return { url: null, problem: 'Links must start with http://, https:// or mailto:.' }
  }
  if (/\s/.test(text)) return { url: null, problem: 'An address cannot contain spaces.' }
  return { url: `https://${text}`, problem: null }
}
