/** Join class names, skipping the ones that are off: cx('a', cond && 'b'). */
export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ')
}
