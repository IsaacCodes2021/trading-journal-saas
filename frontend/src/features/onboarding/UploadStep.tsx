import { useState } from 'react'
import type { ImportRecord } from '../../lib/data/model'
import { ImportWorkspace } from '../imports/ImportWorkspace'
import { StepFrame } from './StepFrame'

/**
 * Step 4: the same import as the Import screen (choose the file, read the preview, confirm), inside the setup. There is
 * no history here and no undo; both are on the Import screen afterwards. Continue says "Skip for now" until a file has
 * been imported, so a person without their file to hand is not stuck.
 */
export function UploadStep({
  csvTimeZone,
  imported,
  onImported,
  onBack,
  onNext,
}: {
  csvTimeZone: string
  imported: ImportRecord | null
  onImported: (record: ImportRecord) => void
  onBack: () => void
  onNext: () => void
}) {
  const [notice, setNotice] = useState<string | null>(null)
  return (
    <StepFrame
      number={4}
      title="Upload your CSV"
      intro="Choose the file and look over the preview. Nothing is saved until you confirm it, and every import can be undone from the Import screen."
      onBack={onBack}
      next={{ onClick: onNext, label: imported ? 'Continue' : 'Skip for now' }}
    >
      <ImportWorkspace timeZone={csvTimeZone} notice={notice} onNotice={setNotice} onImported={onImported} />
    </StepFrame>
  )
}
