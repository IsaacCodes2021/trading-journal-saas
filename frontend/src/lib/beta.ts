/*
 * Joining the beta: the one public form. It is a plain `fetch`, never the API client, whose 401 handling would send the
 * visitor to sign-in; the route needs no session. The server answers the same way whatever became of the address (a new
 * one, one that already has an account, one asked about a moment ago), so nothing here can tell them apart.
 */

export interface JoinForm {
  firstName: string
  email: string
  propFirm: string
  /** The hidden field no person fills in. */
  website: string
}

export type JoinProblems = Partial<Record<'firstName' | 'email' | 'propFirm', string>>

const NAME_MAX = 80
const EMAIL_MAX = 254
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

/** What is wrong with the form, field by field; an empty object when it can be sent. The server checks all of it again. */
export function joinProblems(form: JoinForm): JoinProblems {
  const problems: JoinProblems = {}
  const name = form.firstName.trim()
  const email = form.email.trim()
  const firm = form.propFirm.trim()
  if (!name) problems.firstName = 'Enter your first name.'
  else if (name.length > NAME_MAX) problems.firstName = `Use ${NAME_MAX} characters or fewer.`
  if (!EMAIL_SHAPE.test(email) || email.length > EMAIL_MAX) problems.email = 'That does not look like an email address.'
  if (!firm) problems.propFirm = 'Enter a firm, or "none" if you do not use one yet.'
  else if (firm.length > NAME_MAX) problems.propFirm = `Use ${NAME_MAX} characters or fewer.`
  return problems
}

export type JoinResult = { ok: true } | { ok: false; message: string }

/** Send the form. A failure is one plain sentence; nothing the server says is shown. */
export async function joinTheBeta(form: JoinForm): Promise<JoinResult> {
  let response: Response
  try {
    response = await fetch('/api/v1/beta/join', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({
        first_name: form.firstName.trim(),
        email: form.email.trim(),
        prop_firm: form.propFirm.trim(),
        website: form.website,
      }),
    })
  } catch {
    return { ok: false, message: 'Could not reach the server. Check your connection and try again.' }
  }
  if (response.ok) return { ok: true }
  if (response.status === 429)
    return { ok: false, message: 'Too many tries from here. Please wait a little and try again.' }
  if (response.status === 503)
    return { ok: false, message: 'Joining the beta is not open yet. Please try again later.' }
  if (response.status === 422)
    return { ok: false, message: 'Something in the form was not accepted. Please check it and try again.' }
  return { ok: false, message: 'Something went wrong. Please try again.' }
}
