/**
 * PicturePick 桌面端 preload。
 *
 * 只暴露白名单 API，渲染进程无 Node 权限（contextIsolation=true, nodeIntegration=false）。
 * 前端通过 `window.picturePickDesktop` 判断是否运行在桌面外壳中。
 */
import { contextBridge, ipcRenderer } from 'electron';

const api = {
  isDesktop: true as const,

  pickDirectory(): Promise<string | null> {
    return ipcRenderer.invoke('picturepick:pick-directory') as Promise<string | null>;
  },

  saveJson(defaultName: string, content: string): Promise<string | null> {
    return ipcRenderer.invoke('picturepick:save-json', defaultName, content) as Promise<string | null>;
  },

  openPath(target: string): Promise<string> {
    return ipcRenderer.invoke('picturepick:open-path', target) as Promise<string>;
  },
};

contextBridge.exposeInMainWorld('picturePickDesktop', api);
