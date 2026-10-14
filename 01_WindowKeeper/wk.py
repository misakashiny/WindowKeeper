# -*- coding: utf-8 -*-
"""wk 命令行的入口脚本。安装后用 `wk` 或 `python -m windowkeeper` 调用。"""

import sys

from windowkeeper.cli import main

if __name__ == "__main__":
    sys.exit(main())
