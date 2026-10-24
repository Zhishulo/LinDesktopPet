# -*- coding: utf-8 -*-
"""全局异常兜底：避免 PyQt 槽函数里一个未捕获异常就让整个桌宠进程消失。

背景：PyQt5 5.5+ 在槽函数抛出未捕获异常时，若 sys.excepthook 是默认实现，
会走 qFatal/abort，进程直接退出（Windows 退出码 0xC0000409），
用户看到的就是「桌宠突然自己没了」。安装自定义 excepthook 之后，
异常只被记录（crash.log + stderr），进程继续活着。
"""
import os
import sys
import time
import traceback
import threading

_installing = False
_inside = False
_original = None
_recover = None
_log_path = None


def _write_log(text):
    if not _log_path:
        return
    try:
        d = os.path.dirname(_log_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(_log_path, "a", encoding="utf-8") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")
    except Exception:
        pass


def _format(exc_type, exc, tb):
    return "[{}] [thread:{}] {}\n{}".format(
        time.strftime("%Y-%m-%d %H:%M:%S"),
        threading.current_thread().name,
        exc_type.__name__ if exc_type else "Exception",
        "".join(traceback.format_exception(exc_type, exc, tb)),
    )


def _hook(exc_type, exc, tb):
    global _inside
    if _inside:          # 写日志/恢复回调自身再出错时不要递归
        return
    _inside = True
    try:
        if exc_type is not None and issubclass(exc_type, KeyboardInterrupt):
            if _original:
                _original(exc_type, exc, tb)
            return
        text = _format(exc_type, exc, tb)
        _write_log(text)
        try:
            stream = sys.stderr or getattr(sys, "__stderr__", None)
            if stream is not None:
                stream.write(text)
                stream.flush()
        except Exception:
            pass
        if _recover is not None:
            try:
                _recover(exc)
            except Exception:
                pass
    finally:
        _inside = False


def _thread_hook(args):
    if args.exc_type is SystemExit:
        return
    _hook(args.exc_type, args.exc_value, args.exc_traceback)


def install(log_path=None, on_error=None):
    """安装兜底。log_path 默认程序目录下 crash.log；on_error(exc) 用于复位界面状态。"""
    global _original, _recover, _log_path, _installing
    if log_path is None:
        log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash.log")
    _recover = on_error
    if _installing:
        if log_path:
            _log_path = log_path
        return _log_path
    _installing = True
    _log_path = log_path
    _original = sys.excepthook
    sys.excepthook = _hook
    try:
        threading.excepthook = _thread_hook
    except Exception:
        pass
    return _log_path
