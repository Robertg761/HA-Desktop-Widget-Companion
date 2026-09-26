import { expect, test } from '@playwright/test';

const HARNESS = '/tests/frontend/harness.html';

function panelLocator(page) {
  return page.locator('ha-desktop-widget-panel');
}

async function openPanel(page, query = '') {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto(`${HARNESS}${query}`);
  await expect(panelLocator(page).getByText('Office PC')).toBeVisible();
  return errors;
}

async function waitForSelectionLoaded(page) {
  await page.waitForFunction(
    () => window.__panel._draft !== null && !window.__panel._busy,
    null,
    { timeout: 30_000 }
  );
}

function savedDocuments(page) {
  return page.evaluate(() =>
    window.__db.calls.filter((call) => call.type === 'ha_desktop_widget/profiles/save')
  );
}

test('a desktop layout previews with real tiles and saves as a profile', async ({ page }) => {
  const errors = await openPanel(page);
  const panel = panelLocator(page);

  await panel.locator('[data-action="select-desktop"]').click();
  await waitForSelectionLoaded(page);
  const preview = page.frameLocator('ha-desktop-widget-panel >> iframe');
  await expect(preview.locator('.control-item[data-entity-id="light.desk"]')).toBeVisible();
  await expect(preview.locator('.control-item[data-entity-id="sensor.temperature"]')).toBeVisible();

  const frame = panel.locator('.frame');
  await expect(frame).toHaveCSS('width', '460px');

  await panel.locator('[data-field="name"]').fill('Office');
  await panel.locator('[data-role="save"]').click();
  await page.waitForFunction(() => window.__panel._selection?.type === 'profile');
  await waitForSelectionLoaded(page);

  const [saved] = await savedDocuments(page);
  expect(saved.name).toBe('Office');
  expect(saved.document.customTabs[0].entityIds).toEqual(['light.desk', 'sensor.temperature']);
  await expect(panel.locator('[data-action="select-profile"]', { hasText: 'Office' })).toBeVisible();
  expect(errors).toEqual([]);
});

test('tile edits mark the profile dirty and save as a new revision', async ({ page }) => {
  const errors = await openPanel(page);
  const panel = panelLocator(page);

  await panel.locator('[data-action="new-profile"]').click();
  await panel.locator('[data-field="create-name"]').fill('Office');
  await panel.locator('[data-field="create-source"]').selectOption('desktop-office1');
  await panel.locator('[data-action="create"]').click();
  await page.waitForFunction(() => window.__panel._selection?.type === 'profile');
  await waitForSelectionLoaded(page);

  await panel.locator('[data-action="toggle-edit"]').click();
  await panel.locator('[data-field="entity"]').fill('switch.fan');
  await panel.locator('[data-action="add-entity"]').click();
  await expect(panel.getByText('Unsaved changes')).toBeVisible();

  await panel.locator('[data-role="save"]').click();
  await expect(panel.getByText('Unsaved changes')).toBeHidden();
  const saves = await savedDocuments(page);
  expect(saves.at(-1).document.customTabs[0].entityIds).toContain('switch.fan');
  const revision = await page.evaluate(() => window.__db.profiles[window.__panel._selection.id].revision);
  expect(revision).toBe(2);
  expect(errors).toEqual([]);
});

test('renaming a partial profile keeps its document and revision', async ({ page }) => {
  const errors = await openPanel(page);
  const panel = panelLocator(page);

  // Load a full desktop layout first so a leak into the next preview would show.
  await panel.locator('[data-action="select-desktop"]').click();
  await waitForSelectionLoaded(page);
  await panel.locator('[data-action="select-profile"]', { hasText: 'Evening' }).click();
  await page.waitForFunction(() => window.__panel._selection?.id === 'evening');
  await waitForSelectionLoaded(page);

  const accent = await page
    .frameLocator('ha-desktop-widget-panel >> iframe')
    .locator('body')
    .getAttribute('data-accent');
  expect(accent).not.toBe('teal');
  await expect(panel.getByText('Unsaved changes')).toBeHidden();

  await panel.locator('[data-field="name"]').fill('Evening light');
  await panel.locator('[data-role="save"]').click();
  await page.waitForFunction(() => window.__db.profiles.evening.name === 'Evening light');
  const evening = await page.evaluate(() => window.__db.profiles.evening);
  expect(evening.document).toEqual({ ui: { theme: 'light' } });
  expect(evening.revision).toBe(2);
  expect(errors).toEqual([]);
});

