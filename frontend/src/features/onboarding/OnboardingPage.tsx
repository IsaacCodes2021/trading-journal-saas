import { useState } from 'react'
import { Card, Spinner } from '../../components/ui/primitives'
import { cx } from '../../components/ui/cx'
import type { ImportRecord } from '../../lib/data/model'
import { useSession } from '../../lib/session'
import { AccountStep } from './AccountStep'
import { DoneStep } from './DoneStep'
import { ExportStep } from './ExportStep'
import { STEPS, type StepNumber } from './steps'
import { TimezoneStep } from './TimezoneStep'
import { UploadStep } from './UploadStep'

/**
 * The first-run setup (the design canvas's Onboarding screen): five short steps from "which timezone" to the dashboard.
 * The step is in the address, so Back and reload work and a step can be linked to. Everything it does is also in Settings
 * and on the Import screen; it only puts the first-time path in one place.
 */
export function OnboardingPage({ step, onStep }: { step: StepNumber; onStep: (step: StepNumber) => void }) {
  const session = useSession()
  const [imported, setImported] = useState<ImportRecord | null>(null)
  if (session.isPending) return <Spinner label="Loading" />
  if (!session.data) return null
  const settings = session.data.settings
  const go = (n: number) => onStep(Math.min(5, Math.max(1, n)) as StepNumber)

  return (
    <>
      <div>
        <h1 className="m-0 text-[26px] font-semibold">Welcome. Let&rsquo;s get your first trades in.</h1>
        <p className="mt-1.5 mb-0 text-muted">Five short steps. You can change any of this later in Settings.</p>
      </div>

      <div className="flex flex-wrap items-start gap-6">
        <Card className="flex-[1_1_260px] px-5 py-3">
          <nav aria-label="Setup steps">
            <ol className="m-0 flex list-none flex-col p-0">
              {STEPS.map((item) => {
                const current = item.number === step
                const done = item.number < step
                return (
                  <li key={item.number}>
                    <button
                      type="button"
                      onClick={() => go(item.number)}
                      aria-current={current ? 'step' : undefined}
                      className={cx(
                        'flex w-full cursor-pointer items-center gap-3 border-0 bg-transparent py-2.5 text-left font-[inherit]',
                        current ? 'font-semibold text-ink' : 'text-muted hover:text-ink',
                      )}
                    >
                      <span
                        aria-hidden="true"
                        className={cx(
                          'inline-flex size-7 flex-none items-center justify-center rounded-full border text-[13px]',
                          current
                            ? 'border-accent bg-accent font-semibold text-accent-on'
                            : done
                              ? 'border-accent text-accent'
                              : 'border-line-strong',
                        )}
                      >
                        {done ? '✓' : item.number}
                      </span>
                      <span>
                        {item.title}
                        {done ? (
                          <>
                            {' '}
                            <span className="sr-only">(done)</span>
                          </>
                        ) : null}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ol>
          </nav>
        </Card>

        {step === 1 ? <TimezoneStep settings={settings} onNext={() => go(2)} /> : null}
        {step === 2 ? <AccountStep onBack={() => go(1)} onNext={() => go(3)} /> : null}
        {step === 3 ? <ExportStep onBack={() => go(2)} onNext={() => go(4)} /> : null}
        {step === 4 ? (
          <UploadStep
            csvTimeZone={settings.csv_timezone ?? settings.timezone}
            imported={imported}
            onImported={setImported}
            onBack={() => go(3)}
            onNext={() => go(5)}
          />
        ) : null}
        {step === 5 ? <DoneStep imported={imported} onBack={() => go(4)} /> : null}
      </div>
    </>
  )
}
