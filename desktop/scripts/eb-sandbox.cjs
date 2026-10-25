#!/usr/bin/env node
/**
 * electron-builder 沙箱包装器。
 *
 * 问题：DSH 沙箱禁止创建命名管道。electron-builder 打包安装包时：
 *   1) 用 `builder-util.exec()` → `child_process.execFile`（管道 stdio）调用 7za.exe；
 *   2) 用 `builder-util.spawnAndWriteWithOutput()`（管道 stdio）调用 makensis.exe，
 *      并把 NSIS 脚本写到 stdin（makensis 参数以 "-" 结尾表示从 stdin 读脚本）。
 * 两者在沙箱内都会 `spawn EPERM`。
 *
 * 方案：
 *   - 把 child_process.execFile 替换成「stdio 重定向到临时文件，退出后读回输出」的实现；
 *   - 把 builder-util 的 spawnAndWriteWithOutput / spawnAndWrite 替换成
 *     「脚本先落临时文件，再把参数里的 "-" 换成该文件路径」的实现（makensis 语义等价）。
 *
 * 用法（先 npm install、npm run pack:backend）：
 *   node scripts/eb-sandbox.cjs --dir --config electron-builder.yml
 *   node scripts/eb-sandbox.cjs --config electron-builder.yml          # portable + nsis
 *
 * 普通（非沙箱）环境不需要本包装器，直接 `npm run dist`。
 */
const cp = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const tmpDir = path.join(os.tmpdir(), 'picturepick-eb-exec');
fs.mkdirSync(tmpDir, { recursive: true });

const originalSpawn = cp.spawn.bind(cp);

function tempFile(prefix) {
  return path.join(tmpDir, `${prefix}-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}.log`);
}

function readAndCleanup(file) {
  let text = '';
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch {
    /* ignore */
  }
  try {
    fs.rmSync(file, { force: true });
  } catch {
    /* ignore */
  }
  return text;
}

/* ------------------------------------------------------------------ */
/* 1) execFile → 文件描述符重定向                                      */
/* ------------------------------------------------------------------ */
const originalExecFile = cp.execFile.bind(cp);

cp.execFile = function execFileWithoutPipes(file, args, options, callback) {
  if (typeof args === 'function') {
    callback = args;
    args = [];
    options = {};
  } else if (typeof options === 'function') {
    callback = options;
    options = {};
  }
  args = args || [];
  options = options || {};

  const logFile = tempFile('exec');
  const fd = fs.openSync(logFile, 'a');
  let child;
  try {
    child = originalSpawn(file, args, {
      cwd: options.cwd,
      env: options.env,
      windowsHide: true,
      stdio: ['ignore', fd, fd],
    });
  } finally {
    try {
      fs.closeSync(fd);
    } catch {
      /* ignore */
    }
  }

  let settled = false;
  const finish = (error) => {
    if (settled) return;
    settled = true;
    const text = readAndCleanup(logFile);
    if (callback) callback(error, text, text);
  };
  child.once('error', (error) => finish(error));
  child.once('close', (code, signal) => {
    if (code === 0) finish(null);
    else finish(new Error(`Command failed: ${file} ${args.join(' ')} (exit ${code}, signal ${signal})`));
  });
  return child;
};

/* ------------------------------------------------------------------ */
/* 2) builder-util 的 spawnAndWrite* → stdin 落临时文件                */
/* ------------------------------------------------------------------ */
const builderUtil = require('../node_modules/builder-util');
const { ExecError } = builderUtil;

function runWithStdinFile(command, args, data, options) {
  return new Promise((resolve, reject) => {
    // NSIS 的 !include 相对路径是相对「脚本文件所在目录」解析的；
    // 把脚本放到调用方 cwd（nsisTemplatesDir）下，才能找到 common.nsh 等模板。
    const cwd = options && options.cwd;
    const scriptDir = cwd && fs.existsSync(cwd) ? cwd : tmpDir;
    const stdinFile = path.join(scriptDir, `.eb-stdin-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}.nsh`);
    const stdoutFile = tempFile('stdout');
    const stderrFile = tempFile('stderr');
    fs.writeFileSync(stdinFile, data == null ? '' : String(data), 'utf8');

    const isNsis = /makensis/i.test(path.basename(command));
    const finalArgs = args.map((arg) => (isNsis && arg === '-' ? stdinFile : arg));

    const outFd = fs.openSync(stdoutFile, 'a');
    const errFd = fs.openSync(stderrFile, 'a');
    let child;
    try {
      child = originalSpawn(command, finalArgs, {
        cwd: options && options.cwd,
        env: options && options.env,
        windowsHide: true,
        stdio: ['ignore', outFd, errFd],
      });
    } finally {
      try {
        fs.closeSync(outFd);
        fs.closeSync(errFd);
      } catch {
        /* ignore */
      }
    }

    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      try {
        child.kill();
      } catch {
        /* ignore */
      }
    }, 4 * 60 * 1000);

    child.once('error', (error) => {
      clearTimeout(timeout);
      fs.rmSync(stdinFile, { force: true });
      readAndCleanup(stdoutFile);
      readAndCleanup(stderrFile);
      reject(error);
    });
    child.once('close', (code) => {
      clearTimeout(timeout);
      fs.rmSync(stdinFile, { force: true });
      const stdout = readAndCleanup(stdoutFile);
      const stderr = readAndCleanup(stderrFile);
      if (timedOut) reject(new Error(`${command} timed out after 4 minutes`));
      else if (code === 0) resolve({ stdout, stderr });
      else reject(new ExecError(command, code == null ? -1 : code, stdout, stderr));
    });
  });
}

builderUtil.spawnAndWriteWithOutput = (command, args, data, options) => runWithStdinFile(command, args, data, options);
builderUtil.spawnAndWrite = (command, args, data, options) => runWithStdinFile(command, args, data, options).then(() => undefined);

// 加载 electron-builder 的 CLI（此时补丁已就位）。
require('../node_modules/electron-builder/cli.js');