test('assigning a profile and deleting one go through the admin API', async ({ page }) => {
  const errors = await openPanel(page);
  const panel = panelLocator(page);

  await panel.locator('select[data-field="assign"][data-id="desktop-office1"]').selectOption({
    label: 'Evening',
  });
  await expect(panel.getByText('Updating')).toBeVisible();
  await expect(panel.locator('select[data-field="assign"][data-id="desktop-laptop1"]')).toBeDisabled();

  page.once('dialog', (dialog) => dialog.accept());
  await panel.locator('[data-action="select-profile"]', { hasText: 'Evening' }).click();
  await waitForSelectionLoaded(page);
  await panel.locator('[data-action="delete"]').click();
  await expect(panel.getByText('Profile deleted.')).toBeVisible();
  const deleted = await page.evaluate(() => !('evening' in window.__db.profiles));
  expect(deleted).toBe(true);
  expect(errors).toEqual([]);
});

test('a rename alone counts as an unsaved change', async ({ page }) => {
  const errors = await openPanel(page);
  const panel = panelLocator(page);

  await panel.locator('[data-action="select-profile"]', { hasText: 'Evening' }).click();
  await waitForSelectionLoaded(page);
  await panel.locator('[data-field="name"]').fill('Evening light');
  await expect(panel.getByText('Unsaved changes')).toBeVisible();

  // Switching away asks first; declining keeps the typed name.
  let asked = false;
  page.once('dialog', (dialog) => {
    asked = true;
    return dialog.dismiss();
  });
  await panel.locator('[data-action="select-desktop"]').click();
  expect(asked).toBe(true);
  await expect(panel.locator('[data-field="name"]')).toHaveValue('Evening light');
  expect(errors).toEqual([]);
});

test('selection is locked while a save is in flight', async ({ page }) => {
  const errors = await openPanel(page);
  const panel = panelLocator(page);

  await panel.locator('[data-action="select-profile"]', { hasText: 'Evening' }).click();
  await waitForSelectionLoaded(page);
  await panel.locator('[data-field="name"]').fill('Evening light');
  await page.evaluate(() => {
    window.__db.delayMs = 1500;
  });
  await panel.locator('[data-role="save"]').click();
  await expect(panel.locator('[data-action="select-desktop"]')).toBeDisabled();
  await panel.locator('[data-action="select-desktop"]').click({ force: true });
  expect(await page.evaluate(() => window.__panel._selection)).toEqual({
    type: 'profile',
    id: 'evening',
  });

  await page.waitForFunction(() => !window.__panel._busy);
  await expect(panel.locator('[data-action="select-desktop"]')).toBeEnabled();
  expect(await page.evaluate(() => window.__db.profiles.evening.name)).toBe('Evening light');
  expect(errors).toEqual([]);
});

test('entities deleted from Home Assistant leave the preview', async ({ page }) => {
  const errors = await openPanel(page);
  const panel = panelLocator(page);

  await panel.locator('[data-action="select-desktop"]').click();
  await waitForSelectionLoaded(page);
  const replaced = await page.evaluate(async () => {
    const api = window.__panel.shadowRoot.querySelector('iframe').contentWindow.__hadwPreview;
    const calls = [];
    const original = api.setStates;
    api.setStates = (states) => {
      calls.push(Object.keys(states));
      return original(states);
    };
    const { 'switch.fan': _removed, ...remaining } = window.__hass.states;
    window.__panel.hass = { ...window.__hass, states: remaining };
    await new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 50)));
    return calls;
  });
  expect(replaced).toHaveLength(1);
  expect(replaced[0]).not.toContain('switch.fan');
  expect(replaced[0]).toContain('light.desk');
  expect(errors).toEqual([]);
});

