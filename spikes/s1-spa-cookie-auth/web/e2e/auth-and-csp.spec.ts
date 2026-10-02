import { expect, test, type BrowserContext, type Page } from '@playwright/test'

/** Collect Content-Security-Policy violations from the console; any one of them fails the test. */
function watchCsp(page: Page): string[] {
  const violations: string[] = []
  page.on('console', (message) => {
    if (message.text().includes('Content Security Policy')) violations.push(message.text())
  })
  page.on('pageerror', (error) => violations.push(`pageerror: ${error.message}`))
  return violations
}

async function signIn(page: Page, who: 'alice' | 'bob', next = '/') {
  await page.goto(next)
  await expect(page.getByRole('heading', { name: 'Fake identity provider' })).toBeVisible()
  await page.locator(`#signin-${who}`).click()
}

async function csrf(context: BrowserContext): Promise<string> {
  const session = await (await context.request.get('/api/v1/session')).json()
  return session.csrf_token as string
}

test('signed-out visitors are sent to sign-in and come back to where they were going', async ({ page }) => {
  await signIn(page, 'alice', '/trades?preset=7d&direction=long')
  await expect(page).toHaveURL(/\/trades\?preset=7d&direction=long$/) // `next` survived the round trip, filters intact
  await expect(page.getByTestId('who')).toHaveText('alice@example.test')
  await expect(page.getByRole('link', { name: /NQ|MNQ|ES/ }).first()).toBeVisible()
})

test('the dashboard, chart and tooltip run under the strict CSP with no violations', async ({ page }) => {
  const violations = watchCsp(page)
  await signIn(page, 'alice')
  await expect(page.getByTestId('net-pnl')).toHaveText('$485.00') // exact sum, computed on the server
  const chart = page.getByRole('img', { name: 'Daily net P&L' })
  await expect(chart).toBeVisible()
  const box = await chart.boundingBox()
  if (!box) throw new Error('the chart has no bounding box')
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.5)
  await page.mouse.move(box.x + box.width * 0.62, box.y + box.height * 0.55)
  await page.waitForTimeout(500)
  expect(violations).toEqual([])
})

test('control: the CSP watcher really does catch a violation (so the "no violations" tests mean something)', async ({ page }) => {
  const violations = watchCsp(page)
  await signIn(page, 'alice')
  await expect(page.getByTestId('net-pnl')).toBeVisible()
  expect(violations).toEqual([]) // clean before the probe
  await page.evaluate(() => document.body.insertAdjacentHTML('beforeend', '<div style="color:red">probe</div>'))
  await expect.poll(() => violations.length).toBeGreaterThan(0)
  expect(violations[0]).toContain("style-src 'self'")
})

test('notes autosave through the CSRF-protected API and survive a reload; the editor runs under the CSP', async ({ page }) => {
  const violations = watchCsp(page)
  await signIn(page, 'alice', '/trades/1')
  const note = `e2e note ${Date.now()}`
  await page.locator('.ProseMirror').click()
  await page.keyboard.type(note)
  await expect(page.getByTestId('notes-status')).toHaveText('Saved')
  await page.reload()
  await expect(page.getByText(note).first()).toBeVisible()
  expect(violations).toEqual([])
})

test('a hostile note stored through the API cannot run code or style the page', async ({ page, context }) => {
  await signIn(page, 'alice', '/trades/1')
  const payload = `<p>safe</p><img src=x onerror="document.title='PWNED'"><script>document.title='PWNED'</script><svg onload="document.title='PWNED'"></svg>`
  const stored = await context.request.put('/api/v1/trades/1/notes', {
    data: { notes: payload },
    headers: { 'X-CSRF-Token': await csrf(context), Origin: 'http://localhost:8000' },
  })
  expect(stored.status()).toBe(200)
  await page.reload()
  await expect(page.getByText('safe').first()).toBeVisible()
  expect(await page.title()).toBe('S1 spike')
  const panelHtml = await page.getByText('Saved notes (rendered through SafeHtml)').locator('xpath=following-sibling::div[1]').innerHTML()
  expect(panelHtml).not.toMatch(/<script|<img|<svg|onerror|onload/i)
})

test('CSRF: a request without the token, or from a foreign origin, is refused', async ({ page, context }) => {
  await signIn(page, 'alice')
  const url = '/api/v1/trades/1/notes'
  const body = { notes: 'x' }
  expect((await context.request.put(url, { data: body, headers: { Origin: 'http://localhost:8000' } })).status()).toBe(403)
  const token = await csrf(context)
  expect((await context.request.put(url, { data: body, headers: { 'X-CSRF-Token': token, Origin: 'https://evil.test' } })).status()).toBe(403)
})

test("another user's trade is invisible: the id answers 404, not 403", async ({ browser }) => {
  const bob = await browser.newContext()
  const page = await bob.newPage()
  await signIn(page, 'bob')
  await expect(page.getByTestId('who')).toHaveText('bob@example.test')
  expect((await bob.request.get('/api/v1/trades/1')).status()).toBe(404) // trade 1 is Alice's
  await bob.close()
})

test('sign-out revokes the session on the server', async ({ page, context }) => {
  await signIn(page, 'alice')
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('heading', { name: 'Fake identity provider' })).toBeVisible()
  expect((await context.request.get('/api/v1/session')).status()).toBe(401)
})
