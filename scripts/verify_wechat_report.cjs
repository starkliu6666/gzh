const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require('C:/Users/stark/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');

(async()=>{
  const root=path.resolve(__dirname,'..');
  const reportPath=path.join(root,'reports/2026-10-21/金融公众号监控_2026-10-14至2026-10-20.html');
  const qa=path.join(root,'reports/2026-10-21/qa');fs.mkdirSync(qa,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const context=await browser.newContext({viewport:{width:1440,height:1100},acceptDownloads:true});
  const page=await context.newPage();const errors=[],external=[];const checks=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',req=>{if(/^https?:/.test(req.url()))external.push(req.url());});
  try{
    await page.goto(pathToFileURL(reportPath).href);
    await page.waitForSelector('#articleRows tr');
    const data=await page.locator('#report-data').textContent().then(JSON.parse);
    assert.equal(data.meta.keywords.length,15);assert.equal(data.articles.length,235);
    assert.equal(await page.locator('#articleRows tr').count(),10);
    assert.equal(await page.locator('#pageSize').inputValue(),'10');checks.push('默认每页10篇，全部235篇已嵌入');
    for(const keyword of data.meta.keywords){
      await page.locator('.keyword').filter({hasText:keyword}).evaluateAll((buttons,keyword)=>{
        const button=buttons.find(x=>x.dataset.keyword===keyword);if(!button)throw Error('missing keyword');button.click();
      },keyword);
      const expected=data.articles.filter(a=>a.keywords.includes(keyword)).length;
      assert.equal(await page.locator('#articleRows tr').count(),Math.min(expected,10));
      assert.equal(await page.locator('.keyword[aria-pressed="true"]').getAttribute('data-keyword'),keyword);
      if(expected===0){assert.equal(await page.locator('#empty').isVisible(),true);assert.equal(await page.locator('#exportCsv').isDisabled(),true);}
    }
    checks.push('全部15个关键词切换及0结果状态');
    await page.locator('#reset').click();
    const firstTitle=await page.locator('#articleRows .article-link').first().textContent();
    await page.locator('#nextPage').click();assert.notEqual(await page.locator('#articleRows .article-link').first().textContent(),firstTitle);
    await page.locator('#lastPage').click();assert.equal(await page.locator('#articleRows tr').count(),5);assert.equal(await page.locator('#nextPage').isDisabled(),true);
    await page.locator('#pageSelect').selectOption('2');assert.match(await page.locator('#pageLabel').textContent(),/第 2 \/ 24 页/);
    await page.locator('#firstPage').click();assert.equal(await page.locator('#prevPage').isDisabled(),true);checks.push('首页、上一页、下一页、末页和页码跳转');
    await page.locator('#pageSize').selectOption('20');assert.equal(await page.locator('#articleRows tr').count(),20);await page.locator('#pageSize').selectOption('10');
    for(const sort of ['reads-desc','reads-asc','score-desc','score-asc','date-desc']){
      await page.locator('#sortBy').selectOption(sort);
      const values=await page.locator('#articleRows tr').evaluateAll((rows,sort)=>rows.map(row=>sort==='date-desc'?row.querySelector('time').dateTime:sort.startsWith('reads')?Number(row.querySelector('.read-count').title.replace(/[^0-9]/g,'')):Number(row.querySelector('.score').textContent)),sort);
      for(let i=1;i<values.length;i++)assert.ok(sort.endsWith('asc')?values[i]>=values[i-1]:values[i]<=values[i-1]);
    }checks.push('阅读数、综合分和日期排序');
    await page.locator('#reset').click();
    await page.locator('#searchTitle').fill('催收');
    const expectedSearch=data.articles.filter(a=>(a.title+' '+a.author).includes('催收')).length;
    assert.equal(await page.locator('#resultsLabel strong').textContent(),expectedSearch+' 篇文章');
    await page.locator('#searchTitle').fill('');
    const author=data.articles[0].author;await page.locator('#searchAuthor').fill(author);
    const expectedAuthor=data.articles.filter(a=>a.author.includes(author)).length;
    assert.equal(await page.locator('#resultsLabel strong').textContent(),expectedAuthor+' 篇文章');
    await page.locator('#searchTitle').fill('不存在的标题_qa_78291');assert.equal(await page.locator('#empty').isVisible(),true);
    await page.locator('#reset').click();checks.push('标题与公众号搜索、组合筛选及无匹配状态');
    await page.locator('.keyword[data-keyword="度小满"]').click();
    const dScores=data.articles.filter(a=>a.keywords.includes('度小满')).map(a=>a.scoresByKeyword['度小满'].totalScore).sort((a,b)=>b-a);
    assert.equal(await page.locator('#articleRows .score').first().textContent(),dScores[0].toFixed(2));checks.push('切换关键词时使用对应关键词评分');
    const filteredDownloadPromise=page.waitForEvent('download');await page.locator('#exportCsv').click();const filteredDownload=await filteredDownloadPromise;await filteredDownload.saveAs(path.join(qa,'度小满_筛选导出.csv'));
    await page.locator('#reset').click();
    const downloadPromise=page.waitForEvent('download');await page.locator('#exportCsv').click();const download=await downloadPromise;await download.saveAs(path.join(qa,'全部_导出验证.csv'));
    const csv=fs.readFileSync(path.join(qa,'全部_导出验证.csv'),'utf8');assert.ok(csv.startsWith('\ufeff'));assert.ok(csv.includes(data.articles.at(-1).title.replaceAll('"','""')));checks.push('实际CSV下载、UTF-8 BOM及跨页全量导出');
    const links=await page.locator('#articleRows .article-link').evaluateAll(links=>links.map(a=>({href:a.href,target:a.target,rel:a.rel})));
    assert.ok(links.every(a=>a.href.startsWith('https://mp.weixin.qq.com/')&&a.target==='_blank'&&a.rel.includes('noopener')));checks.push('标题链接指向公众号原文并在新标签页打开');
    await page.screenshot({path:path.join(qa,'desktop.png'),fullPage:true});
    for(const width of [390,320]){
      await page.setViewportSize({width,height:900});
      const sizing=await page.evaluate(()=>({viewport:innerWidth,body:document.documentElement.scrollWidth}));assert.ok(sizing.body<=sizing.viewport+1,JSON.stringify(sizing));
      await page.screenshot({path:path.join(qa,'mobile-'+width+'.png'),fullPage:true});
    }checks.push('390px与320px窄屏无页面横向溢出（表格可独立滚动）');
    assert.deepEqual(external,[]);assert.deepEqual(errors,[]);checks.push('file://直接打开、零外部请求、无JavaScript运行错误');
    fs.writeFileSync(path.join(qa,'verification.json'),JSON.stringify({passed:checks,errors,externalRequests:external},null,2));
    console.log(JSON.stringify({passed:checks,errors,externalRequests:external},null,2));
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
