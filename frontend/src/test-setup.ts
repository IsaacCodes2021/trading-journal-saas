import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// jsdom has no scrolling; the router asks for it on navigation.
window.scrollTo = vi.fn() as typeof window.scrollTo

afterEach(cleanup)
