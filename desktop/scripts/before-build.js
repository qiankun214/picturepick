/**
 * electron-builder `beforeBuild` hook.
 *
 * 本应用没有运行时 node 依赖：主进程/preload 全部由 tsc 编译进 dist/，
 * 不需要 electron-builder 去 `npm install/rebuild` 或收集生产 node_modules。
 *
 * 返回 false 明确告诉 electron-builder「node_modules 由我们自己处理」，
 * 从而跳过：
 *   1) npm install/rebuild；
 *   2) 用管道 stdio 调 `npm list` 收集生产依赖（受限沙箱下会 spawn EPERM）。
 *
 * electron-builder 会跳过 npm 生命周期脚本，只用 files 里声明的 dist/** + package.json 组成 app.asar。
 */
module.exports = async function beforeBuild() {
  return false;
};
