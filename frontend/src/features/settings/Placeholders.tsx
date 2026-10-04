import { SectionHeader, SectionNextUp } from './SettingsLayout'

/** Sections that are designed (the Design canvas) and have their routes planned, but are built after this one. */

export function TagsSoon() {
  return (
    <>
      <SectionHeader title="Tags">Group, rename, recolour and merge the tags you use on trades.</SectionHeader>
      <SectionNextUp title="Tags">Managing tags is the next screen to be built.</SectionNextUp>
    </>
  )
}

export function PrivacySoon() {
  return (
    <>
      <SectionHeader title="Data & privacy">Download everything you have stored, or delete your account.</SectionHeader>
      <SectionNextUp title="Data & privacy">
        Exporting your data and deleting your account arrive with the account features, before the beta opens.
      </SectionNextUp>
    </>
  )
}
