/**
 * PicturePick 桌面端主进程。
 *
 * 职责：
 *  1. 单实例锁 + 创建 1280x840 深色窗口（contextIsolation, 无 Node 集成）。
 *  2. 选空闲端口 → 拉起 Python 后端 sidecar（打包后为 resources/backend/picturepick-backend.exe，
 *     开发时为 `python -m picturepick serve`）→ 轮询 /api/health → loadURL。
 *  3. 退出时杀掉后端子进程树（Windows 用 taskkill /T /F），避免残留占用端口/文件锁。
 *  4. IPC：原生目录选择、JSON 另存为、用系统程序打开路径。
 *
 * 调试/验证用环境变量（详见 README）：
 *  - PICTUREPICK_DESKTOP_TEST_URL          跳过后端，直接加载该 URL（用于空壳启动验证）
 *  - PICTUREPICK_DESKTOP_SCREENSHOT        启动完成后把窗口截图写入该 PNG 路径
 *  - PICTUREPICK_DESKTOP_SCREENSHOT_DELAY_MS  截图前等待毫秒数（默认 1500）
 *  - PICTUREPICK_DESKTOP_EXIT_AFTER_SCREENSHOT=1  截图后自动退出
 *  - PICTUREPICK_DESKTOP_HEADLESS=1        无界面环境：启动失败不弹模态框，直接退出
 *  - PICTUREPICK_DESKTOP_USER_DATA         覆盖 userData 目录（沙箱内必须指向工作区）
 *  - PICTUREPICK_DESKTOP_LOG_DIR           覆盖日志目录
 *  - PICTUREPICK_DESKTOP_BACKEND_TIMEOUT_MS 等待 /api/health 的总超时（默认 60000）
 *  - PICTUREPICK_PYTHON                    开发模式覆盖 Python 解释器
 */
