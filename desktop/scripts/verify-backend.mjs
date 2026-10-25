#!/usr/bin/env node
/**
 * 验证 PyInstaller 打包出的后端 sidecar（沙箱内可用，不依赖 PowerShell Start-Process）。
 *
 * 做四件事：
 *   1) 运行 `picturepick-backend.exe version`，期望输出 1.0.0；
 *   2) 以空闲端口启动 `serve --no-open`，轮询 /api/health 直到 ok；
 *   3) GET /，确认内嵌前端 webui 返回 HTML（--require-webui 时强制要求）；
 *   4) 杀掉进程树，确认端口已释放（对应桌面端退出清理逻辑）。
 *
 * 用法（在 desktop/ 目录）：
 *   node scripts/verify-backend.mjs [--require-webui]
 *
 * 退出码 0 = PASS，1 = FAIL。
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const desktopDir = path.resolve(scriptDir, '..');
const rootDir = path.resolve(desktopDir, '..');
const backendDir = path.join(rootDir, 'build', 'backend');
const backendExe = path.join(backendDir, 'picturepick-backend.exe');
const logDir = path.join(rootDir, '.tmp', 'desktop', 'logs');
const logPath = path.join(logDir, 'backend-verify.log');
const requireWebui = process.argv.includes('--require-webui');

function pass(message) {
  console.log(`    [PASS] ${message}`);
}
function warn(message) {
  console.log(`    [WARN] ${message}`);
}
function fail(message) {
  console.log(`    [FAIL] ${message}`);
  process.exitCode = 1;
}
function info(message) {
  console.log(`    [INFO] ${message}`);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port = typeof address === 'object' && address !== null ? address.port : 0;
      server.close(() => (port > 0 ? resolve(port) : reject(new Error('no free port'))));
    });
  });
}

async function portOpen(port) {
  return await new Promise((resolve) => {
    const socket = net.connect({ host: '127.0.0.1', port });
    const done = (result) => {
      socket.removeAllListeners();
      socket.destroy();
      resolve(result);
    };
    socket.setTimeout(700, () => done(false));
    socket.once('connect', () => done(true));
    socket.once('error', () => done(false));
  });
}

async function waitHealth(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastError = 'no response yet';
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/health`, { signal: AbortSignal.timeout(1500) });
      if (response.ok) {
        const body = await response.json();
        if (body && body.status === 'ok') return body;
        lastError = `unexpected body ${JSON.stringify(body)}`;
      } else {
        lastError = `HTTP ${response.status}`;
      }
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await sleep(150);
  }
  throw new Error(`backend not healthy within ${timeoutMs}ms: ${lastError}`);
}

function tail(file, lines = 20) {
  try {
    const content = fs.readFileSync(file, 'utf8').trimEnd().split(/\r?\n/);
    return content.slice(-lines).map((line) => `           ${line}`).join('\n');
  } catch {
    return '           (no log)';
  }
}

async function stopTree(child, pid) {
  if (!pid) return;
  try {
    child.kill();
  } catch {
    /* ignore */
  }
  await sleep(1200);
  if (child.exitCode !== null && child.exitCode !== undefined) return;
  // 关闭端口；taskkill /T 覆盖 uvicorn 可能派生的子进程
  await new Promise((resolve) => {
    const killer = spawn('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true });
    killer.once('close', resolve);
    killer.once('error', resolve);
    setTimeout(resolve, 6000);
  });
}

async function main() {
  console.log('');
  console.log('==> Verify PyInstaller backend sidecar');
  if (!fs.existsSync(backendExe)) {
    fail(`missing ${backendExe}`);
    return;
  }
  const stat = fs.statSync(backendExe);
  pass(`picturepick-backend.exe exists (${stat.size} bytes)`);

  // 1) version（管道 stdio 在沙箱内会 EPERM，改用文件描述符重定向）
  fs.mkdirSync(logDir, { recursive: true });
  const versionLog = path.join(logDir, 'backend-version.log');
  fs.rmSync(versionLog, { force: true });
  const version = await new Promise((resolve) => {
    const outFd = fs.openSync(versionLog, 'a');
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      try {
        resolve(fs.readFileSync(versionLog, 'utf8').trim());
      } catch {
        resolve('');
      }
    };
    try {
      const child = spawn(backendExe, ['version'], { stdio: ['ignore', outFd, outFd], windowsHide: true });
      child.once('close', () => {
        fs.closeSync(outFd);
        finish();
      });
      child.once('error', () => {
        try {
          fs.closeSync(outFd);
        } catch {
          /* ignore */
        }
        finish();
      });
      setTimeout(finish, 20000);
    } catch {
      fs.closeSync(outFd);
      finish();
    }
  });
  info(`version output: ${version || '(empty)'}`);
  if (/^1\.0\.0/.test(version)) pass('version is 1.0.0');
  else fail('unexpected version output (expected 1.0.0)');

  // 2) serve + health + webui
  fs.mkdirSync(logDir, { recursive: true });
  const logFd = fs.openSync(logPath, 'a');
  const port = await getFreePort();
  info(`starting serve --port ${port} (log: ${logPath})`);
  const child = spawn(backendExe, ['serve', '--host', '127.0.0.1', '--port', String(port), '--no-open'], {
    cwd: backendDir,
    stdio: ['ignore', logFd, logFd],
    windowsHide: true,
  });
  fs.closeSync(logFd);

  let healthy = false;
  try {
    const health = await waitHealth(port, 30000);
    healthy = true;
    pass(`/api/health ok: ${JSON.stringify(health)}`);

    const response = await fetch(`http://127.0.0.1:${port}/`, { signal: AbortSignal.timeout(5000) });
    const contentType = response.headers.get('content-type') ?? '';
    const body = await response.text();
    info(`GET / -> ${response.status} ${contentType}`);
    if (contentType.includes('text/html') && /<html/i.test(body)) {
      pass('embedded webui served (frontend index.html)');
    } else if (requireWebui) {
      fail('expected embedded webui index.html, got non-HTML');
    } else {
      warn('GET / is not HTML (frontend not built or not embedded; re-run pack:backend)');
    }
  } catch (error) {
    fail(error instanceof Error ? error.message : String(error));
    console.log('    --- backend log tail ---');
    console.log(tail(logPath));
  } finally {
    await stopTree(child, child.pid);
  }

  // 3) 端口释放
  const stillOpen = await portOpen(port);
  if (stillOpen) fail(`port ${port} still open: backend child process not cleaned up`);
  else pass(`port ${port} released after kill`);

  if (!healthy) process.exitCode = 1;
}

await main();
