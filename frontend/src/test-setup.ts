import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// jsdom has no scrolling; the router asks for it on navigation.
window.scrollTo = vi.fn() as typeof window.scrollTo

// ...and no object URLs; the sample journal makes one for each image a test adds.
let blobCount = 0
URL.createObjectURL = () => `blob:test/${++blobCount}`
URL.revokeObjectURL = () => undefined

afterEach(cleanup)
