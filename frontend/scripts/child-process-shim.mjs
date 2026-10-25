/**
 * node:child_process 垫片（仅由 scripts/esbuild-loader.mjs 在构建期注册）。
 *
 * Vite 在 Windows 上会用 `exec("net use")` 探测网络驱动器（windowsSafeRealPathSync）；
 * 沙箱禁止 Node 以管道 stdio 启动子进程，该调用会同步抛 EPERM 并中断构建。
 * 这里把这条探测改为异步报错（Vite 会按「无网络驱动器」继续），其余调用透传。
 */
import * as real from 'child_process';

const NET_USE_RE = /^\s*net\s+use\b/i;

export function exec(command, options, callback) {
  const cb = typeof options === 'function' ? options : callback;
  if (typeof command === 'string' && NET_USE_RE.test(command)) {
    if (typeof cb === 'function') {
      process.nextTick(() => {
        const error = new Error('EPERM: child_process spawn is disabled in this sandbox');
        error.code = 'EPERM';
        cb(error, '', '');
      });
    }
    return undefined;
  }
  return real.exec(command, options, callback);
}

export const spawn = real.spawn;
export const spawnSync = real.spawnSync;
export const execFile = real.execFile;
export const execFileSync = real.execFileSync;
export const execSync = real.execSync;
export const fork = real.fork;
export const ChildProcess = real.ChildProcess;
export default real;
