/**
 * 全局类型声明：由 preload 注入到渲染进程的桌面桥接 API。
 */
export interface PicturePickDesktopApi {
  /** 固定为 true，前端用它判断是否运行在 Electron 外壳中。 */
  readonly isDesktop: true;
  /** 打开原生目录选择框；用户取消返回 null。 */
  pickDirectory(): Promise<string | null>;
  /** 打开原生“另存为”对话框并写入文本内容；用户取消返回 null，成功返回保存路径。 */
  saveJson(defaultName: string, content: string): Promise<string | null>;
  /** 用系统默认程序打开路径，返回空串表示成功，否则为错误描述。 */
  openPath(target: string): Promise<string>;
}

declare global {
  interface Window {
    picturePickDesktop?: PicturePickDesktopApi;
  }
}

export {};
