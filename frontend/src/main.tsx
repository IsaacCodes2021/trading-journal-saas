import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { router } from './routes'
import { applyTheme, storedTheme } from './lib/theme'
import './styles.css'

// First paint uses what this browser last used (dark by default); the account's saved choice replaces it once the
// session loads (AppShell). Done before React renders so there is no flash of the wrong theme.
applyTheme(storedTheme() ?? 'dark')

const queryClient = new QueryClient()
const root = document.getElementById('root')
if (!root) throw new Error('missing #root')

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
)
