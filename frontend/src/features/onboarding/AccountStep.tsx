import { useState } from 'react'
import { Button, Spinner } from '../../components/ui/primitives'
import { useAccounts, useCreateAccount } from '../../lib/data/queries'
import { pluralize } from '../../lib/format'
import { AccountForm, BLANK_ACCOUNT } from '../accounts/AccountDialogs'
import { messageOf } from '../tags/messages'
import { StepFrame } from './StepFrame'

/**
 * Step 2: the account the first import goes into. With none yet, the form; with some (a person coming back to the
 * setup), the list, and a way to add another. Continue waits for at least one, because a file has to go somewhere.
 */
export function AccountStep({ onBack, onNext }: { onBack: () => void; onNext: () => void }) {
  const accounts = useAccounts()
  const create = useCreateAccount()
  const [adding, setAdding] = useState(false)
  const existing = accounts.data ?? []
  const showForm = accounts.data !== undefined && (existing.length === 0 || adding)

  return (
    <StepFrame
      number={2}
      title="Add your first account"
      intro="An account is one prop-firm account or your own: trades are kept per account, and the rules you set later (a profit target, a drawdown) belong to it."
      onBack={onBack}
      next={{ onClick: onNext, disabled: existing.length === 0 }}
    >
      {accounts.isPending ? <Spinner label="Loading your accounts" /> : null}
      {accounts.isError ? (
        <p role="alert" className="m-0 text-neg">
          {messageOf(accounts.error)}
        </p>
      ) : null}

      {existing.length > 0 ? (
        <div role="status" className="flex flex-col gap-2">
          <p className="m-0">
            You have {existing.length === 1 ? 'one account' : pluralize(existing.length, 'account')}:
          </p>
          <ul className="m-0 flex list-none flex-wrap gap-2 p-0">
            {existing.map((account) => (
              <li key={account.id} className="rounded-lg border border-line bg-bg px-3 py-1.5 text-[13px]">
                {account.name}
              </li>
            ))}
          </ul>
          {showForm ? null : (
            <div>
              <Button onClick={() => setAdding(true)}>Add another account</Button>
            </div>
          )}
        </div>
      ) : null}

      {showForm ? (
        <AccountForm
          initial={BLANK_ACCOUNT}
          requirePurchaseDate
          submitLabel="Add account"
          pending={create.isPending}
          error={create.isError ? messageOf(create.error) : null}
          onSubmit={(input) => create.mutate(input, { onSuccess: () => setAdding(false) })}
          {...(existing.length > 0 ? { onCancel: () => setAdding(false) } : {})}
        />
      ) : null}
    </StepFrame>
  )
}