import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron';
import { spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import util from 'node:util';

// 防御：某些开发环境（CI/沙箱/IDE 终端）会全局设置 ELECTRON_RUN_AS_NODE=1，
// 此时 electron.exe 以纯 Node 方式运行，require('electron') 拿不到 app，报错难以定位。
if (typeof app === 'undefined' || app === null) {
  console.error(
    '[PicturePick] Electron 主进程未正确初始化：检测到 ELECTRON_RUN_AS_NODE=1 之类的环境变量。\n' +
      '请在启动 electron 前清除该变量，例如 PowerShell: Remove-Item Env:\\ELECTRON_RUN_AS_NODE',
  );
  process.exit(1);
}

const APP_TITLE = 'PicturePick 选片';
const APP_ID = 'com.picturepick.app';
const WINDOW_WIDTH = 1280;
const WINDOW_HEIGHT = 840;
const WINDOW_MIN_WIDTH = 900;
const WINDOW_MIN_HEIGHT = 600;
const BACKGROUND_COLOR = '#0f1115';
const HEALTH_TIMEOUT_MS = Number(process.env.PICTUREPICK_DESKTOP_BACKEND_TIMEOUT_MS ?? 60_000);
const HEALTH_POLL_INTERVAL_MS = 100;
const BACKEND_EXIT_WAIT_MS = 5_000;

const isDev = !app.isPackaged;
const DEBUG_LOG = isDev || process.env.PICTUREPICK_DESKTOP_DEBUG === '1';

interface BackendHandle {
  child: ChildProcess;
  pid: number;
  port: number;
  logPath: string;
}

interface BackendCommand {
  command: string;
  args: string[];
  cwd: string;
  env: NodeJS.ProcessEnv;
}

let mainWindow: BrowserWindow | null = null;
let backend: BackendHandle | null = null;
let isQuitting = false;
let quitCleanupStarted = false;
let mainLogPath = '';

/* -------------------------------------------------------------------------- */
/* 通用工具                                                                    */
/* -------------------------------------------------------------------------- */

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 桌面工程根目录（开发时为 desktop/，打包后为 exe 所在目录）。 */
function appRoot(): string {
  return isDev ? path.join(__dirname, '..') : path.dirname(app.getPath('exe'));
}

/** 依次尝试候选目录，返回第一个真正可写的；全部失败时返回 tmpdir。 */
function resolveWritableDir(candidates: readonly string[]): string {
  const probe = `.write-probe-${process.pid}`;
  for (const candidate of candidates) {
    if (!candidate) continue;
    try {
      fs.mkdirSync(candidate, { recursive: true });
      const probePath = path.join(candidate, probe);
      fs.writeFileSync(probePath, 'ok');
      fs.rmSync(probePath, { force: true });
      return candidate;
    } catch {
      /* 尝试下一个 */
    }
  }
  const fallback = path.join(os.tmpdir(), 'picturepick-logs');
  try {
    fs.mkdirSync(fallback, { recursive: true });
  } catch {
    /* ignore */
  }
  return fallback;
}

function logDir(): string {
  const override = process.env.PICTUREPICK_DESKTOP_LOG_DIR;
  return resolveWritableDir([
    ...(override ? [path.resolve(override)] : []),
    path.join(app.getPath('userData'), 'logs'),
    path.join(appRoot(), 'out', 'logs'),
    path.join(os.tmpdir(), 'picturepick-logs'),
  ]);
}

function truncateTail(text: string, maxChars = 4000): string {
  return text.length > maxChars ? `…（前略）\n${text.slice(-maxChars)}` : text;
}

function readTail(filePath: string, maxChars = 4000): string {
  try {
    return truncateTail(fs.readFileSync(filePath, 'utf8'), maxChars);
  } catch {
    return '(无法读取日志文件)';
  }
}

/* -------------------------------------------------------------------------- */
/* 日志：主进程日志 + 后端 stdout/stderr 落盘                                   */
/* -------------------------------------------------------------------------- */

function initLogging(): void {
  const dir = logDir();
  mainLogPath = path.join(dir, 'main.log');
  try {
    fs.appendFileSync(mainLogPath, `\n===== PicturePick desktop started ${new Date().toISOString()} =====\n`);
  } catch {
    mainLogPath = '';
  }
}

function log(...args: unknown[]): void {
  const line = `[${new Date().toISOString()}] ${util.format(...args)}`;
  if (mainLogPath) {
    try {
      fs.appendFileSync(mainLogPath, `${line}\n`);
    } catch {
      /* ignore */
    }
  }
  if (DEBUG_LOG) console.log(line);
}

/** 在后端日志文件尾部追加内容（供 --dir 预扫描等场景）。 */
function appendBackendLog(filePath: string, text: string): void {
  try {
    fs.appendFileSync(filePath, text);
  } catch {
    /* ignore */
  }
}

/**
 * 直接读取后端子进程输出并转发到日志文件（开发模式同时打印到控制台）。
 * 管道 stdio 在受限沙箱中可能不可用，调用方会回退到文件描述符模式。
 */
function pipeChildOutput(child: ChildProcess, logPath: string): void {
  const stream = fs.createWriteStream(logPath, { flags: 'a' });
  stream.on('error', (err) => log(`后端日志写入失败: ${String(err)}`));

  const forward = (streamName: 'stdout' | 'stderr') => {
    const source = streamName === 'stdout' ? child.stdout : child.stderr;
    if (!source) return;
    source.on('data', (chunk: Buffer | string) => {
      const text = typeof chunk === 'string' ? chunk : chunk.toString('utf8');
      stream.write(text);
      if (isDev) process.stdout.write(`[backend:${streamName}] ${text}`);
    });
  };

  forward('stdout');
  forward('stderr');
  child.on('close', (code, signal) => {
    const line = `\n[desktop] backend exited code=${code ?? 'null'} signal=${signal ?? 'null'}\n`;
    stream.write(line);
    if (isDev) process.stdout.write(line);
    stream.end();
  });
}

/** 文件描述符模式下的日志 tail（开发模式把后端日志回显到控制台）。 */
function startLogTail(logPath: string): () => void {
  let offset = 0;
  try {
    offset = fs.statSync(logPath).size;
  } catch {
    offset = 0;
  }
  const timer = setInterval(() => {
    let size = 0;
    try {
      size = fs.statSync(logPath).size;
    } catch {
      return;
    }
    if (size <= offset) {
      if (size < offset) offset = 0;
      return;
    }
    try {
      const fd = fs.openSync(logPath, 'r');
      const buffer = Buffer.alloc(size - offset);
      fs.readSync(fd, buffer, 0, buffer.length, offset);
      fs.closeSync(fd);
      offset = size;
      process.stdout.write(`[backend] ${buffer.toString('utf8')}`);
    } catch {
      /* ignore */
    }
  }, 400);
  timer.unref?.();
  return () => clearInterval(timer);
}

/* -------------------------------------------------------------------------- */
/* 端口 / 后端进程                                                              */
/* -------------------------------------------------------------------------- */

function findFreePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port = typeof address === 'object' && address !== null ? address.port : 0;
      server.close(() => {
        if (port > 0) resolve(port);
        else reject(new Error('未能获取空闲端口'));
      });
    });
  });
}

