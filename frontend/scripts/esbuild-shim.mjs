/**
 * 进程内 esbuild 垫片：把 Vite 对 `esbuild` 的依赖指向 esbuild-wasm 的
 * 浏览器构建（lib/browser.js）。该构建通过 WebAssembly 在本进程内执行，
 * 不启动子进程，因此不受沙箱「禁止管道 stdio」的限制。
 *
 * 由 scripts/esbuild-loader.mjs 在 Node loader 层把说明符 'esbuild' 重定向到这里。
 * Vite 需要 default + 具名导出（transform / build / formatMessages / context ...）。
 *
 * 注意：js/wasm 构建按 POSIX 路径语义运行，而 Windows 的 Node 会给出
 * `E:\a\b` 形式路径。这里把 esbuild 的构建/上下文选项与插件回调结果统一
 * 归一化为正斜杠路径，避免 "not an absolute path" 之类错误。
 */
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);

const browserEntry = require.resolve('esbuild/lib/browser.js');
const browserModule = await import(pathToFileURL(browserEntry).href);
const esbuild = browserModule.default ?? browserModule;

const normalizePath = (value) => (typeof value === 'string' ? value.replace(/\\/g, '/') : value);

function normalizeEntryPoints(entryPoints) {
  if (typeof entryPoints === 'string') return normalizePath(entryPoints);
  if (Array.isArray(entryPoints)) return entryPoints.map(normalizePath);
  if (entryPoints && typeof entryPoints === 'object') {
    const next = {};
    for (const [key, value] of Object.entries(entryPoints)) {
      next[normalizePath(key)] = normalizePath(value);
    }
    return next;
  }
  return entryPoints;
}

/** 包装插件，把 onResolve / onLoad 返回结果里的 Windows 路径归一化。 */
function wrapPlugin(plugin) {
  if (!plugin || typeof plugin.setup !== 'function') return plugin;
  return {
    ...plugin,
    setup(build) {
      const proxy = new Proxy(build, {
        get(target, property) {
          if (property === 'onResolve') {
            return (filter, callback) =>
              target.onResolve(filter, async (args) => {
                const result = await callback(args);
                if (result && typeof result.path === 'string') {
                  return { ...result, path: normalizePath(result.path) };
                }
                return result;
              });
          }
          if (property === 'onLoad') {
            return (filter, callback) =>
              target.onLoad(filter, async (args) => {
                const result = await callback(args);
                if (result && typeof result.resolveDir === 'string') {
                  return { ...result, resolveDir: normalizePath(result.resolveDir) };
                }
                return result;
              });
          }
          const value = Reflect.get(target, property, target);
          return typeof value === 'function' ? value.bind(target) : value;
        },
      });
      plugin.setup(proxy);
    },
  };
}

function normalizeBuildOptions(options) {
  if (!options || typeof options !== 'object') return options;
  const next = { ...options };
  if (next.absWorkingDir) next.absWorkingDir = normalizePath(next.absWorkingDir);
  if (next.entryPoints) next.entryPoints = normalizeEntryPoints(next.entryPoints);
  if (next.outdir) next.outdir = normalizePath(next.outdir);
  if (next.outfile) next.outfile = normalizePath(next.outfile);
  if (next.tsconfig) next.tsconfig = normalizePath(next.tsconfig);
  if (next.stdin && typeof next.stdin === 'object' && next.stdin.resolveDir) {
    next.stdin = { ...next.stdin, resolveDir: normalizePath(next.stdin.resolveDir) };
  }
  if (Array.isArray(next.plugins)) next.plugins = next.plugins.map(wrapPlugin);
  return next;
}

export const initialize = (...args) => esbuild.initialize(...args);
export const transform = (input, options) => esbuild.transform(input, options);
export const build = (options) => esbuild.build(normalizeBuildOptions(options));
export const context = (options) => esbuild.context(normalizeBuildOptions(options));
export const formatMessages = (...args) => esbuild.formatMessages(...args);
export const analyzeMetafile = (...args) => esbuild.analyzeMetafile(...args);
export const stop = (...args) => esbuild.stop(...args);
export const version = esbuild.version;
export default esbuild;
