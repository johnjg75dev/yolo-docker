import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  const response = await page.request.post('/api/login', { data: { password: '' } });
  expect(response.ok()).toBeTruthy();
});

test('pose edits, navigation guard, crop preview and revert', async ({ page }) => {
  expect((await page.request.put('/api/suite/datasets/sample/label/train/frame.png', { data: { text: '' } })).ok()).toBeTruthy();
  await page.goto('/datasets/edit/sample');
  await expect(page.getByText('File: frame.png')).toBeVisible();
  await page.getByRole('button', { name: 'Place Quad' }).click();
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', { name: 'Val Split' }).click();
  await expect(page.getByText('File: frame.png')).toBeVisible();
  await page.getByRole('button', { name: 'Save Changes' }).click();
  await expect(page.getByRole('button', { name: 'Saved', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Preview crop' }).click();
  await expect(page.getByText('Preview: 100 × 80 px')).toBeVisible();
  await page.getByRole('button', { name: 'Save this crop' }).click();
  await expect(page.getByRole('button', { name: 'Revert original' })).toBeEnabled();
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', { name: 'Revert original' }).click();
  await expect(page.getByText('Original restored')).toBeVisible();
});

test('curates a selection into a new dataset', async ({ page }) => {
  await page.goto('/datasets');
  await page.getByRole('row').filter({ hasText: 'sample' }).getByRole('button', { name: 'Choose images' }).click();
  await page.getByRole('checkbox', { name: 'frame.png' }).check();
  await page.getByLabel('New dataset name').fill('sample-curated');
  await page.getByRole('button', { name: /Create dataset \(1 selected\)/ }).click();
  await expect(page.getByText('sample-curated', { exact: true })).toBeVisible();
});

test('launches training with the default automatic batch', async ({ page }) => {
  let batch: number | undefined;
  await page.route('**/api/suite/train', async route => {
    batch = route.request().postDataJSON().batch;
    await route.fulfill({ json: { job: { id: 'test' }, run: 'pose-v1' } });
  });
  await page.goto('/train?dataset=sample');
  await expect(page.getByRole('combobox', { name: 'Dataset' }).locator('option[value=sample]')).toBeAttached();
  await page.getByRole('button', { name: 'Start Training Job' }).click();
  await expect.poll(() => batch).toBe(0);
});

test('browser back preserves unsaved pose edits', async ({ page }) => {
  const quad = '0 0.5 0.5 0.5 0.5 ' + '0.5 0.5 2 '.repeat(4);
  expect((await page.request.put('/api/suite/datasets/sample/label/train/frame.png', { data: { text: quad } })).ok()).toBeTruthy();
  await page.goto('/datasets');
  await page.getByRole('row').filter({ hasText: 'sample' }).getByRole('link', { name: 'Pose Studio' }).first().click();
  await expect(page.getByText('File: frame.png')).toBeVisible();
  // A keyboard move makes the loaded pose dirty.
  const handle = page.getByRole('slider', { name: /0: TL keypoint/ });
  await handle.focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('button', { name: /Save Changes/ })).toBeEnabled();
  page.once('dialog', dialog => dialog.dismiss());
  await page.goBack();
  await expect(page.getByRole('heading', { name: 'Pose Studio: sample' })).toBeVisible();
  await expect(page.getByRole('button', { name: /Save Changes/ })).toBeEnabled();
});

test('unsupported multi-object labels are read-only', async ({ page }) => {
  const row = '0 0.5 0.5 0.5 0.5 ' + '0.5 0.5 2 '.repeat(4);
  expect((await page.request.put('/api/suite/datasets/sample/label/val/frame.png', { data: { text: `${row}\n${row}` } })).ok()).toBeTruthy();
  await page.goto('/datasets/edit/sample');
  await page.getByRole('button', { name: 'Val Split' }).click();
  await expect(page.getByRole('alert')).toContainText('unsupported format');
  await expect(page.getByRole('button', { name: 'Place Quad' })).toBeDisabled();
});

test('stale crop preview is rejected without modifying the image', async ({ page }) => {
  const quad = '0 0.5 0.5 0.5 0.5 ' + '0.5 0.5 2 '.repeat(4);
  expect((await page.request.put('/api/suite/datasets/sample/label/train/frame.png', { data: { text: quad } })).ok()).toBeTruthy();
  await page.goto('/datasets/edit/sample');
  await expect(page.getByRole('button', { name: 'Preview crop' })).toBeEnabled();
  await page.getByRole('button', { name: 'Preview crop' }).click();
  expect((await page.request.put('/api/suite/datasets/sample/label/train/frame.png', { data: { text: '' } })).ok()).toBeTruthy();
  await page.getByRole('button', { name: 'Save this crop' }).click();
  await expect(page.getByText(/changed since preview/)).toBeVisible();
});

test('pose editor navigates across image pages', async ({ page }) => {
  await page.goto('/datasets/edit/long');
  await expect(page.getByText('File: 000.png')).toBeVisible();
  await page.getByLabel('Image page').selectOption({ value: '1' });
  await expect(page.getByText('File: 200.png')).toBeVisible();
  await page.getByRole('button', { name: 'Prev', exact: true }).click();
  await expect(page.getByText('File: 199.png')).toBeVisible();
});

test('same-origin images and event streams do not expose the session token in URLs', async ({ page }) => {
  const urls: string[] = [];
  page.on('request', request => { if (request.url().includes('/api/')) urls.push(request.url()); });
  await page.goto('/datasets/edit/sample');
  await expect(page.getByText('File: frame.png')).toBeVisible();
  await expect.poll(() => urls.some(url => url.includes('/api/events'))).toBeTruthy();
  expect(urls.some(url => url.includes('_tok='))).toBeFalsy();
});

test('theme preference persists after reload', async ({ page }) => {
  await page.goto('/settings');
  await page.getByRole('button', { name: /Orchid/ }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'orchid');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'orchid');
});

test('dataset label review highlights issues and opens the exact image', async ({ page }) => {
  await page.goto('/datasets');
  await page.getByRole('row').filter({ hasText: 'long' }).getByRole('button', { name: 'Check labels' }).click();
  const review = page.getByRole('region', { name: 'Label review for long' });
  await expect(review.getByRole('alert')).toContainText('findings need review');
  await expect(review.getByText('missing label').first()).toBeVisible();
  await review.getByRole('button', { name: 'Next findings' }).click();
  await expect(review.getByText(/25–48 of/)).toBeVisible();
  const link = review.getByRole('link', { name: 'Review in Pose Studio' }).first();
  const href = await link.getAttribute('href');
  const target = new URL(href!, 'http://localhost').searchParams.get('image');
  await link.click();
  await expect(page.getByText(`File: ${target}`)).toBeVisible();
});
