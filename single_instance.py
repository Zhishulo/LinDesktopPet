"""单实例守卫：不管双击几次 exe，电脑上只会有一只麟。

用户反馈（2026-11-05）：「多次启动 exe 文件会出现多个桌宠」。
根因：main() 每次都新建一个 PetWindow —— 双击几次就有几只麟，托盘里也一堆图标。

做法：QLocalServer / QLocalSocket（Windows 下底层是命名管道，Qt 自带，不引入新依赖）
- 先到的进程 listen(NAME) 成功，成为唯一实例；
- 后来的进程连上 NAME、发一行 "show"，然后自己退出（不建窗口、不装托盘）；
- 唯一实例收到 "show" 就把窗口 show + raise 到最前（被托盘隐藏时也先显示）。

健壮性（宁可多开，也不能打不开）：
- 上一个进程崩溃留下的管道名：listen 前先 removeServer() 清掉；
- listen 失败时再试一次 connect：连上了说明真有一只在跑，就退出；
- 仍失败（权限/名字被占）就放行本进程，绝不因守卫本身让麟启动不了；
- 本模块不碰任何窗口状态，激活动作由调用方用 set_activate() 注入。
"""

import os
import sys

from PyQt5.QtCore import QTimer
from PyQt5.QtNetwork import QLocalServer, QLocalSocket

# 命名管道名：不要用反斜杠，也不要带路径（Windows 的 \.pipe 前缀由 Qt 自己加）
SERVER_NAME = "LinDesktopPet_Lin_single_v1"

_server = None          # 唯一实例才有的 QLocalServer（模块级引用，防 GC）
_on_activate = None     # 唯一的「把麟叫到最前」回调


def set_activate(cb):
    """注册激活回调（main() 建好窗口、show 之后调用）。"""
    global _on_activate
    _on_activate = cb


def _activate():
    cb = _on_activate
    if cb is None:
        return
    try:
        cb()
    except Exception as e:
        print("[single_instance] activate:", e)


def _handle_connection(conn):
    """处理第二个进程的连接：读 "show" 并把麟叫到最前（只处理一次）。"""
    if getattr(conn, "_si_done", False):
        return
    try:
        conn._si_done = True
    except Exception:
        pass
    try:
        data = bytes(conn.readAll()).decode("utf-8", "ignore")
    except Exception:
        data = ""
    # 第二个进程可能连上就退出（没来得及写）：也当作「把麟叫到最前」
    if not data or "show" in data:
        _activate()
    try:
        conn.disconnectFromServer()
    except Exception:
        pass


def _notify_existing():
    """已有实例在跑？有就通知它、返回 True。"""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(800):
        try:
            sock.abort()
        except Exception:
            pass
        return False
    try:
        sock.write(b"show\n")
        sock.flush()
        sock.waitForBytesWritten(500)
    except Exception:
        pass
    try:
        sock.disconnectFromServer()
    except Exception:
        pass
    return True


def try_acquire():
    """True = 本进程唯一，可以建窗口；False = 已有实例（已通知它），本进程直接退出。"""
    global _server
    if _notify_existing():
        return False
    try:
        QLocalServer.removeServer(SERVER_NAME)   # 清理上次崩溃残留的管道
    except Exception:
        pass
    srv = QLocalServer()
    try:
        srv.setSocketOptions(QLocalServer.UserAccessOption)
    except Exception:
        pass
    if not srv.listen(SERVER_NAME):
        # listen 失败：再确认一次是不是刚好有人先监听了（竞态）
        if _notify_existing():
            return False
        print("[single_instance] listen failed:", srv.errorString())
        return True      # 兜底放行：绝不因为单实例守卫让麟启动不了

    def _on_new_connection():
        while srv.hasPendingConnections():
            conn = srv.nextPendingConnection()
            if conn is None:
                continue
            conn.readyRead.connect(lambda c=conn: _handle_connection(c))
            conn.disconnected.connect(conn.deleteLater)
            # 有的平台数据随连接一起到、不一定触发 readyRead，补读一次
            QTimer.singleShot(50, lambda c=conn: _handle_connection(c))

    srv.newConnection.connect(_on_new_connection)
    _server = srv
    return True
