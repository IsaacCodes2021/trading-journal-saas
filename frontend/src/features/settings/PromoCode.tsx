import { useState, type FormEvent } from 'react'
import { CONTROL, Field } from '../../components/ui/Field'
import { Button, Card } from '../../components/ui/primitives'
import { useRedeemCode } from '../../lib/grants'
import { messageOf } from '../tags/messages'

/** "Jan 12, 2027". */
export function when(iso: string): string {
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(
    new Date(iso),
  )
}

/** Use a promo code (or an older plan code). What the server says is what is shown: one sentence for every code that does
 * not work. */
export function RedeemCode() {
  const redeem = useRedeemCode()
  const [code, setCode] = useState('')
  const typed = code.trim()

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!typed || redeem.isPending) return
    redeem.mutate(typed, { onSuccess: () => setCode('') })
  }

  return (
    <Card className="flex flex-col gap-3 px-5 py-4" role="region" aria-label="Redeem a promo code">
      <h2 className="m-0 text-base font-semibold">Have a promo code?</h2>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <Field id="plan-code" label="Promo code" hint="For example FREE30. Case and spaces do not matter.">
          <input
            id="plan-code"
            type="text"
            autoComplete="off"
            autoCapitalize="characters"
            spellCheck={false}
            maxLength={64}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            className={CONTROL}
          />
        </Field>
        <div>
          <Button type="submit" variant="primary" disabled={!typed || redeem.isPending}>
            {redeem.isPending ? 'Redeeming…' : 'Redeem'}
          </Button>
        </div>
      </form>
      {redeem.isError ? (
        <p role="alert" className="m-0 text-[13px] text-neg">
          {messageOf(redeem.error)}
        </p>
      ) : null}
      {redeem.isSuccess && redeem.data.discount ? (
        <p role="status" className="m-0 text-[13px] text-pos">
          Saved: {redeem.data.discount.words}. It is applied when you subscribe.
        </p>
      ) : redeem.isSuccess ? (
        <p role="status" className="m-0 text-[13px] text-pos">
          Done. You are on the {redeem.data.effective} plan
          {redeem.data.expires_at ? ` until ${when(redeem.data.expires_at)}` : ''}.
        </p>
      ) : null}
    </Card>
  )
}

/** Said once, on the way in from sign-up, when the account was made with a discount code: it waits for the checkout. */
export function PromoSaved() {
  return (
    <div role="status" className="rounded-[10px] border border-line bg-raised px-4 py-3 text-[13px]">
      <strong>Your account is ready, and your discount code is saved.</strong> It is applied when you subscribe; until
      then, enjoy your free days.
    </div>
  )
}

/** Said once, on the way in from sign-up, when the promo code the account was made with could not be applied. */
export function PromoNotApplied() {
  return (
    <div role="status" className="rounded-[10px] border border-warn-line bg-raised px-4 py-3 text-[13px]">
      <strong>Your account is ready, but the promo code could not be applied.</strong> Its last use was taken a moment
      ago, or the free days you already have come first. If it still works when those end, enter it below.
    </div>
  )
}
