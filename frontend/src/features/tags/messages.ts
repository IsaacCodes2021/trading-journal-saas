/** What to tell a person when a change was refused: the server's own sentence, or a plain fallback. */
export function messageOf(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'That did not work. Nothing was changed.'
}
