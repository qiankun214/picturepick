"""PicturePick 后端 sidecar 的 PyInstaller 入口。

等价于 `python -m picturepick`：把控制权交给 picturepick.cli.main。
使用薄入口而不是直接分析 cli.py，是为了避免相对导入在“顶层脚本”模式下失败。
"""

import os
import sys


def main() -> int:
    # 非冻结运行时（例如直接 python 本文件调试）把 backend 目录加入 sys.path。
    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    backend_dir = os.path.join(root_dir, "backend")
    if os.path.isdir(os.path.join(backend_dir, "picturepick")) and backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from picturepick.cli import main as cli_main

    return int(cli_main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
