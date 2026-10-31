"""User-scoped Windows DPAPI storage; secrets never enter settings/documents."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import tempfile


class CredentialError(RuntimeError):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise CredentialError("此平台请使用 DEEPSEEK_API_KEY 环境变量提供密钥。")
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    incoming = _Blob(len(data), buffer)
    outgoing = _Blob()
    crypto = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        function = crypto.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        success = function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))
    else:
        function = crypto.CryptProtectData
        function.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        success = function(ctypes.byref(incoming), "DeepSeek API Key", None, None, None, 1,
                           ctypes.byref(outgoing))
    if not success:
        raise CredentialError("DeepSeek 密钥加密/读取失败，请重新在设置中填写。")
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(ctypes.cast(outgoing.data, ctypes.c_void_p))


def load_deepseek_key(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return _crypt(path.read_bytes(), decrypt=True).decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise CredentialError("已保存的 DeepSeek 密钥无法读取，请在设置中重新填写。") from error


def save_deepseek_key(path: Path, key: str):
    if not key:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise CredentialError("已保存的 DeepSeek 密钥无法清除，请检查目录权限。") from error
        return
    encrypted = _crypt(key.encode("utf-8"), decrypt=False)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as error:
        raise CredentialError("DeepSeek 密钥保存失败，请检查目录权限。") from error
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
