"""Launch the distributed EXE with isolated user data, then close it normally."""

import ctypes
import argparse
from ctypes import wintypes
import json
from pathlib import Path
import os
import subprocess
import time
from uuid import uuid4
import zipfile


def main():
    project = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, default=project / "dist" / "翻译对照-Windows-x64.zip")
    archive = parser.parse_args().archive
    directory = project / "build" / "smoke" / uuid4().hex[:8]
    portable = directory / "portable"
    portable.mkdir(parents=True)
    with zipfile.ZipFile(archive) as package:
        expected = {"翻译对照.exe", "使用说明.txt"}
        if set(package.namelist()) != expected:
            raise AssertionError("Unexpected release archive contents")
        for name in expected:
            (portable / name).write_bytes(package.read(name))
    executable = portable / "翻译对照.exe"
    appdata = directory / "appdata"
    data = appdata / "BilingualDesktop"
    data.mkdir(parents=True)
    (data / "settings.json").write_text(json.dumps({"auto": False}), encoding="utf-8")
    environment = dict(os.environ, LOCALAPPDATA=str(appdata), DEEPSEEK_API_KEY="")
    # The frozen runtime supplies its own Python/Tcl/Tk paths.
    for name in ("PYTHONPATH", "PYTHONHOME", "TCL_LIBRARY", "TK_LIBRARY"):
        environment.pop(name, None)

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                  wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def windows():
        result = []

        @callback_type
        def visit(hwnd, _):
            title = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, title, len(title))
            if title.value == "翻译对照":
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                handle = kernel32.OpenProcess(0x1000, False, pid.value)
                if handle:
                    try:
                        path = ctypes.create_unicode_buffer(32768)
                        length = wintypes.DWORD(len(path))
                        if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
                            if Path(path.value).resolve() == executable.resolve():
                                result.append(hwnd)
                    finally:
                        kernel32.CloseHandle(handle)
            return True

        user32.EnumWindows(visit, 0)
        return result

    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    process = subprocess.Popen([str(executable)], cwd=portable, env=environment,
                               startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
    hwnd = None
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            candidates = windows()
            if candidates:
                hwnd = candidates[0]
                user32.ShowWindow(hwnd, 0)
                break
            if process.poll() is not None:
                raise AssertionError(f"EXE exited before creating its window: {process.returncode}")
            time.sleep(0.1)
        if hwnd is None:
            raise AssertionError("The packaged main window did not appear within 30 seconds")
        # Allow startup callbacks to complete, with automatic translation disabled.
        time.sleep(1)
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE: use the application's save/shutdown path.
        returncode = process.wait(timeout=15)
        if returncode != 0:
            raise AssertionError(f"EXE did not exit cleanly: {returncode}")
        draft = json.loads((data / "draft.json").read_text(encoding="utf-8"))
        if draft.get("format_version") != 2 or draft.get("source") != "":
            raise AssertionError("The packaged app did not save its isolated draft correctly")
        records = [json.loads(line) for line in (data / "debug.jsonl").read_text(encoding="utf-8").splitlines()]
        errors = [record for record in records if record.get("level") == "ERROR"]
        startup_record = next(record for record in records if record.get("event") == "应用启动")
        if "DeepSeek" not in startup_record.get("details", {}).get("provider", ""):
            raise AssertionError("A fresh installation must default to DeepSeek API")
        if errors:
            raise AssertionError(f"The packaged app recorded errors: {errors}")
        report = {"archive_contents": sorted(expected), "window_created": True,
                  "normal_close": True, "draft_saved": True, "default_provider": "deepseek", "errors": 0,
                  "executable_bytes": executable.stat().st_size}
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        if process.poll() is None:
            if hwnd is not None:
                user32.PostMessageW(hwnd, 0x0010, 0, 0)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               creationflags=subprocess.CREATE_NO_WINDOW, capture_output=True)


if __name__ == "__main__":
    main()
