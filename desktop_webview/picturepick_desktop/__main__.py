"""PicturePick 桌面版入口（PyInstaller / python -m picturepick_desktop）。

注意：PyInstaller 会把入口脚本作为顶层 ``__main__`` 执行，因此这里必须使用绝对导入
（``from picturepick_desktop.desktop import main``），不能写相对导入。
"""

from picturepick_desktop.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
