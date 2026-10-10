import { expect, test, type Locator, type Page } from '@playwright/test';

async function choose(page: Page, control: Locator, label: string) {
  await control.press('Enter');
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText(label, { exact: true }).first().click();
}
async function confirmRemoval(page: Page) {
  await page.locator('.ant-popconfirm:visible').getByRole('button', { name: 'Remove', exact: true }).click();
}

test('template and traffic demo support adding, editing and removing draft entities, predicates, chains and agents', async ({ page }) => {
  const errors: string[] = [], bad: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  page.on('response', response => { if (response.status() >= 400) bad.push(`${response.status()} ${response.url()}`); });
  await page.addInitScript(() => localStorage.setItem('aas:welcome-dismissed', '1'));
  for (const entry of ['studio-template-parcel-delivery', 'open-traffic-demo']) {
    await page.goto('/?lang=en');
    await page.getByTestId(entry).click();
    await expect(page.getByLabel('Workspace name', { exact: true })).toBeVisible();
    for (const step of ['scene', 'entities', 'plugins', 'predicates', 'rules', 'agents', 'validate']) {
      await page.getByTestId(`studio-step-${step}`).click();
      await expect(page.getByTestId(`studio-panel-${step}`)).toBeVisible();
    }
    await page.getByTestId('studio-step-entities').click();
    const entities = page.getByTestId('studio-panel-entities');
    await choose(page, entities.getByRole('combobox', { name: 'Registry type picker', exact: true }), 'Customer');
    await entities.getByLabel('New entity ID', { exact: true }).fill('student-temp-entity');
    await entities.getByRole('button', { name: 'Add entity', exact: true }).click();
    await entities.getByLabel('Filter entities by id or type', { exact: true }).fill('student-temp-entity');
    await entities.locator('.guided-entities-group-label').click();
    await entities.getByRole('button', { name: 'Inspect', exact: true }).click();
    const detail = page.getByRole('dialog');
    await choose(page, detail.getByRole('combobox', { name: 'New initial field', exact: true }), 'Label');
    await detail.getByLabel('Initial field value', { exact: true }).fill('Student test site');
    await detail.getByRole('button', { name: 'Add initial field', exact: true }).click();
    await expect(detail.getByRole('textbox', { name: 'Label', exact: true })).toHaveValue('Student test site');
    await detail.getByRole('button', { name: 'Close', exact: true }).click();
    await entities.getByRole('button', { name: 'Remove', exact: true }).click(); await confirmRemoval(page);
    await expect(entities).toContainText('No entities match the current filter.');

    await page.getByTestId('studio-step-rules').click();
    const editor = page.getByTestId('studio-panel-rules').locator('.behaviour-editor');
    await page.getByRole('button', { name: 'Add behaviour package', exact: true }).click();
    await editor.getByLabel('Package ID', { exact: true }).fill('student-controls');
    await page.getByTestId('studio-step-predicates').click();
    const predicates = page.getByTestId('studio-panel-predicates');
    await choose(page, predicates.getByRole('combobox', { name: 'New predicate package', exact: true }), 'student-controls');
    await predicates.getByLabel('New predicate ID', { exact: true }).fill('student-ready');
    await predicates.getByRole('button', { name: 'Add predicate to student-controls', exact: true }).click();
    await predicates.getByLabel('Predicate expression literal', { exact: true }).selectOption('boolean');
    await predicates.getByRole('button', { name: 'Remove predicate', exact: true }).click(); await confirmRemoval(page);
    await page.getByTestId('studio-step-rules').click();
    await editor.getByLabel('New element ID', { exact: true }).fill('student-response');
    await editor.getByRole('button', { name: 'Add chain', exact: true }).click();
    await editor.getByLabel('Initial state', { exact: true }).fill('student-waiting');
    await editor.getByRole('button', { name: 'Remove chain', exact: true }).click(); await confirmRemoval(page);
    await expect(editor.getByLabel('Authored element', { exact: true })).not.toContainText('student-response');
    await page.getByRole('button', { name: 'Remove behaviour package', exact: true }).click(); await confirmRemoval(page);

    await page.getByTestId('studio-step-agents').click();
    const agents = page.getByTestId('studio-panel-agents');
    await agents.getByRole('button', { name: 'Add agent', exact: true }).click();
    let dialog = page.getByRole('dialog');
    await dialog.getByLabel('Agent engine ID', { exact: true }).fill('student-agent');
    await dialog.getByLabel('Agent configuration JSON', { exact: true }).fill('{"options":{"test":1}}');
    await dialog.getByRole('button', { name: 'Add agent', exact: true }).click();
    const row = agents.getByRole('row').filter({ hasText: 'student-agent' });
    await row.getByRole('button', { name: 'Edit agent', exact: true }).click();
    dialog = page.getByRole('dialog');
    await dialog.getByLabel('Agent configuration JSON', { exact: true }).fill('{"options":{"test":2}}');
    await dialog.getByRole('button', { name: 'Apply agent changes', exact: true }).click();
    await row.getByRole('button', { name: 'Remove agent', exact: true }).click(); await confirmRemoval(page);
    await expect(row).toHaveCount(0);
    await page.getByRole('button', { name: 'Save workspace', exact: true }).click();
    await page.getByRole('button', { name: 'Validate', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Run now', exact: true })).toBeEnabled({ timeout: 90_000 });
  }
  await page.getByTestId('workspace-picker').click();
  await page.getByRole('menuitem').first().click();
  await expect(page.getByTestId('studio-panel-scene')).toBeVisible();
  expect(errors).toEqual([]); expect(bad).toEqual([]);
});
