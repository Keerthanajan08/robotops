import { expect, test } from '@playwright/test'

test('critical workflow: failing device sequence is diagnosed, replayed and exported', async ({ page }) => {
  const email = `e2e-${Date.now()}@example.com`, password = 'e2e-password-123'

  // 1. Register, log out, log in again
  await page.goto('/login')
  await page.getByRole('button', { name: /Need an account/ }).click()
  await page.getByLabel('Email').fill(email); await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
  await page.getByRole('button', { name: 'Log out' }).click()
  await page.getByLabel('Email').fill(email); await page.getByLabel('Password').fill(password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()

  // 2. Create a project
  await page.getByRole('link', { name: 'Projects' }).click()
  await page.getByLabel('Project name').fill('E2E caregiver PoC'); await page.getByRole('button', { name: 'Create' }).click()
  await page.getByRole('link', { name: 'E2E caregiver PoC' }).click()

  // 3. Create a scenario: person_detected -> voice_prompt_issued (within 1000 ms)
  await page.getByLabel('Scenario name').fill('Prompt after detection')
  await page.getByRole('button', { name: 'Add step' }).click()
  await page.getByRole('button', { name: 'Save scenario' }).click()
  await expect(page.getByText('person_detected → voice_prompt_issued')).toBeVisible()

  // 4. Register a simulated device
  await page.getByRole('link', { name: 'Devices' }).click()
  await page.getByLabel('Device name').fill('sim-robot-e2e'); await page.getByRole('button', { name: 'Register' }).click()
  await expect(page.getByText('online (simulated)')).toBeVisible()

  // 5. Execute the scenario with a passing sequence via the background worker
  await page.getByRole('link', { name: 'Simulations' }).click()
  await page.getByRole('button', { name: 'Queue simulation' }).click()
  await expect(page.getByTestId('job-status').first()).toHaveText('completed')
  await page.getByRole('link', { name: 'View run' }).first().click()
  await expect(page.getByTestId('run-status')).toHaveText('passed')

  // 6. Trigger a deliberately failing sequence (voice prompt arrives after its 1000 ms limit)
  await page.getByRole('link', { name: 'Simulations' }).click()
  await page.getByLabel('Simulate failing sequence').check()
  await page.getByRole('button', { name: 'Queue simulation' }).click()
  await expect(page.getByRole('row', { name: /failing/ }).getByTestId('job-status')).toHaveText('completed')
  await page.getByRole('row', { name: /failing/ }).getByRole('link', { name: 'View run' }).click()

  // 7. Inspect the failed assertion
  await expect(page.getByTestId('run-status')).toHaveText('failed')
  const findings = page.locator('section', { has: page.getByRole('heading', { name: 'Findings' }) })
  await expect(findings).toContainText('timeout')
  await expect(findings).toContainText('limit 1000ms')
  const logs = page.locator('section', { has: page.getByRole('heading', { name: 'Structured logs' }) })
  await expect(logs).toContainText('ERROR')

  // 8. Replay the run: same failure, linked to the original
  await page.getByRole('button', { name: 'Replay run' }).click()
  await expect(page.getByRole('link', { name: 'earlier run' })).toBeVisible()
  await expect(page.getByTestId('run-status')).toHaveText('failed')

  // 8b. Compare the replay with the original: identical outcome
  await page.getByRole('link', { name: 'Compare with original' }).click()
  await expect(page.getByTestId('compare-outcome')).toContainText('identical')
  await expect(page.getByText('Unchanged (1)')).toBeVisible()

  // 9. Export the report from the dashboard
  await page.getByRole('link', { name: 'Dashboard' }).click()
  await expect(page.getByRole('link', { name: 'Prompt after detection' }).first()).toBeVisible()
  const [dl] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Export CSV' }).click()])
  expect(dl.suggestedFilename()).toBe('robotops-runs.csv')
  const path = await dl.path(); const csv = (await import('node:fs')).readFileSync(path!, 'utf8')
  expect(csv).toContain('failed'); expect(csv).toContain('Prompt after detection')
})
