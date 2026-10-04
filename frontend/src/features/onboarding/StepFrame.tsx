import type { ReactNode } from 'react'
import { Button, Card } from '../../components/ui/primitives'
import { STEPS, type StepNumber } from './steps'

/** One step's card: where we are, what it is for, its content, and Back and Continue. */
export function StepFrame({
  number,
  title,
  intro,
  children,
  onBack,
  next,
}: {
  number: StepNumber
  title: string
  intro: string
  children: ReactNode
  onBack?: () => void
  next: { label?: string; onClick: () => void; disabled?: boolean }
}) {
  return (
    <Card className="min-w-0 flex-[2_1_560px] p-7" role="region" aria-label={title}>
      <div className="text-xs tracking-[0.05em] text-muted uppercase">
        Step {number} of {STEPS.length}
      </div>
      <h2 className="mt-1.5 mb-2 text-xl font-semibold">{title}</h2>
      <p className="m-0 mb-5 text-muted">{intro}</p>
      <div className="flex flex-col gap-5">{children}</div>
      <div className="mt-6 flex flex-wrap justify-between gap-2.5">
        <Button className="h-10" onClick={onBack} disabled={!onBack}>
          Back
        </Button>
        <Button variant="primary" className="h-10 px-4" onClick={next.onClick} disabled={next.disabled}>
          {next.label ?? 'Continue'}
        </Button>
      </div>
    </Card>
  )
}
