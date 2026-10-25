/**
 * PicturePick 浏览器端全流程验收（Playwright + Chromium，第 2 版）
 *
 * 覆盖：
 *   浏览器原生目录选择（<input webkitdirectory>，Playwright 目录上传）
 *   → 选片（上一张/下一张/缩放/自动放映/pick/统计，图片全部来自 blob URL）
 *   → 结束本轮 → 进入下一轮 → 再次 pick → 生成结果
 *   → 一键导出最后一轮保留照片到指定文件夹（mock 目录句柄，校验写入文件）
 *   → 下载 JSON 并校验（origin=browser、lastRound 等）
 *   → 再选一组照片
 *
 * 前置：
 *   1) 后端已启动并托管已构建的前端：
 *        cd backend && python -m picturepick serve --port 8125 --no-open
 *   2) 已安装 Playwright 与 Chromium（npm i -D playwright && npx playwright install chromium），
 *      或通过环境变量指定 playwright 模块与浏览器路径。
 *
 * 运行示例：
 *   $env:PP_BASE_URL='http://127.0.0.1:8125'
 *   $env:PP_PHOTOS_DIR='E:\path\to\photos'
 *   $env:PP_CHROMIUM='C:\Users\me\AppData\Local\ms-playwright\chromium-1148\chrome-win\chrome.exe'
 *   node scripts/ui_e2e.cjs
 * 退出码：0 = 全部通过；1 = 有失败；2 = 脚本异常。
 */
const path = require('path');
const fs = require('fs');

const BASE = process.env.PP_BASE_URL || 'http://127.0.0.1:8125';
const PHOTOS = process.env.PP_PHOTOS_DIR || path.join(__dirname, '..', 'samples', 'photos');
const OUT = process.env.PP_OUT_DIR || path.join(__dirname, '..', '.tmp', 'verify');
const CHROME = process.env.PP_CHROMIUM || '';

let playwright;
try {
  playwright = require('playwright');
} catch {
  const fallback = process.env.PP_PLAYWRIGHT_DIR;
  if (!fallback) {
    console.error('未找到 playwright 模块：请先安装（npm i -D playwright），或用 PP_PLAYWRIGHT_DIR 指向其 node_modules/playwright 目录。');
    process.exit(2);
  }
  playwright = require(path.join(fallback, 'node_modules', 'playwright'));
}
const { chromium } = playwright;

const EXPECTED_TOTAL = 60;