function buildBackendCommand(port: number): BackendCommand {
  const commonArgs = ['serve', '--host', '127.0.0.1', '--port', String(port), '--no-open'];

  if (app.isPackaged) {
    const exePath = path.join(process.resourcesPath, 'backend', 'picturepick-backend.exe');
    if (!fs.existsSync(exePath)) {
      throw new Error(
        `打包后的后端不存在：${exePath}\n请先在 desktop/ 目录执行 npm run pack:backend 后再打包 Electron 安装包。`,
      );
    }
    return {
      command: exePath,
      args: commonArgs,
      cwd: path.dirname(exePath),
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    };
  }

  const backendDir = path.resolve(__dirname, '..', '..', 'backend');
  if (!fs.existsSync(path.join(backendDir, 'picturepick', '__init__.py'))) {
    throw new Error(
      `开发模式找不到 Python 后端包：${path.join(backendDir, 'picturepick')}\n` +
        '请确认 backend/ 已就绪，或用 PICTUREPICK_DESKTOP_TEST_URL 先验证 Electron 外壳。',
    );
  }
  const python = process.env.PICTUREPICK_PYTHON || 'python';
  return {
    command: python,
    args: ['-m', 'picturepick', ...commonArgs],
    cwd: backendDir,
    env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' },
  };
}

/** 等待子进程真正 spawn 成功；遇到 error（如沙箱 EPERM）返回 false。 */
function waitForSpawn(child: ChildProcess, timeoutMs = 1500): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value: boolean) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    child.once('spawn', () => finish(true));
    child.once('error', () => finish(false));
    setTimeout(() => finish(true), timeoutMs);
  });
}

