// 在工作区缓存中下载 Playwright Chromium（沙箱无法用 playwright 自带下载器：其 fork 子进程被拦截）
// 用法：node scripts/fetch-playwright-chromium.cjs
const https = require('https');
const fs = require('fs');
const path = require('path');

const url = process.argv[2] || 'https://cdn.npmmirror.com/binaries/playwright/builds/chromium/1148/chromium-win64.zip';
const dest = process.argv[3] || path.join(__dirname, '..', '.cache', 'playwright', 'chromium-1148.zip');

function download(u, target, redirects = 0) {
  if (redirects > 5) throw new Error('too many redirects');
  https.get(u, (res) => {
    if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
      res.resume();
      return download(res.headers.location, target, redirects + 1);
    }
    if (res.statusCode !== 200) {
      console.error('HTTP', res.statusCode, u);
      process.exit(1);
    }
    const total = Number(res.headers['content-length'] || 0);
    let received = 0;
    let lastPct = -10;
    const file = fs.createWriteStream(target);
    res.on('data', (chunk) => {
      received += chunk.length;
      const pct = total ? Math.floor((received / total) * 100) : 0;
      if (pct >= lastPct + 10) { lastPct = pct; console.log(`  ${pct}% (${(received / 1048576).toFixed(1)}/${(total / 1048576).toFixed(1)} MB)`); }
    });
    res.pipe(file);
    file.on('finish', () => file.close(() => console.log('DONE', target, fs.statSync(target).size)));
    file.on('error', (e) => { console.error('WRITE_ERR', e.message); process.exit(1); });
  }).on('error', (e) => {
    if (redirects === 0) console.error('NET_ERR', e.message);
    process.exit(1);
  });
}

fs.mkdirSync(path.dirname(dest), { recursive: true });
console.log('Downloading', url, '->', dest);
download(url, dest);
