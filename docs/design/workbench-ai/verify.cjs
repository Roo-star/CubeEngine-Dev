// Local-only visual QA for the design proposals; no product integration.
const {chromium}=require(process.env.CUBEENGINE_PLAYWRIGHT || 'playwright');
const path=require('path');
const fs=require('fs');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 const page=await browser.newPage({viewport:{width:1440,height:1040},deviceScaleFactor:1});
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 const url='file:///'+path.join(__dirname,'index.html').replaceAll('\\','/');
 const report={screens:[],interactions:[],errors};
 for(const concept of ['studio','flow','lab']){
  await page.goto(url+'?concept='+concept);
  await page.waitForTimeout(250);
  await page.screenshot({path:path.join(__dirname,concept+'.png'),fullPage:true});
  report.screens.push({concept,overflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)});
  const clip=await page.evaluate(()=>{const rail=document.querySelector('.right-rail');const workspace=document.querySelector('.workspace');const dock=document.querySelector('.train-card');return dock.getBoundingClientRect().bottom>workspace.getBoundingClientRect().bottom+1;});
  if(clip)throw Error('Training dock clipped '+concept);
 }
 await page.goto(url+'?concept=studio');
 await page.locator('[data-game="mines"]').click();
 if(!await page.locator('.train-card').innerText().then(t=>t.includes('隱藏資訊')))throw Error('Minesweeper eligibility state missing');
 await page.screenshot({path:path.join(__dirname,'studio-ineligible.png'),fullPage:true});
 report.interactions.push('Minesweeper is ineligible with actionable reason');
 await page.locator('[data-game="tic"]').click();
 await page.locator('[data-settings]').click();
 await page.locator('#save-settings').click();
 await page.locator('[data-run="start"]').click();
 await page.locator('[data-run="cancel"]').click();
 if(!await page.locator('.train-card').innerText().then(t=>t.includes('續跑')))throw Error('Cancelled state cannot resume');
 await page.locator('[data-run="start"]').click();
 await page.waitForFunction(()=>document.querySelector('.train-card').innerText.includes('一輪試跑完成'),null,{timeout:12000});
 await page.locator('.train-card [data-detail="results"]').click();
 if(!await page.locator('#detail-dialog').isVisible())throw Error('Results not shown');
 report.interactions.push('Settings / start / safe cancel / resume / results');
 await page.locator('#close-detail').click();
 await page.locator('[data-mode="source"]').click();
 if(!await page.locator('.canvas .source-board').isVisible())throw Error('Source preview missing');
 await page.locator('[data-mode="target"]').click();
 await page.locator('[data-layer="1"]').click();
 report.interactions.push('Source/Target switch and layer isolation');
 for(const width of [1366,1024,768,390]){
  await page.setViewportSize({width,height:1000});
  for(const concept of ['studio','flow','lab']){
   await page.goto(url+'?concept='+concept);
   const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
   report.screens.push({width,concept,overflow});
   if(overflow)throw Error('Horizontal overflow '+concept+' '+width);
  }
 }
 fs.writeFileSync(path.join(__dirname,'visual-qa.json'),JSON.stringify(report,null,2));
 await browser.close();
 if(errors.length)throw Error(errors.join('\n'));
 console.log('Three proposals and interactive states verified');
})().catch(error=>{console.error(error);process.exit(1)});
