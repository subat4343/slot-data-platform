"""run-collect.ps1 のテスト用に slot-data の代わりに動くプログラム。

標準エラーに INFO ログ（日本語を含む）を複数行、標準出力に JSON を出し、指定の終了コードで終わる。
"""

import json
import sys

args = sys.argv[1:]
for i in range(3):
    print(f"07:29:{i:02d} INFO    parsed 台番号=0277 [{i}]", file=sys.stderr, flush=True)
print(json.dumps({"command": args[1] if len(args) > 1 else None, "status": "succeeded"}, ensure_ascii=False))
sys.exit(3 if "collect" in args else 0)
