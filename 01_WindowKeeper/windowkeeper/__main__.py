# -*- coding: utf-8 -*-
"""让 `python -m windowkeeper` 直接走命令行。"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
