import { Link, Outlet } from '@tanstack/react-router'
import { Card } from '../../components/ui/primitives'

const SECTIONS = [
  { to: '/settings/preferences', label: 'Preferences' },
  { to: '/settings/accounts', label: 'Prop accounts' },
  { to: '/settings/tags', label: 'Tags' },
  { to: '/settings/plan', label: 'Plan & billing' },
  { to: '/settings/privacy', label: 'Data & privacy' },
] as const

/** The frame of every settings section: the list of sections beside the one being shown. */
export function SettingsLayout() {
  return (
    <div className="flex flex-wrap items-start gap-6">
      <nav
        aria-label="Settings sections"
        className="flex max-w-full flex-none gap-1 overflow-x-auto sm:basis-[200px] sm:flex-col"
      >
        {SECTIONS.map((section) => (
          <Link
            key={section.to}
            to={section.to}
            search={(prev) => prev}
            className="block rounded-lg px-3 py-2 whitespace-nowrap text-muted no-underline hover:text-ink data-[status=active]:bg-raised data-[status=active]:text-ink"
          >
            {section.label}
          </Link>
        ))}
      </nav>
      <div className="flex min-w-0 flex-[1_1_620px] flex-col gap-4">
        <Outlet />
      </div>
    </div>
  )
}

/** A section that is designed but not built yet, inside the settings frame. */
export function SectionNextUp({ title, children }: { title: string; children: string }) {
  return (
    <Card className="p-8 text-center" role="status">
      <h1 className="m-0 text-xl font-semibold">{title}</h1>
      <p className="mt-2 mb-0 text-muted">{children}</p>
    </Card>
  )
}

export function SectionHeader({ title, children }: { title: string; children?: string }) {
  return (
    <div>
      <h1 className="m-0 text-[22px] font-semibold">{title}</h1>
      {children ? <p className="mt-0.5 mb-0 text-muted">{children}</p> : null}
    </div>
  )
}
