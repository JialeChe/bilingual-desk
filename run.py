"""Launch the native desktop app. No browser or HTTP server."""

import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description="翻译对照 · 本地 Codex Luna")
    parser.add_argument("--check-codex", action="store_true", help="检查本地订阅登录和 Luna 可用性")
    args = parser.parse_args()
    if args.check_codex:
        from backend.providers.codex import CodexEngine
        engine = CodexEngine(timeout=30)
        try:
            print(engine.connect())
        finally:
            engine.close()
        return
    import tkinter as tk
    from ui.window import MainWindow
    if getattr(sys, "frozen", False):
        data_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "BilingualDesktop"
    else:
        data_dir = Path(__file__).resolve().parent / "data"
    root = tk.Tk()
    MainWindow(root, data_dir)
    root.mainloop()


if __name__ == "__main__":
    main()
