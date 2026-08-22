"""讓 scripts/*.py 能直接執行時找到 app 套件，並修正 Windows 主控台編碼。"""
import sys
from pathlib import Path

# Windows 主控台預設 cp950，無法輸出 ✓ 等字元；統一改 UTF-8，避免排程/雙擊執行時崩潰。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