async function startBackend(port: number): Promise<BackendHandle> {
  const spec = buildBackendCommand(port);
  const logsDir = logDir();
  const logPath = path.join(logsDir, 'backend.log');
  appendBackendLog(logPath, `\n===== backend start ${new Date().toISOString()} =====\n`);
  log(`启动后端：${spec.command} ${spec.args.join(' ')}（cwd=${spec.cwd}）`);

  // 首选管道 stdio（可实时转发 stdout/stderr）；受限沙箱禁止创建管道时回退到文件描述符。
  const piped = spawn(spec.command, spec.args, {
    cwd: spec.cwd,
    env: spec.env,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const pipedOk = await waitForSpawn(piped);
  if (pipedOk && piped.pid) {
    pipeChildOutput(piped, logPath);
    registerBackendExit(piped, logPath);
    log(`后端已启动（pid=${piped.pid}, port=${port}, 管道日志模式）`);
    return { child: piped, pid: piped.pid, port, logPath };
  }

  log('管道 stdio 不可用，回退为日志文件描述符模式');
  try {
    piped.kill();
  } catch {
    /* ignore */
  }

  const logFd = fs.openSync(logPath, 'a');
  let fileChild: ChildProcess;
  try {
    fileChild = spawn(spec.command, spec.args, {
      cwd: spec.cwd,
      env: spec.env,
      windowsHide: true,
      stdio: ['ignore', logFd, logFd],
    });
  } finally {
    // 子进程已继承句柄，父进程可关闭自己的副本
    try {
      fs.closeSync(logFd);
    } catch {
      /* ignore */
    }
  }
  const fileOk = await waitForSpawn(fileChild);
  if (!fileOk || !fileChild.pid) {
    throw new Error(`后端进程启动失败：${spec.command}\n${readTail(logPath)}`);
  }
  const stopTail = isDev ? startLogTail(logPath) : () => undefined;
  fileChild.once('close', stopTail);
  registerBackendExit(fileChild, logPath);
  log(`后端已启动（pid=${fileChild.pid}, port=${port}, 文件日志模式）`);
  return { child: fileChild, pid: fileChild.pid, port, logPath };
}

function registerBackendExit(child: ChildProcess, logPath: string): void {
  child.on('error', (err) => log(`后端子进程错误: ${String(err)}`));
  child.on('close', (code) => log(`后端子进程退出 code=${code ?? 'null'}，日志：${logPath}`));
}

/** 轮询 /api/health 直到就绪或超时。 */
async function waitForHealth(port: number, timeoutMs = HEALTH_TIMEOUT_MS): Promise<void> {
  const url = `http://127.0.0.1:${port}/api/health`;
  const deadline = Date.now() + timeoutMs;
  let lastError = '尚未收到响应';

  while (Date.now() < deadline) {
    if (backend && backend.child.exitCode !== null && backend.child.exitCode !== undefined) {
      throw new Error(
        `后端进程在就绪前退出（exitCode=${backend.child.exitCode}）。\n日志：${backend.logPath}\n${readTail(backend.logPath)}`,
      );
    }
    try {
      const body = await httpGetText(url, 1200);
      const parsed = JSON.parse(body) as { status?: string };
      if (parsed && parsed.status === 'ok') return;
      lastError = `健康检查响应异常：${body.slice(0, 200)}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await sleep(HEALTH_POLL_INTERVAL_MS);
  }

  throw new Error(
    `等待后端就绪超时（${timeoutMs}ms，端口 ${port}）。最后错误：${lastError}\n` +
      (backend ? `日志：${backend.logPath}\n${readTail(backend.logPath)}` : ''),
  );
}

function httpGetText(url: string, timeoutMs: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const request = http.get(url, { timeout: timeoutMs, headers: { Accept: 'application/json' } }, (response) => {
      const status = response.statusCode ?? 0;
      const chunks: Buffer[] = [];
      response.on('data', (chunk: Buffer) => chunks.push(chunk));
      response.on('end', () => {
        const body = Buffer.concat(chunks).toString('utf8');
        if (status >= 200 && status < 300) resolve(body);
        else reject(new Error(`HTTP ${status}: ${body.slice(0, 200)}`));
      });
    });
    request.on('timeout', () => {
      request.destroy(new Error(`请求超时：${url}`));
    });
    request.on('error', reject);
  });
}

/** 杀掉后端进程树。Windows 用 taskkill /T /F 以覆盖 uvicorn 可能派生的子进程。 */
function killProcessTree(pid: number): Promise<void> {
  return new Promise((resolve) => {
    if (process.platform !== 'win32') {
      try {
        process.kill(pid, 'SIGTERM');
      } catch {
        /* ignore */
      }
      resolve();
      return;
    }
    const killer = spawn('taskkill', ['/PID', String(pid), '/T', '/F'], {
      windowsHide: true,
      stdio: 'ignore',
    });
    const done = () => resolve();
    killer.once('close', done);
    killer.once('error', () => {
      try {
        process.kill(pid);
      } catch {
        /* ignore */
      }
      done();
    });
    setTimeout(done, 4000);
  });
}

async function stopBackend(): Promise<void> {
  const handle = backend;
  backend = null;
  if (!handle) return;
  const { pid, child } = handle;
  log(`停止后端 pid=${pid}`);
  try {
    if (child.exitCode !== null && child.exitCode !== undefined) return;
    if (pid) await killProcessTree(pid);
    if (child.exitCode === null || child.exitCode === undefined) {
      try {
        child.kill();
      } catch {
        /* ignore */
      }
    }
  } catch (error) {
    log(`停止后端出错: ${String(error)}`);
  }
}

/* -------------------------------------------------------------------------- */
/* 窗口                                                                        */
/* -------------------------------------------------------------------------- */

function createWindow(): BrowserWindow {
  const iconPath = path.join(appRoot(), 'resources', 'icon.ico');
  const window = new BrowserWindow({
    width: WINDOW_WIDTH,
    height: WINDOW_HEIGHT,
    minWidth: WINDOW_MIN_WIDTH,
    minHeight: WINDOW_MIN_HEIGHT,
    title: APP_TITLE,
    backgroundColor: BACKGROUND_COLOR,
    show: false,
    autoHideMenuBar: true,
    ...(fs.existsSync(iconPath) ? { icon: iconPath } : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      spellcheck: false,
      devTools: isDev,
      webSecurity: true,
    },
  });

  window.once('ready-to-show', () => {
    window.show();
    log(`window shown title="${APP_TITLE}" size=${WINDOW_WIDTH}x${WINDOW_HEIGHT}`);
  });

  // 保持任务栏/标题栏始终为「PicturePick 选片」，不被页面 <title> 覆盖（契约要求窗口标题固定）。
  window.on('page-title-updated', (event) => {
    event.preventDefault();
  });

  window.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/i.test(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });

  window.webContents.on('will-navigate', (event, url) => {
    const allowedPrefix = backend ? `http://127.0.0.1:${backend.port}` : null;
    if (allowedPrefix && url.startsWith(allowedPrefix)) return;
    event.preventDefault();
    if (/^https?:/i.test(url)) void shell.openExternal(url);
  });

  window.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    log(`页面加载失败 code=${errorCode} desc=${errorDescription} url=${validatedURL}`);
  });

  window.webContents.on('render-process-gone', (_event, details) => {
    log(`渲染进程退出：${JSON.stringify(details)}`);
  });

  window.on('closed', () => {
    mainWindow = null;
  });

  return window;
}

