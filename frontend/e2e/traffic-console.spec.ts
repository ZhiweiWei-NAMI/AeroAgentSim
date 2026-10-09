import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';

test('real traffic console: draft, compiler, operator accident, award, city photo and shared cut',async({page})=>{
 mkdirSync('/tmp/aas-q/e2/screenshots',{recursive:true});
 await page.goto('/studio?api=http://127.0.0.1:8002');
 await page.getByRole('button',{name:'Traffic accident (demo)',exact:true}).click();
 await expect(page.getByRole('tab',{name:'Entities by AeroGraph type'})).toBeVisible({timeout:60_000});
 await page.getByRole('tab',{name:'Behaviours',exact:true}).click();
 await expect(page.getByLabel('Package ID')).toHaveValue('traffic.accident');
 await page.getByRole('button',{name:'Validate',exact:true}).click();
 await expect(page.getByRole('button',{name:'Run now',exact:true})).toBeEnabled({timeout:60_000});
 await page.screenshot({path:'/tmp/aas-q/e2/screenshots/draft.png'});
 await page.getByRole('button',{name:'Run now',exact:true}).click();
 await page.waitForURL(/\/runs\/run-/,{timeout:60_000});
 const run=page.url().match(/\/runs\/(run-[a-f0-9]+)/)![1];
 await expect(page.getByTestId('dual-run-views')).toBeVisible({timeout:60_000});
 await page.getByLabel('Injection point').selectOption('accident');
 await expect(page.getByRole('button',{name:'Submit typed ingress'})).toBeEnabled({timeout:60_000});
 await page.getByRole('button',{name:'Submit typed ingress'}).click();
 await expect(page.getByText('Admission receipts · 1')).toBeVisible({timeout:30_000});
 await page.getByText('Decision / LangGraph and typed event records at this cut',{exact:true}).click();
 await expect(page.getByText('traffic.award.committed',{exact:true}).first()).toBeVisible({timeout:300_000});
 await expect.poll(async()=>{const failed=page.getByText('traffic.capture.failed',{exact:true});if(await failed.count())throw Error(`Recorded capture failure: ${await failed.first().locator('..').textContent()}`);return page.evaluate(async id=>{const r=await fetch(`/v1/runs/${id}/artifacts`);if(!r.ok)throw Error(`Artifact HTTP ${r.status}: ${await r.text()}`);const rows:unknown=await r.json();if(!Array.isArray(rows))throw Error('Artifact response must be an array');if(!rows.length){const status=await fetch('/v1/runs');if(!status.ok)throw Error(`Run status HTTP ${status.status}`);const runs=await status.json();const run=runs.find((row:{id:string})=>row.id===id);if(!run)throw Error('Run status is unavailable');if(['faulted','interrupted','stopped','completed'].includes(run.status))throw Error(`Run ${run.status} before capture: ${run.error??'no artifact recorded'}`);}return rows.length;},run);},{timeout:900_000,intervals:[5000]}).toBeGreaterThan(0);
 await page.getByRole('button',{name:'Refresh stored artifacts'}).click();
 await page.getByRole('button',{name:'Open verified PNG'}).first().click();
 await expect(page.getByAltText(/^Stored capture /)).toBeVisible({timeout:60_000});

 await page.getByRole('button',{name:'Alpha',exact:true}).click();
 await page.screenshot({path:'/tmp/aas-q/e2/screenshots/run.png'});
 const transition=page.getByRole('button',{name:/^Seek .* → .* · .* · cut /}).first();await transition.click();
 const graph=page.getByRole('region',{name:'Synchronized AeroGraph view'});const dual=page.getByTestId('dual-run-views');
 await expect(graph).toHaveAttribute('data-cut',await dual.getAttribute('data-cut') as string);
 await expect(page.getByTestId('synchronized-city')).toHaveAttribute('data-cut',await dual.getAttribute('data-cut') as string);
 await page.getByRole('button',{name:'Simulation stop',exact:true}).click();
});
