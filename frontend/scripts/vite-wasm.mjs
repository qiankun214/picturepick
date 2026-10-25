/**
 * 沙箱兼容的 Vite 启动器（build / dev / preview）。
 *
 * 背景：DSH 沙箱禁止 Node 通过管道 stdio 启动子进程（EPERM）。原生 esbuild 的
 * JS API 依赖一个常驻子进程服务，因此 `vite build` / `vite dev` 在沙箱内会以
 * `spawn EPERM` 失败。
 *
 * 方案：package.json 把 `esbuild` 别名到 `esbuild-wasm`，本脚本在导入 Vite 前：
 *   1) 注册 Node loader，把 'esbuild' 重定向到 scripts/esbuild-shim.mjs；
 *   2) 垫片加载 esbuild-wasm 的 lib/browser.js（WebAssembly，进程内、无子进程）；
 *   3) 初始化 WASM（worker: false），并补齐浏览器构建需要的 `self` 全局。
 * wasm 版 fs 不支持 readdir，无法用于 esbuild 的「打包/配置加载」，但
 * transform（TS→JS、JS/CSS 压缩、target 降级）完全可用，而 Vite 生产构建
 * 恰好只需要 transform。因此本脚本用 wasm 把 vite.config.ts 转成 ESM，
 * 再以 `configFile: false` 内联传入 Vite（配置仍以 vite.config.ts 为准）。
 *
 * 用法：
 *   node scripts/vite-wasm.mjs build
 *   node scripts/vite-wasm.mjs dev
 *   node scripts/vite-wasm.mjs preview
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire, register } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const projectRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const require = createRequire(import.meta.url);
const mode = process.argv[2] ?? 'build';

// wasm 版 esbuild 在进程内执行，需要浏览器构建依赖的 self 全局。
globalThis.self ??= globalThis;

register(new URL('./esbuild-loader.mjs', import.meta.url));

/** 定位 esbuild-wasm 的 wasm 文件（npm 别名下位于 node_modules/esbuild/）。 */
function resolveWasmPath() {
  const candidates = [];
  for (const specifier of ['esbuild/package.json', 'esbuild-wasm/package.json']) {
    try {
      candidates.push(join(dirname(require.resolve(specifier)), 'esbuild.wasm'));
    } catch {
      // 未安装对应包时忽略。
    }
  }
  return candidates.find((candidate) => existsSync(candidate)) ?? null;
}

const esbuild = await import('esbuild');

async function initializeEsbuild() {
  const wasmPath = resolveWasmPath();
  if (!wasmPath) {
    throw new Error('[vite-wasm] 找不到 esbuild.wasm，请确认已安装 esbuild-wasm 别名依赖');
  }
  const wasmModule = await WebAssembly.compile(readFileSync(wasmPath));
  await esbuild.initialize({ wasmModule, worker: false });
  console.log(`[vite-wasm] esbuild(wasm) 就绪 → ${wasmPath}`);
}

/** 把 vite.config.ts 转成可被 Node 原生 import 的 ESM 临时文件。 */
async function loadInlineConfig() {
  const configPath = join(projectRoot, 'vite.config.ts');
  const source = readFileSync(configPath, 'utf8');
  const compiled = await esbuild.transform(source, {
    loader: 'ts',
    format: 'esm',
    target: 'es2022',
    sourcefile: 'vite.config.ts',
  });
  const tempDir = join(projectRoot, 'node_modules', '.vite-temp');
  mkdirSync(tempDir, { recursive: true });
  const tempFile = join(tempDir, `picturepick.vite.config.${process.pid}.mjs`);
  writeFileSync(tempFile, compiled.code, 'utf8');
  const module = await import(`${pathToFileURL(tempFile).href}?t=${Date.now()}`);
  return module.default ?? module;
}

await initializeEsbuild();

const inlineConfig = await loadInlineConfig();
const { build, createServer, preview } = await import('vite');

if (mode === 'dev') {
  const server = await createServer({ configFile: false, root: projectRoot, ...inlineConfig });
  await server.listen();
  server.printUrls();
} else if (mode === 'preview') {
  const server = await preview({ configFile: false, root: projectRoot, ...inlineConfig });
  server.printUrls();
} else if (mode === 'build') {
  await build({ configFile: false, root: projectRoot, ...inlineConfig });
  console.log('[vite-wasm] 前端构建完成 → backend/picturepick/webui');
} else {
  console.error(`[vite-wasm] 未知模式: ${mode}（可用: build / dev / preview）`);
  process.exit(1);
}