test("one profile's sections never leak into the next preview", async ({ page }) => {
  const errors = await openPanel(page);
  const panel = panelLocator(page);
  const previewDocument = () =>
    page.evaluate(() =>
      window.__panel.shadowRoot.querySelector('iframe').contentWindow.__hadwPreview.getDocument()
    );

  await panel.locator('[data-action="select-profile"]', { hasText: 'Named tiles' }).click();
  await page.waitForFunction(() => window.__panel._selection?.id === 'names');
  await waitForSelectionLoaded(page);
  expect((await previewDocument()).customEntityNames).toEqual({ 'light.desk': 'Reading lamp' });

  await panel.locator('[data-action="select-profile"]', { hasText: 'Evening' }).click();
  await page.waitForFunction(() => window.__panel._selection?.id === 'evening');
  await waitForSelectionLoaded(page);
  expect((await previewDocument()).customEntityNames ?? {}).toEqual({});
  expect(errors).toEqual([]);
});

test('a rename made elsewhere is followed while the editor is clean', async ({ page }) => {
  const errors = await openPanel(page);
  const panel = panelLocator(page);

  await panel.locator('[data-action="select-profile"]', { hasText: 'Evening' }).click();
  await waitForSelectionLoaded(page);
  await page.evaluate(() => {
    window.__db.profiles.evening.name = 'Dusk';
    window.__emit();
  });
  await expect(panel.locator('[data-field="name"]')).toHaveValue('Dusk');
  await expect(panel.getByText('Unsaved changes')).toBeHidden();
  expect(errors).toEqual([]);
});

test('saving over a newer revision from elsewhere is refused', async ({ page }) => {
  const errors = await openPanel(page);
  const panel = panelLocator(page);

  await panel.locator('[data-action="select-profile"]', { hasText: 'Evening' }).click();
  await waitForSelectionLoaded(page);
  await panel.locator('[data-field="name"]').fill('Evening light');
  await page.evaluate(() => {
    const evening = window.__db.profiles.evening;
    evening.revision = 3;
    evening.document = { ui: { theme: 'dark' } };
    window.__emit();
  });
  await panel.locator('[data-role="save"]').click();
  await expect(panel.getByRole('alert')).toContainText('changed elsewhere');
  const evening = await page.evaluate(() => window.__db.profiles.evening);
  expect(evening.name).toBe('Evening');
  expect(evening.document).toEqual({ ui: { theme: 'dark' } });
  expect(errors).toEqual([]);
});

test('a failed load leaves nothing to save under the new selection', async ({ page }) => {
  const errors = await openPanel(page);
  const panel = panelLocator(page);

  await panel.locator('[data-action="select-desktop"]').click();
  await waitForSelectionLoaded(page);
  await page.evaluate(() => {
    window.__db.failNext = 'ha_desktop_widget/profiles/get';
  });
  await panel.locator('[data-action="select-profile"]', { hasText: 'Evening' }).click();
  await expect(panel.getByRole('alert')).toContainText('Simulated failure');
  await expect(panel.locator('[data-role="save"]')).toBeDisabled();
  expect(await page.evaluate(() => window.__panel._draft)).toBeNull();
  expect(errors).toEqual([]);
});

test('a preview wider than a narrow screen scrolls instead of clipping', async ({ page }) => {
  await page.setViewportSize({ width: 420, height: 900 });
  const errors = await openPanel(page, '?narrow');
  const panel = panelLocator(page);

  await panel.locator('[data-action="select-desktop"]').click();
  await waitForSelectionLoaded(page);
  const box = await panel.locator('.frame').boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(errors).toEqual([]);
});
