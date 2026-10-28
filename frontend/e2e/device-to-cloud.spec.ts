import { expect, test } from '@playwright/test'

test('device-to-cloud: device key, payload conditions, key rotation, project rename', async ({ page }) => {
  page.on('dialog', d => d.accept()) // confirm() for key rotation
  const email = `d2c-${Date.now()}@example.com`, password = 'e2e-password-123'
  await page.goto('/login')
  await page.getByRole('button', { name: /Need an account/ }).click()
  await page.getByLabel('Email').fill(email)
  await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
  const token = await page.evaluate(() => localStorage.getItem('token'))
  const auth = { Authorization: `Bearer ${token}` }

  // Project create + rename
  await page.getByRole('link', { name: 'Projects' }).click()
  await page.getByLabel('Project name').fill('Reception draft')
  await page.getByRole('button', { name: 'Create' }).click()
  await page.getByRole('button', { name: 'Rename Reception draft' }).click()
  await page.getByLabel('New name for Reception draft').fill('Reception robot')
  await page.getByRole('button', { name: 'Save' }).click()
  await page.getByRole('link', { name: 'Reception robot' }).click()

  // Scenario with a payload condition: the voice prompt must be in Japanese
  await page.getByLabel('Scenario name').fill('Greet in Japanese')
  await page.getByRole('button', { name: 'Add step' }).click()
  await page.getByLabel('Step 2 payload condition').fill('{"lang": "ja"}')
  await page.getByRole('button', { name: 'Save scenario' }).click()
  await expect(page.getByText('conditions: step 2 lang="ja"')).toBeVisible()
  const pid = page.url().split('/projects/')[1]
  const [sc] = await (await page.request.get(`/api/v1/projects/${pid}/scenarios`, { headers: auth })).json()

  // Register a device: the key is shown once
  await page.getByRole('link', { name: 'Devices' }).click()
  await page.getByLabel('Device name').fill('lobby-bot')
  await page.getByRole('button', { name: 'Register' }).click()
  const key = (await page.getByTestId('device-key').textContent())!.trim()
  expect(key).toMatch(/^rok_/)
  await page.getByRole('button', { name: 'I have saved the key' }).click()
  await expect(page.getByTestId('device-key')).toHaveCount(0)

  // The device pushes events with its own key (no user token); English prompt does not satisfy the condition
  const ingest = (k: string, cid: string, lang: string) =>
    page.request.post('/api/v1/ingest/events', {
      headers: { 'X-Device-Key': k },
      data: { events: [
        { event_id: `${cid}-0`, type: 'person_detected', ts_ms: 0, correlation_id: cid },
        { event_id: `${cid}-1`, type: 'voice_prompt_issued', ts_ms: 300, correlation_id: cid, payload: { lang } },
      ] },
    })
  const first = await ingest(key, 'wrong-lang', 'en')
  expect(first.status()).toBe(200)
  expect((await first.json()).accepted).toBe(2)
  expect((await (await ingest(key, 'wrong-lang', 'en')).json()).duplicate).toBe(2) // safe resend
  await ingest(key, 'right-lang', 'ja')

  const runFor = async (cid: string) =>
    (await (await page.request.post(`/api/v1/scenarios/${sc.id}/runs`, { headers: auth, data: { correlation_id: cid } })).json()).id
  await page.goto(`/runs/${await runFor('wrong-lang')}`)
  await expect(page.getByTestId('run-status')).toHaveText('failed')
  await expect(page.locator('section', { has: page.getByRole('heading', { name: 'Findings' }) })).toContainText('missing')
  await page.goto(`/runs/${await runFor('right-lang')}`)
  await expect(page.getByTestId('run-status')).toHaveText('passed')

  // Rotate the key from the device page: old key is rejected, new one works
  await page.getByRole('link', { name: 'Devices' }).click()
  await page.getByRole('link', { name: 'lobby-bot' }).click()
  await expect(page.getByText('voice_prompt_issued').first()).toBeVisible()
  await page.getByRole('button', { name: 'Rotate key' }).click()
  const rotated = (await page.getByTestId('device-key').textContent())!.trim()
  expect(rotated).not.toBe(key)
  expect((await ingest(key, 'after-rotate', 'ja')).status()).toBe(401)
  expect((await ingest(rotated, 'after-rotate', 'ja')).status()).toBe(200)

  // Expired/invalid session is sent back to sign-in with an explanation
  await page.evaluate(() => localStorage.setItem('token', 'expired.token.value'))
  await page.getByRole('link', { name: 'Projects' }).click()
  await expect(page.getByText('Your session expired. Please sign in again.')).toBeVisible()
})
