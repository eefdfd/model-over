# -*- coding: utf-8 -*-
"""
项目统一入口（整合 ``run_model`` 与 ``trend_up_screen_loose``）。

**默认**不带子命令时 = 原 ``run_model.py``（跨市场联动；跑完后**默认**再生成 ``outputs/运行日/`` 下的 ``trend_up_loose_结果_按趋势强度.xlsx``，可用 ``--skip-trend-up-screen`` 跳过）::

  python model.py
  python model.py --start-date 2024-01-01

子命令::

  python model.py trend-up
  python model.py trend-up --limit 50

``regime`` 可显式写出（与默认相同）::

  python model.py regime --output-dir outputs
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def _usage() -> None:
    print(
        "用法:\n"
        "  python model.py              # 默认：跨市场模型（同 run_model.py）\n"
        "  python model.py [run_model 参数…]\n"
        "  python model.py trend-up [trend_up_screen_loose 参数…]\n"
        "  python model.py regime [run_model 参数…]  # 与默认相同\n"
        "示例:\n"
        "  python model.py --end-date 2025-12-31\n"
        "  python model.py trend-up --limit 10\n",
        end="",
    )


def main() -> None:
    argv = sys.argv[1:]

    if not argv:
        sys.argv = [sys.argv[0]]
        from run_model import main as regime_main

        regime_main()
        return

    if argv[0] in ("-h", "--help", "help"):
        _usage()
        sys.exit(0)

    if argv[0] in ("trend-up", "trend_up"):
        sys.argv = [sys.argv[0]] + argv[1:]
        from trend_up_screen_loose import main as trend_main

        trend_main()
        return

    if argv[0] == "regime":
        sys.argv = [sys.argv[0]] + argv[1:]
        from run_model import main as regime_main

        regime_main()
        return

    # 默认：无子命令名时，整段参数交给 run_model
    sys.argv = [sys.argv[0]] + argv
    from run_model import main as regime_main

    regime_main()


if __name__ == "__main__":
    main()
