#!/usr/bin/env node
/**
 * 离线绿色版（portable）打包兜底脚本 —— 不依赖 electron-builder。
 *
 * 背景：electron-builder 在受限沙箱里会用管道 stdio 拉起 app-builder.exe / 7za.exe，
 * 触发 spawn EPERM；同时它还需要联网下载 winCodeSign / nsis / 7za 等二进制。
 * 本脚本只用 Node 的 fs 复制（不创建管道），因此可在沙箱内完成打包。
 *
 * 产物：
 *   release/win-unpacked/                        可双击 PicturePick.exe 运行的绿色目录
 *   release/PicturePick-<version>-win-x64-portable.zip   同上的 zip 压缩包
 *
 * 用法（在 desktop/ 目录）：
 *   node scripts/pack-portable.mjs            # 目录 + zip
 *   node scripts/pack-portable.mjs --no-zip   # 只产出目录
 *
 * 前置条件：
 *   1) desktop/node_modules/electron/dist 已存在（npm install 完成）
 *   2) desktop/dist 已存在（npm run build）
 *   3) E:\picturePick\build\backend 已存在（npm run pack:backend）；缺失时脚本会警告并继续，
 *      这样外壳仍可启动并显示错误页。
 */

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const desktopDir = path.resolve(scriptDir, '..');
const rootDir = path.resolve(desktopDir, '..');
const releaseDir = path.join(rootDir, 'release');
const unpackedDir = path.join(releaseDir, 'win-unpacked');
const electronDistDir = path.join(desktopDir, 'node_modules', 'electron', 'dist');
const appDistDir = path.join(desktopDir, 'dist');
const backendBuildDir = path.join(rootDir, 'build', 'backend');
const resourcesDir = path.join(desktopDir, 'resources');
const skipZip = process.argv.includes('--no-zip');

const pkg = JSON.parse(fs.readFileSync(path.join(desktopDir, 'package.json'), 'utf8'));
const version = pkg.version ?? '1.0.0';

function info(message) {
  console.log(`[pack-portable] ${message}`);
}

function fail(message) {
  console.error(`[pack-portable] 错误: ${message}`);
  process.exitCode = 1;
  throw new Error(message);
}

function copyDir(from, to) {
  fs.cpSync(from, to, { recursive: true, force: true, dereference: false });
}

function dirSizeBytes(dir) {
  let total = 0;
  const stack = [dir];
  while (stack.length > 0) {
    const current = stack.pop();
    const entries = fs.readdirSync(current, { withFileTypes: true });
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.isFile()) total += fs.statSync(full).size;
    }
  }
  return total;
}

function formatSize(bytes) {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function main() {
  info(`PicturePick portable 打包 v${version}`);
  info(`工作区: ${rootDir}`);

  if (!fs.existsSync(electronDistDir)) {
    fail(`找不到 Electron 运行时: ${electronDistDir}（请先执行 npm install）`);
  }
  if (!fs.existsSync(path.join(appDistDir, 'main.js'))) {
    fail(`找不到编译产物: ${path.join(appDistDir, 'main.js')}（请先执行 npm run build）`);
  }

  // 1) 清空并复制 Electron 运行时
  fs.rmSync(unpackedDir, { recursive: true, force: true });
  fs.mkdirSync(unpackedDir, { recursive: true });
  info('复制 Electron 运行时 → release/win-unpacked ...');
  copyDir(electronDistDir, unpackedDir);

  // 2) 重命名 electron.exe → PicturePick.exe
  const electronExe = path.join(unpackedDir, 'electron.exe');
  const appExe = path.join(unpackedDir, 'PicturePick.exe');
  if (!fs.existsSync(electronExe)) fail(`Electron 运行时缺少 electron.exe: ${electronExe}`);
  fs.renameSync(electronExe, appExe);
  info('electron.exe → PicturePick.exe');

  // 3) 写入 resources/app（Electron 会优先加载 resources/app）
  const appDir = path.join(unpackedDir, 'resources', 'app');
  fs.rmSync(appDir, { recursive: true, force: true });
  fs.mkdirSync(appDir, { recursive: true });
  copyDir(appDistDir, path.join(appDir, 'dist'));

  const appPkg = {
    name: pkg.name,
    productName: 'PicturePick',
    version,
    description: pkg.description,
    main: 'dist/main.js',
    private: true,
  };
  fs.writeFileSync(path.join(appDir, 'package.json'), `${JSON.stringify(appPkg, null, 2)}\n`, 'utf8');
  info('写入 resources/app/package.json（main = dist/main.js）');

  // 主进程图标（开发模式会从 resources/icon.ico 读取）
  const iconSrc = path.join(resourcesDir, 'icon.ico');
  const iconDst = path.join(unpackedDir, 'resources', 'icon.ico');
  if (fs.existsSync(iconSrc)) {
    fs.copyFileSync(iconSrc, iconDst);
    info('复制 icon.ico → resources/');
  } else {
    info('警告: desktop/resources/icon.ico 不存在，跳过图标');
  }

  // 4) 后端 sidecar → resources/backend
  const backendDst = path.join(unpackedDir, 'resources', 'backend');
  fs.rmSync(backendDst, { recursive: true, force: true });
  const backendExe = path.join(backendBuildDir, 'picturepick-backend.exe');
  if (fs.existsSync(backendExe)) {
    info('复制 PyInstaller 后端 → resources/backend ...');
    copyDir(backendBuildDir, backendDst);
  } else {
    console.warn(
      `[pack-portable] 警告: 未找到 ${backendExe}\n` +
        '[pack-portable] 先执行 npm run pack:backend 再重新打包；本次产物的应用会显示“后端不存在”错误页。',
    );
  }

  // 5) 自检：关键文件存在
  const required = [
    appExe,
    path.join(appDir, 'package.json'),
    path.join(appDir, 'dist', 'main.js'),
    path.join(appDir, 'dist', 'preload.js'),
  ];
  for (const file of required) {
    if (!fs.existsSync(file)) fail(`产物自检失败，缺少文件: ${file}`);
  }
  const hasBackend = fs.existsSync(path.join(backendDst, 'picturepick-backend.exe'));
  info(`产物自检通过（后端 sidecar: ${hasBackend ? '已内置' : '缺失'}）`);

  const totalSize = dirSizeBytes(unpackedDir);
  info(`绿色目录大小: ${formatSize(totalSize)}`);

  // 6) zip 压缩（PowerShell Compress-Archive，避免额外依赖）
  if (!skipZip) {
    const zipPath = path.join(releaseDir, `PicturePick-${version}-win-x64-portable.zip`);
    fs.rmSync(zipPath, { force: true });
    info(`压缩 → ${zipPath} ...`);
    const command =
      `Compress-Archive -Path '${unpackedDir.replace(/'/g, "''")}\\*' ` +
      `-DestinationPath '${zipPath.replace(/'/g, "''")}' -CompressionLevel Optimal -Force`;
    const result = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', command], {
      stdio: 'inherit',
    });
    if (result.error) {
      console.warn(`[pack-portable] 警告: 无法启动 powershell 压缩 zip: ${String(result.error)}`);
    } else if (result.status !== 0) {
      console.warn(`[pack-portable] 警告: zip 压缩失败 exit=${result.status}，绿色目录仍然可用`);
    } else if (fs.existsSync(zipPath)) {
      info(`zip 已完成: ${zipPath}（${formatSize(fs.statSync(zipPath).size)}）`);
    }
  } else {
    info('已指定 --no-zip，跳过压缩');
  }

  info('完成。运行方式: release\\win-unpacked\\PicturePick.exe');
}

main();
