import {test,expect} from '@playwright/test';
import {mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';

test.use({viewport:{width:1600,height:1000},launchOptions:{executablePath:process.env.AEROAGENTSIM_CHROMIUM,args:['--use-gl=swiftshader','--enable-unsafe-swiftshader','--no-proxy-server']}});
for (const template of ['traffic-accident','city-day']) test(`${template} stores real shared-city photos at the declared cut and resolution`,async({page},info)=>{
  test.setTimeout(300_000);
  page.setDefaultTimeout(30_000);
  const errors:string[]=[];
  page.on('pageerror',e=>errors.push(String(e)));page.on('console',m=>{if(m.type()==='error'&&!/favicon|ERR_ABORTED/.test(m.text()))errors.push(m.text());});
  await page.goto('/?gallery=1');
  await page.getByTestId(`wb-template-${template}`).click();
  await page.getByTestId('wb-create-project').click();
  await expect(page.getByTestId('wb-config')).toBeVisible({timeout:60_000});
  await page.getByTestId('wb-validate').click();
  await expect(page.getByTestId('wb-save-version')).toBeEnabled({timeout:120_000});
  await page.getByTestId('wb-save-version').click();
  await expect(page.getByTestId('wb-initialize')).toBeEnabled({timeout:60_000});
  await page.getByTestId('wb-initialize').click();
  await expect(page.getByTestId('workbench')).toHaveAttribute('data-status','initialized',{timeout:90_000});
  await expect(page.locator('[data-testid=viewport][data-rendered=true]')).toBeVisible({timeout:90_000});
  await page.getByTestId('wb-start').click();
  await page.getByTestId('wb-speed').selectOption('max');
  const run=new URL(page.url()).searchParams.get('run')!;
  await expect.poll(async()=>{
    const response=await page.request.get(`/v1/runs/${run}/artifacts`);return response.ok()?(await response.json()).length:0;
  },{timeout:180_000}).toBeGreaterThan(0);
  const artifacts=await(await page.request.get(`/v1/runs/${run}/artifacts`)).json();
  const artifact=artifacts[0];
  expect(artifact.renderer_mode).toBe('browser');
  expect(artifact.byte_count).toBeGreaterThan(10_000);
  const png=await(await page.request.get(`/v1/runs/${run}/artifacts/${artifact.digest}/download`)).body();
  expect(png.readUInt32BE(16)).toBe(artifact.request.width);expect(png.readUInt32BE(20)).toBe(artifact.request.height);
  const root=join(process.env.AEROAGENTSIM_E2E_ROOT!,'screenshots');await mkdir(root,{recursive:true});
  await writeFile(join(root,`${template}-photo.png`),png);
  await writeFile(info.outputPath('capture-metadata.json'),JSON.stringify(artifact,null,2));
  await page.getByTestId('wb-dock').getByRole('button',{name:'Photos',exact:true}).click();
  await page.getByRole('button',{name:'Open photo',exact:true}).first().click();
  const photo=page.getByRole('img',{name:'Stored capture photo',exact:true});
  await expect(photo).toBeVisible();
  await expect.poll(()=>photo.evaluate((img:HTMLImageElement)=>img.naturalWidth)).toBe(artifact.request.width);
  // View the stored acquisition in the same workbench, both locales and all target sizes.
  for(const [width,height] of [[1280,800],[1600,1000],[1920,1080]]){
    await page.setViewportSize({width,height});
    await expect(page.locator('[data-testid=viewport][data-rendered=true]')).toBeVisible();
    await page.screenshot({path:join(root,`${template}-${width}-en.png`)});
  }
  await page.getByRole('button',{name:'Switch to Chinese',exact:true}).click();
  await page.screenshot({path:join(root,`${template}-1920-zh.png`)});
  expect(errors).toEqual([]);
  await page.request.post(`/v1/runs/${run}/stop`);
});