async function loadStartPage(port: number): Promise<void> {
  if (!mainWindow) return;
  const url = `http://127.0.0.1:${port}/`;
  log(`loading page url=${url}`);
  await mainWindow.loadURL(url);
  log('page loaded');
}

function buildErrorPage(message: string): string {
  const escaped = message
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  return `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <title>PicturePick 启动失败</title>
    <style>
      :root { color-scheme: dark; }
      html, body { height: 100%; margin: 0; background: #0f1115; color: #e6e8eb;
        font-family: "Segoe UI", "Microsoft YaHei", system-ui, sans-serif; }
      main { max-width: 860px; margin: 0 auto; padding: 56px 32px; }
      h1 { font-size: 26px; color: #ff8a80; margin: 0 0 12px; }
      p { color: #9aa4b2; line-height: 1.7; }
      pre { background: #161a21; border: 1px solid #262c37; border-radius: 10px;
        padding: 18px; white-space: pre-wrap; word-break: break-word; color: #cfd6e0;
        font-family: "Cascadia Code", Consolas, monospace; font-size: 13px; line-height: 1.6; }
    </style>
  </head>
  <body>
    <main>
      <h1>PicturePick 启动失败</h1>
      <p>桌面外壳没能启动本地后端服务。请把下面的错误信息交给开发者排查。</p>
      <pre>${escaped}</pre>
    </main>
  </body>
</html>`;
}

async function showStartupError(error: unknown): Promise<void> {
  const message = error instanceof Error ? error.message : String(error);
  log(`启动失败：${message}`);
  if (mainWindow) {
    try {
      await mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(buildErrorPage(message))}`);
    } catch (loadError) {
      log(`错误页加载失败：${String(loadError)}`);
    }
  }
  await maybeCaptureScreenshot();
  const headless = process.env.PICTUREPICK_DESKTOP_HEADLESS === '1';
  if (!headless) {
    dialog.showErrorBox('PicturePick 启动失败', message);
  }
  app.quit();
}

/* -------------------------------------------------------------------------- */
/* 截图（启动验证证据）                                                         */
/* -------------------------------------------------------------------------- */

async function maybeCaptureScreenshot(): Promise<void> {
  const target = process.env.PICTUREPICK_DESKTOP_SCREENSHOT;
  if (!target || !mainWindow) return;
  const delayMs = Number(process.env.PICTUREPICK_DESKTOP_SCREENSHOT_DELAY_MS ?? 1500);
  if (delayMs > 0) await sleep(delayMs);
  try {
    const image = await mainWindow.webContents.capturePage();
    const outputPath = path.resolve(target);
    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    fs.writeFileSync(outputPath, image.toPNG());
    const size = image.getSize();
    log(`screenshot saved path=${outputPath} size=${size.width}x${size.height} bytes=${image.toPNG().length}`);
  } catch (error) {
    log(`screenshot failed: ${String(error)}`);
  }
  if (process.env.PICTUREPICK_DESKTOP_EXIT_AFTER_SCREENSHOT === '1') {
    log('screenshot done, quitting');
    app.quit();
  }
}

/* -------------------------------------------------------------------------- */
/* IPC                                                                         */
/* -------------------------------------------------------------------------- */

function registerIpc(): void {
  ipcMain.handle('picturepick:pick-directory', async () => {
    if (!mainWindow) return null;
    const result = await dialog.showOpenDialog(mainWindow, {
      title: '选择照片目录',
      buttonLabel: '选择此目录',
      properties: ['openDirectory'],
    });
    if (result.canceled || result.filePaths.length === 0) return null;
    return result.filePaths[0];
  });

  ipcMain.handle('picturepick:save-json', async (_event, defaultName: unknown, content: unknown) => {
    if (!mainWindow) return null;
    if (typeof defaultName !== 'string' || typeof content !== 'string') {
      throw new Error('saveJson 参数非法');
    }
    const safeName = defaultName.replace(/[\\/:*?"<>|]/g, '_') || 'picturepick-result.json';
    const result = await dialog.showSaveDialog(mainWindow, {
      title: '保存选片结果',
      defaultPath: safeName,
      filters: [
        { name: 'JSON 文件', extensions: ['json'] },
        { name: '所有文件', extensions: ['*'] },
      ],
    });
    if (result.canceled || !result.filePath) return null;
    await fs.promises.writeFile(result.filePath, content, 'utf8');
    log(`结果 JSON 已保存：${result.filePath}`);
    return result.filePath;
  });

  ipcMain.handle('picturepick:open-path', async (_event, target: unknown) => {
    if (typeof target !== 'string' || target.trim() === '') return '路径为空';
    try {
      const errorMessage = await shell.openPath(target);
      return errorMessage ?? '';
    } catch (error) {
      return error instanceof Error ? error.message : String(error);
    }
  });
}

/* -------------------------------------------------------------------------- */
/* 生命周期                                                                    */
/* -------------------------------------------------------------------------- */

async function onReady(): Promise<void> {
  app.setAppUserModelId(APP_ID);
  initLogging();
  log(`PicturePick 桌面端启动 isDev=${isDev} userData=${app.getPath('userData')}`);

  registerIpc();
  mainWindow = createWindow();

  const testUrl = process.env.PICTUREPICK_DESKTOP_TEST_URL;
  try {
    if (testUrl) {
      log(`测试模式：跳过后端，直接加载 ${testUrl}`);
      await mainWindow.loadURL(testUrl);
      log('测试页面加载完成');
    } else {
      const port = await findFreePort();
      log(`free port selected port=${port}`);
      backend = await startBackend(port);
      await waitForHealth(port);
      log(`backend ready port=${port} url=http://127.0.0.1:${port}/`);
      await loadStartPage(port);
    }
    await maybeCaptureScreenshot();
    if (process.env.PICTUREPICK_DESKTOP_EXIT_AFTER_SCREENSHOT === '1' && !process.env.PICTUREPICK_DESKTOP_SCREENSHOT) {
      log('启动验证完成，自动退出');
      app.quit();
    }
  } catch (error) {
    await showStartupError(error);
  }
}