let pass = 0;
let fail = 0;
const failures = [];
function check(name, cond, detail = '') {
  if (cond) { pass++; console.log(`  PASS  ${name}`); }
  else { fail++; failures.push(name + (detail ? ` :: ${detail}` : '')); console.log(`  FAIL  ${name}  ${detail}`); }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch({
    ...(CHROME ? { executablePath: CHROME } : {}),
    headless: true,
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true, locale: 'zh-CN' });
  const page = await context.newPage();
  const pageErrors = [];
  const imageRequests = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));
  page.on('request', (req) => {
    if (req.url().includes('/photos/') && req.url().includes('/image')) imageRequests.push(req.url());
  });
  page.on('console', (msg) => { if (msg.type() === 'error') console.log('  [console.error]', msg.text()); });

  const ui = (id) => `[data-testid="${id}"]`;
  const text = (id) => page.textContent(ui(id));
  const shot = (name) => page.screenshot({ path: path.join(OUT, name), fullPage: false });

  console.log('\n[1] 启动页：浏览器原生目录选择');
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector(ui('pick-folder-button'), { timeout: 20000 });
  check('启动页出现「选择照片文件夹」按钮', await page.isVisible(ui('pick-folder-button')));
  check('不存在自定义目录浏览弹窗', (await page.locator(ui('folder-browser')).count()) === 0);
  check('不存在路径输入框', (await page.locator(ui('path-input')).count()) === 0);
  await page.setInputFiles(ui('folder-input'), PHOTOS);
  await shot('01-start.png');

  console.log('\n[2] 选片视图、统计与本地图片');
  await page.waitForSelector(ui('picker-view'), { timeout: 30000 });
  await page.waitForSelector(`${ui('viewer-image')}[data-loaded="true"]`, { timeout: 30000 });
  check('选片视图出现', await page.isVisible(ui('picker-view')));
  check(`总数为 ${EXPECTED_TOTAL}`, (await text('stats-total')).trim() === String(EXPECTED_TOTAL), await text('stats-total'));
  check('当前序号为 1/60', (await text('stats-index')).trim() === `1/${EXPECTED_TOTAL}`, await text('stats-index'));
  check('初始 pick 数为 0', (await text('stats-picked')).trim() === '0', await text('stats-picked'));
  const viewed1 = parseInt((await text('stats-viewed')).split('/')[0], 10);
  check('浏览计数已上报', viewed1 >= 1, await text('stats-viewed'));
  const viewerSrc = await page.getAttribute(ui('viewer-image'), 'src');
  check('大图使用浏览器本地 blob URL（不上传）', Boolean(viewerSrc && viewerSrc.startsWith('blob:')), String(viewerSrc).slice(0, 60));
  await shot('02-picker.png');

  console.log('\n[3] 上一张/下一张');
  await page.click(ui('next-button'));
  await page.waitForFunction((sel) => document.querySelector(sel)?.textContent.trim() === '2/60', ui('stats-index'), { timeout: 8000 });
  check('下一张 → 2/60', true);
  await page.waitForSelector(`${ui('viewer-image')}[data-loaded="true"]`, { timeout: 15000 });
  await page.click(ui('prev-button'));
  await page.waitForFunction((sel) => document.querySelector(sel)?.textContent.trim() === '1/60', ui('stats-index'), { timeout: 8000 });
  check('上一张 → 1/60', true);

  console.log('\n[4] 缩放');
  const scaleOf = async () => page.evaluate(() => {
    const img = document.querySelector('[data-testid="viewer-image"]');
    if (!img) return 0;
    const t = getComputedStyle(img).transform;
    if (!t || t === 'none') return 1;
    const m = t.match(/matrix\(([-0-9.]+)/);
    return m ? parseFloat(m[1]) : 1;
  });
  const s0 = await scaleOf();
  await page.click(ui('zoom-in'));
  await page.waitForTimeout(250);
  const s1 = await scaleOf();
  check('放大后 scale 变大', s1 > s0 + 0.01, `${s0} -> ${s1}`);
  await page.click(ui('zoom-out'));
  await page.waitForTimeout(250);
  const s2 = await scaleOf();
  check('缩小后 scale 变小', s2 < s1 - 0.01, `${s1} -> ${s2}`);
  await page.click(ui('zoom-fit'));
  await page.waitForTimeout(200);
  await page.click(ui('zoom-actual'));
  await page.waitForTimeout(200);
  await page.click(ui('zoom-fit'));
  await shot('03-zoom.png');

  console.log('\n[5] 自动放映');
  await page.fill(ui('interval-input'), '1');
  const beforeAuto = (await text('stats-index')).trim();
  await page.click(ui('autoplay-toggle'));
  await page.waitForFunction((sel, before) => document.querySelector(sel)?.textContent.trim() !== before, ui('stats-index'), beforeAuto, { timeout: 8000 }).catch(() => {});
  await sleep(1600);
  const afterAuto = (await text('stats-index')).trim();
  await page.click(ui('autoplay-toggle'));
  check('自动放映推进了照片', afterAuto !== beforeAuto, `${beforeAuto} -> ${afterAuto}`);
  await shot('04-autoplay.png');

  console.log('\n[6] pick 三张（键盘 P + 按钮）');
  while ((await text('stats-index')).trim() !== '1/60') {
    const cur = parseInt((await text('stats-index')).split('/')[0], 10);
    await page.click(cur > 1 ? ui('prev-button') : ui('next-button'));
    await page.waitForTimeout(120);
  }
  for (let i = 1; i <= 3; i++) {
    await page.keyboard.press('p');
    await page.waitForTimeout(250);
    check(`第 ${i} 张 pick 后计数=${i}`, (await text('stats-picked')).trim() === String(i), await text('stats-picked'));
    const pressed = await page.getAttribute(ui('pick-button'), 'aria-pressed');
    check(`第 ${i} 张 pick 按钮高亮`, pressed === 'true', String(pressed));
    if (i < 3) {
      await page.click(ui('next-button'));
      await page.waitForFunction(
        ({ sel, n }) => document.querySelector(sel)?.textContent.trim().startsWith(`${n}/`),
        { sel: ui('stats-index'), n: i + 1 },
        { timeout: 8000 },
      );
      await page.waitForSelector(`${ui('viewer-image')}[data-loaded="true"]`, { timeout: 15000 });
    }
  }
  await page.click(ui('pick-button'));
  await page.waitForTimeout(300);
  check('再次点击取消 pick', (await text('stats-picked')).trim() === '2', await text('stats-picked'));
  await page.click(ui('pick-button'));
  await page.waitForTimeout(300);
  check('恢复 pick 后计数=3', (await text('stats-picked')).trim() === '3', await text('stats-picked'));
  await shot('05-picked.png');

  console.log('\n[7] 结束本轮 → 进入下一轮');
  await page.click(ui('end-round-button'));
  await page.waitForSelector(ui('end-dialog'), { timeout: 8000 });
  await page.waitForTimeout(600);
  const dialogText = await page.textContent(ui('end-dialog'));
  check('结束弹窗显示保留 3 张', dialogText.includes('保留') && dialogText.includes('3'), dialogText.slice(0, 120));
  check('下一轮按钮可用', !(await page.isDisabled(ui('next-round-button'))));
  await shot('06-end-dialog.png');
  await page.click(ui('next-round-button'));
  await page.waitForFunction((sel) => document.querySelector(sel)?.textContent.trim() === '3', ui('stats-total'), { timeout: 15000 });
  await page.waitForSelector(`${ui('viewer-image')}[data-loaded="true"]`, { timeout: 20000 });
  check('第 2 轮总数为 3', true);
  check('第 2 轮从 1/3 开始', (await text('stats-index')).trim() === '1/3', await text('stats-index'));
  check('第 2 轮 pick 数为 0', (await text('stats-picked')).trim() === '0', await text('stats-picked'));
  await shot('07-round2.png');

  console.log('\n[8] 第 2 轮 pick 两张 → 生成结果');
  await page.click(ui('pick-button'));
  await page.waitForTimeout(300);
  await page.click(ui('next-button'));
  await page.waitForFunction((sel) => document.querySelector(sel)?.textContent.trim() === '2/3', ui('stats-index'), { timeout: 8000 });
  await page.waitForSelector(`${ui('viewer-image')}[data-loaded="true"]`, { timeout: 15000 });
  await page.click(ui('pick-button'));
  await page.waitForTimeout(300);
  check('第 2 轮 pick 数为 2', (await text('stats-picked')).trim() === '2', await text('stats-picked'));
  await page.click(ui('end-round-button'));
  await page.waitForSelector(ui('end-dialog'), { timeout: 8000 });
  await page.waitForTimeout(600);
  await page.click(ui('finish-button'));
  await page.waitForSelector(ui('result-view'), { timeout: 20000 });
  check('进入结果页', await page.isVisible(ui('result-view')));
  const resultText = await page.textContent(ui('result-view'));
  check('结果页显示最终保留 2 张', resultText.includes('保留') && resultText.includes('2'), resultText.slice(0, 160));
  await page.waitForTimeout(600);
  await shot('08-results.png');

  console.log('\n[9] 下载并校验结果 JSON');
  const current = await page.evaluate(async () => (await fetch('/api/sessions/current')).json());
  const sid = current.session.id;
  check('当前会话 phase=finished', current.session.phase === 'finished', current.session.phase);
  check('会话来源为 browser', current.session.origin === 'browser', String(current.session.origin));
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: 15000 }),
    page.click(ui('result-download')),
  ]);
  const jsonPath = path.join(OUT, 'result-downloaded.json');
  await download.saveAs(jsonPath);
  check('下载文件名为 .json', download.suggestedFilename().endsWith('.json'), download.suggestedFilename());
  const result = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
  check('schemaVersion=1', result.schemaVersion === 1);
  check('origin=browser', result.origin === 'browser', String(result.origin));
  check('totalPhotos=60', result.totalPhotos === 60, String(result.totalPhotos));
  check('roundsPlayed=2', result.roundsPlayed === 2, String(result.roundsPlayed));
  check('photos 含全部 60 张', result.photos.length === 60, String(result.photos.length));
  check('pickedPhotoCount=3（多轮入选）', result.pickedPhotoCount === 3, String(result.pickedPhotoCount));
  const sorted = result.photos.every((p, i, arr) => i === 0 || arr[i - 1].pickCount >= p.pickCount);
  check('按 pickCount 降序', sorted);
  check('前两名 pickCount=2 且 pickedInRounds=[1,2]',
    result.photos[0].pickCount === 2 && result.photos[1].pickCount === 2 &&
    JSON.stringify(result.photos[0].pickedInRounds) === '[1,2]' &&
    JSON.stringify(result.photos[1].pickedInRounds) === '[1,2]',
    JSON.stringify(result.photos.slice(0, 3).map((p) => [p.name, p.pickCount, p.pickedInRounds])));
  check('第三名 pickCount=1 且 pickedInRounds=[1]',
    result.photos[2].pickCount === 1 && JSON.stringify(result.photos[2].pickedInRounds) === '[1]',
    JSON.stringify([result.photos[2].name, result.photos[2].pickCount, result.photos[2].pickedInRounds]));
  check('browser 结果 absolutePath 为空串', result.photos.every((p) => p.absolutePath === ''));
  check('lastRound 存在且 index=2', result.lastRound && result.lastRound.index === 2, JSON.stringify(result.lastRound).slice(0, 200));
  check('lastRound.pickedCount=2 且 id 数与之一致',
    result.lastRound && result.lastRound.pickedCount === 2 && result.lastRound.pickedPhotoIds.length === 2,
    JSON.stringify(result.lastRound).slice(0, 300));
  const apiResult = await page.evaluate(async (id) => (await fetch(`/api/sessions/${id}/result`)).json(), sid);
  check('下载内容与 API 结果一致', JSON.stringify(apiResult) === JSON.stringify(result));

  console.log('\n[10] 一键导出最后一轮保留照片（mock 目录句柄）');
  const lastNames = result.lastRound.pickedPhotoIds.map((id) => result.photos.find((p) => p.id === id)?.name || id);
  await page.evaluate(() => {
    window.__exported = [];
    window.__picturepickTestExportDirHandle = {
      name: 'mock-export-folder',
      getFileHandle: async (name) => ({
        createWritable: async () => ({
          write: async (blob) => {
            window.__exported.push({ name, size: blob && blob.size ? blob.size : 0 });
          },
          close: async () => {},
        }),
      }),
    };
  });
  await page.click(ui('export-photos'));
  await page.waitForFunction(() => window.__exported && window.__exported.length >= 2, null, { timeout: 30000 });
  await page.waitForTimeout(500);
  const exported = await page.evaluate(() => window.__exported);
  check('导出了 2 个文件', exported.length === 2, JSON.stringify(exported));
  check('导出文件名与保留照片一致', JSON.stringify(exported.map((f) => f.name).sort()) === JSON.stringify([...lastNames].sort()),
    JSON.stringify({ exported: exported.map((f) => f.name), expected: lastNames }));
  check('导出文件非空', exported.every((f) => f.size > 0), JSON.stringify(exported));
  check('导出后无未捕获异常', pageErrors.length === 0, pageErrors.join(' | ') || '');
  await shot('09-export.png');

  console.log('\n[11] 全程未请求后端图片（照片不上传）');
  check('browser 会话未请求 /image 接口', imageRequests.length === 0, String(imageRequests.slice(0, 3)));

  console.log('\n[12] 再选一组');
  await page.click(ui('restart-button'));
  await page.waitForSelector(ui('pick-folder-button'), { timeout: 10000 });
  check('返回启动页（可选择新文件夹）', await page.isVisible(ui('pick-folder-button')));
  await shot('10-restart.png');

  await browser.close();
  const summary = `\n===== 浏览器全流程 UI 验收: PASS=${pass} FAIL=${fail} =====`;
  console.log(summary);
  fs.writeFileSync(path.join(OUT, 'ui-e2e-result.txt'), summary + '\n' + (failures.length ? failures.join('\n') : 'ALL_PASS') + '\n');
  process.exit(fail === 0 ? 0 : 1);
})().catch(async (error) => {
  console.error('UI_E2E_CRASH', error);
  fs.mkdirSync(OUT, { recursive: true });
  fs.writeFileSync(path.join(OUT, 'ui-e2e-result.txt'), 'CRASH: ' + String(error));
  process.exit(2);
});
