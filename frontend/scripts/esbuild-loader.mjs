/**
 * Node ESM loader：构建期把两个说明符重定向到垫片。
 *  - 'esbuild'            → 进程内 WASM 版（scripts/esbuild-shim.mjs）
 *  - 'node:child_process' → 屏蔽 Vite 的 `net use` 探测（scripts/child-process-shim.mjs）
 * 仅在 scripts/vite-wasm.mjs 中通过 module.register() 注册。
 */
const ESBUILD_SHIM_URL = new URL('./esbuild-shim.mjs', import.meta.url).href;
const CHILD_PROCESS_SHIM_URL = new URL('./child-process-shim.mjs', import.meta.url).href;

export function resolve(specifier, context, nextResolve) {
  if (specifier === 'esbuild') {
    return { url: ESBUILD_SHIM_URL, shortCircuit: true };
  }
  if (specifier === 'node:child_process') {
    return { url: CHILD_PROCESS_SHIM_URL, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}