function focusExistingWindow(): void {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.focus();
}

function installLifecycleHooks(): void {
  app.on('second-instance', () => focusExistingWindow());

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit();
  });

  app.on('before-quit', () => {
    isQuitting = true;
  });

  // 退出前确保后端进程树被终结；只拦截一次，避免递归阻止退出。
  app.on('will-quit', (event) => {
    if (quitCleanupStarted || !backend) return;
    quitCleanupStarted = true;
    event.preventDefault();
    log('应用退出：正在清理后端进程');
    const timeout = new Promise<void>((resolve) => setTimeout(resolve, BACKEND_EXIT_WAIT_MS));
    void Promise.race([stopBackend(), timeout]).finally(() => {
      app.quit();
    });
  });

  app.on('quit', () => {
    log('应用已退出');
  });

  process.on('exit', () => {
    if (backend?.pid) {
      try {
        process.kill(backend.pid);
      } catch {
        /* ignore */
      }
    }
    void isQuitting;
  });
}

function bootstrap(): void {
  installLifecycleHooks();
  app.whenReady().then(onReady).catch((error) => {
    console.error(error);
    dialog.showErrorBox('PicturePick 启动失败', error instanceof Error ? error.message : String(error));
    app.quit();
  });
}

ensureUserDataWritable();

function ensureUserDataWritable(): void {
  const override = process.env.PICTUREPICK_DESKTOP_USER_DATA;
  if (override) {
    try {
      fs.mkdirSync(path.resolve(override), { recursive: true });
      app.setPath('userData', path.resolve(override));
      return;
    } catch {
      /* 继续用默认值 */
    }
  }
  // 沙箱/只读环境下默认 userData 可能不可写，此时退化到工作区 .tmp 目录。
  try {
    const defaultDir = app.getPath('userData');
    fs.mkdirSync(defaultDir, { recursive: true });
    fs.accessSync(defaultDir, fs.constants.W_OK);
  } catch {
    const fallback = path.join(appRoot(), '.userdata');
    try {
      fs.mkdirSync(fallback, { recursive: true });
      app.setPath('userData', fallback);
    } catch {
      /* 放弃，交给 Chromium 处理 */
    }
  }
}

if (!app.requestSingleInstanceLock()) {
  log('已有实例在运行，退出');
  app.quit();
} else {
  bootstrap();
}
