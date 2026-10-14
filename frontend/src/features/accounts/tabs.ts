export const TABS = ['details', 'fees', 'rules', 'firm-rules', 'payouts', 'ledger'] as const
export type Tab = (typeof TABS)[number]

export const isTab = (value: unknown): value is Tab => (TABS as readonly unknown[]).includes(value)
