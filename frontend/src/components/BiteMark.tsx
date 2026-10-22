/**
 * The Roundturn mark: the bite from the "Roundturn Motion Ads" design (artboard "Logo anim 03 · Bite"), the same one the
 * marketing site shows. Colours and motion are in styles.css (`.bite-mark`), so it follows the theme.
 */
export function BiteMark({ size }: { size: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 64 64" className="bite-mark flex-none">
      <circle className="bite-ring" cx="32" cy="32" r="20" transform="rotate(-60 32 32)" />
      <polygon className="bite-head" points="39.36,10.39 30.26,22.15 26.79,2.46" />
      <circle className="bite-dot" cx="32" cy="32" r="6" />
    </svg>
  )
}
