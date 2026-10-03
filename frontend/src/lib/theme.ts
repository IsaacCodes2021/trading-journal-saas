export type Theme = 'dark' | 'light'

const KEY = 'tj-theme'

export function isTheme(value: unknown): value is Theme {
  return value === 'dark' || value === 'light'
}

/** The last theme this browser used: a first-paint convenience only. The server's setting is the source of truth. */
export function storedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(KEY)
    return isTheme(value) ? value : null
  } catch {
    return null // storage can be blocked (private windows, site data off); the app works without it
  }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset['theme'] = theme
  try {
    window.localStorage.setItem(KEY, theme)
  } catch {
    // see storedTheme
  }
}

export function otherTheme(theme: Theme): Theme {
  return theme === 'dark' ? 'light' : 'dark'
}
