from __future__ import annotations

import sys

from .imx_cli import main as imx_main


def main(argv: list[str] | None = None) -> int:
    print("[deprecated] 请使用新命令: imx ...")
    return imx_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

