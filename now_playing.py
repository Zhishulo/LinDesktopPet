# -*- coding: utf-8 -*-
"""麟桌宠 · 正在播放探测（now_playing）。

给桌面宠物「麟」判断「主人现在是不是在听歌」用，特点：
  * 首选 Windows SMTC（System Media Transport Controls，需 Win10 1809+）：
    通过可选的 PyWinRT 包读取系统媒体会话，能拿到歌名 / 歌手 / 专辑 / 播放器 /
    播放状态 / 播放进度，支持网易云、QQ 音乐、Spotify、浏览器标签页等一切
    接入了 SMTC 的播放器；
  * PyWinRT 不可用（没装 / 打包失败 / 系统太旧）时，退化用 ctypes 枚举窗口标题，
    只认已知播放器进程（网易云、QQ 音乐、Spotify、酷狗、foobar2000…）且标题里带
    「歌名 - 歌手」分隔符的情况，视为「弱证据」，backend() 会返回 "window"；浏览器
    （Chrome / Edge / Firefox…）标签页一律不在窗口后端解析——浏览器里的歌交给 SMTC
    （Chromium / Firefox 在 Win10 1809+ 会自己注册 SMTC），宁可返回 None 也不猜；
  * 只依赖标准库 + 可选 winrt 包，不依赖 Qt、不开常驻线程、不碰网络；
  * 任何异常都在模块内吞掉并返回降级值（None / False / ""），绝不弹窗、绝不外抛；
  * 本模块不做任何 print（自测除外），不会污染 stderr。

对外 API（v1，Lead 集成请按此调用，语义保持稳定）：
    INFO_VERSION     接口版本号
    is_available()   是否有可用后端（真正探测一次；不抛异常）-> bool
    backend()        当前使用的后端："smtc" / "window" / "none" -> str
    current_track()  正在播放的曲目信息 -> dict | None
    status_text()    中文一行描述，无信息时 "" -> str
    preload()        可选：提前初始化后端（约 100ms），方便主程序放到后台预热点

current_track() 返回字典（所有字符串字段保证是 str，可能为空串，绝不是 None）：
    {"title": str, "artist": str, "album": str, "app": str,
     "playing": bool, "position": float | None, "duration": float | None,
     "confidence": float}
  - playing=False 表示「有曲目但已暂停」，调用方仍可以拿来互动；
  - confidence 是 0~1 的可信度：SMTC = 1.0；窗口弱证据 0.5~0.7。主程序可据此决定
    是否展示、以及用「好像听到」这类弱化措辞（建议 < 0.6 只做轻互动、不断言）；
  - 完全没信息时返回 None（安静地什么都不做）。

线程安全：所有对外函数都可以在任意线程（主线程 / QThread worker / 普通 Python
thread）直接调用——内部用「线程本地 asyncio 事件循环」，不依赖 STA、不要求初始化
线程、不创建常驻线程、也不碰 Qt。preload() 可选，任意线程调一次即可（约 100ms）；
不调也会在首次 current_track() 时惰性初始化。多个线程并发调用是安全的。

耗时：后端初始化后（SMTC 已就绪 / 窗口扫描缓存命中），单次 current_track() 通常
在 1~5ms；首次调用要加载 winrt 并创建 media manager，约 80~150ms（只发生一次）。
需要严格 <50ms 的主程序请先调一次 preload()，或在启动阶段调一次 is_available()。

自测：  C:/LinVenv/Scripts/python.exe music/now_playing.py
"""

import ctypes
import os
import sys
import threading
import time

INFO_VERSION = 1

__all__ = [
    "INFO_VERSION",
    "is_available",
    "backend",
    "current_track",
    "status_text",
    "preload",
]

# ---------------------------------------------------------------------------
# 常量与映射
# ---------------------------------------------------------------------------

# SMTC 播放状态（GlobalSystemMediaTransportControlsSessionPlaybackStatus）
_STATUS_CLOSED = 0
_STATUS_OPENED = 1
_STATUS_CHANGING = 2
_STATUS_STOPPED = 3
_STATUS_PLAYING = 4
_STATUS_PAUSED = 5

# 播放器 exe（小写）或 SMTC AppUserModelId 后缀 -> 友好的中文名
_APP_LABELS = {
    "cloudmusic.exe": "网易云音乐",
    "qqmusic.exe": "QQ音乐",
    "spotify.exe": "Spotify",
    "kugou.exe": "酷狗音乐",
    "kugoumusic.exe": "酷狗音乐",
    "kwmusic.exe": "酷我音乐",
    "kuwo.exe": "酷我音乐",
    "foobar2000.exe": "foobar2000",
    "potplayermini64.exe": "PotPlayer",
    "potplayermini.exe": "PotPlayer",
    "potplayer.exe": "PotPlayer",
    "aimp.exe": "AIMP",
    "aimp64.exe": "AIMP",
    "musicbee.exe": "MusicBee",
    "wmplayer.exe": "Windows Media Player",
    "music.ui.exe": "媒体播放器",
    "microsoft.zunemusic_8wekyb3d8bbwe!microsoft.zunemusic": "媒体播放器",
    "msedge.exe": "Microsoft Edge",
    "chrome.exe": "Google Chrome",
    "firefox.exe": "Firefox",
    "brave.exe": "Brave",
    "opera.exe": "Opera",
    "operagx.exe": "Opera GX",
}

# 窗口标题里常见的「应用名」尾巴，解析时剔除
_APP_TITLE_WORDS = {
    "网易云音乐", "网易云音乐·x studio", "qq音乐", "spotify", "spotify free",
    "spotify premium", "酷狗音乐", "酷我音乐", "foobar2000", "musicbee",
    "potplayer", "aimp", "windows media player", "媒体播放器", "groove 音乐",
    "itunes", "music", "music player",
}

# 窗口弱证据的可信度（0~1）：主程序可据此决定「断言」还是「好像听到」
_CONF_SMTC = 1.0                  # 系统媒体会话，权威数据
_CONF_WINDOW_SONG_ARTIST = 0.7    # 已知播放器，「歌名 - 歌手」
_CONF_WINDOW_SONG_ARTIST_APP = 0.65   # 「歌名 - 歌手 - 播放器名」
_CONF_WINDOW_SONG_ONLY = 0.5      # 「歌名 - 播放器名」，只有歌名没有歌手

# 低于这个可信度的窗口解析结果直接丢弃（宁返回 None 也不返回垃圾）
_WINDOW_MIN_CONFIDENCE = 0.5

# 明显不是歌名的导航/页面词；命中就整条丢弃，避免把「本地音乐」「发现音乐」
# 当成歌名（播放器窗口标题在没播放时经常就是这些）
_NON_SONG_TITLES = {
    "发现音乐", "我的音乐", "本地音乐", "私人漫游", "每日推荐", "排行榜",
    "歌单", "搜索", "设置", "登录", "下载管理", "正在播放", "播放列表",
    "视频", "电台", "直播", "播客", "听歌识曲", "云盘", "关注", "动态",
    "消息", "评论", "关于", "更新", "新歌速递", "新建标签页", "新标签页",
    "个人", "主页", "首页", "我的收藏", "最近播放",
}

# 已知（非浏览器）播放器的 exe -> 中文名（窗口后端用）
_WINDOW_PLAYERS = {
    "cloudmusic.exe": "网易云音乐",
    "qqmusic.exe": "QQ音乐",
    "spotify.exe": "Spotify",
    "kugou.exe": "酷狗音乐",
    "kugoumusic.exe": "酷狗音乐",
    "kwmusic.exe": "酷我音乐",
    "kuwo.exe": "酷我音乐",
    "foobar2000.exe": "foobar2000",
    "potplayermini64.exe": "PotPlayer",
    "potplayermini.exe": "PotPlayer",
    "aimp.exe": "AIMP",
    "aimp64.exe": "AIMP",
    "musicbee.exe": "MusicBee",
    "wmplayer.exe": "Windows Media Player",
    "music.ui.exe": "媒体播放器",
}

# 浏览器 exe —— 只用于 _app_label() 的友好名称映射；
# 窗口后端不扫描浏览器窗口（标签页标题不可信，浏览器里的歌交给 SMTC）
_WINDOW_BROWSERS = {
    "msedge.exe": "Microsoft Edge",
    "chrome.exe": "Google Chrome",
    "firefox.exe": "Firefox",
    "brave.exe": "Brave",
    "opera.exe": "Opera",
    "operagx.exe": "Opera GX",
}

# 标题分隔符（不同播放器 / 语言环境不一样）
_TITLE_SEPS = (" - ", " – ", " — ", " — ", " | ", "｜", " -")

# 窗口扫描结果缓存秒数：主程序可能连续调用 is_available()+current_track()，
# 避免同一轮里重复 EnumWindows（SMTC 路径本身很便宜，不需要缓存）
_WINDOW_CACHE_SECONDS = 0.8

# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _clean_text(value):
    """把任意值整理成 str：去 NUL、去首尾空白、限制长度；异常返回 ""。"""
    try:
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        if "\x00" in value:
            value = value.split("\x00", 1)[0]
        value = value.strip()
        # 防止某个播放器给出超长标题（例如整段歌词）撑爆提示词
        if len(value) > 200:
            value = value[:200].rstrip()
        return value
    except Exception:
        return ""


def _app_label(app_id):
    """把 SMTC AppUserModelId / 窗口 exe 名转成友好名称；未知时返回去掉 .exe 的名字。"""
    raw = _clean_text(app_id)
    if not raw:
        return ""
    low = raw.lower()
    # 打包应用形如 Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic
    if "!" in low:
        low_tail = low.split("!", 1)[1]
        for key in (low, low_tail, low_tail + ".exe"):
            if key in _APP_LABELS:
                return _APP_LABELS[key]
        return _clean_text(low_tail)
    if low in _APP_LABELS:
        return _APP_LABELS[low]
    if low in _WINDOW_PLAYERS:
        return _WINDOW_PLAYERS[low]
    if low in _WINDOW_BROWSERS:
        return _WINDOW_BROWSERS[low]
    if low.endswith(".exe"):
        return raw[:-4]
    return raw


def _seconds(value):
    """timedelta / 数字 / None -> float 秒或 None。"""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return float(value)
        return float(value.total_seconds())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# SMTC 后端（PyWinRT，惰性导入）
# ---------------------------------------------------------------------------

_smtc_lock = threading.Lock()
_smtc_tried = False
_smtc_ok = False
_smtc_manager = None
_loop_local = threading.local()


def _get_loop():
    """每个线程一个 asyncio 事件循环（惰性创建，不常驻线程、不 set_event_loop）。"""
    import asyncio
    loop = getattr(_loop_local, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _loop_local.loop = loop
    return loop


def _run_awaitable(op, timeout):
    """同步等待一个 WinRT IAsyncOperation，超时或出错都返回 None。"""
    import asyncio

    async def _waiter():
        return await asyncio.wait_for(op, timeout)

    loop = _get_loop()
    if loop.is_running():
        # 调用方线程自己正在跑事件循环（本模块设计为同步调用，这种情况直接降级）
        return None
    return loop.run_until_complete(_waiter())


def _smtc_init():
    """初始化 SMTC manager（只尝试一次，失败后不再重试，避免每次拖慢）。"""
    global _smtc_tried, _smtc_ok, _smtc_manager
    with _smtc_lock:
        if _smtc_tried:
            return _smtc_ok
        _smtc_tried = True
        try:
            from winrt.windows.media.control import (
                GlobalSystemMediaTransportControlsSessionManager as _Mgr,
            )
            manager = _run_awaitable(_Mgr.request_async(), 2.0)
            if manager is None:
                raise RuntimeError("SMTC manager 初始化超时")
            _smtc_manager = manager
            _smtc_ok = True
        except Exception:
            _smtc_manager = None
            _smtc_ok = False
        return _smtc_ok


def _smtc_track():
    """读当前 SMTC 会话；没有正在播放/暂停的会话时返回 None。"""
    if not _smtc_init():
        return None
    try:
        manager = _smtc_manager
        session = manager.get_current_session()
        if session is None:
            return None

        playing = None
        try:
            info = session.get_playback_info()
            status = int(info.playback_status) if info is not None else _STATUS_PLAYING
            if status == _STATUS_PLAYING:
                playing = True
            elif status == _STATUS_PAUSED:
                playing = False
            else:
                # STOPPED / CLOSED / OPENED / CHANGING：没有在听，安静返回
                return None
        except Exception:
            playing = True

        title = artist = album = ""
        try:
            props = _run_awaitable(session.try_get_media_properties_async(), 0.6)
            if props is not None:
                title = _clean_text(getattr(props, "title", ""))
                artist = _clean_text(getattr(props, "artist", ""))
                album = _clean_text(getattr(props, "album_title", ""))
        except Exception:
            pass

        position = duration = None
        try:
            timeline = session.get_timeline_properties()
            if timeline is not None:
                start = _seconds(getattr(timeline, "start_time", None)) or 0.0
                end = _seconds(getattr(timeline, "end_time", None))
                pos = _seconds(getattr(timeline, "position", None))
                if end is not None:
                    total = end - start
                    if total > 0:
                        duration = total
                if pos is not None:
                    rel = pos - start
                    if rel >= 0:
                        position = rel
        except Exception:
            position = duration = None

        if not title and not artist and not album:
            return None

        app = _app_label(getattr(session, "source_app_user_model_id", ""))
        return {
            "title": title,
            "artist": artist,
            "album": album,
            "app": app,
            "playing": bool(playing),
            "position": position,
            "duration": duration,
            "confidence": _CONF_SMTC,
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 窗口标题后端（ctypes EnumWindows，弱证据）
# ---------------------------------------------------------------------------

_window_lock = threading.Lock()
_window_cache = (0.0, None)      # (时间戳, (exe, title) 列表)
_window_supported = None         # None 未探测 / True / False


def _iter_candidate_windows():
    """枚举可见窗口，返回 [(小写 exe 名, 窗口标题), ...]；平台不支持时返回 []。"""
    if sys.platform != "win32":
        return []
    try:
        from ctypes import wintypes
    except Exception:
        return []

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
    ]

    own_pid = os.getpid()
    found = []
    exe_cache = {}

    def _exe_for_pid(pid):
        if pid in exe_cache:
            return exe_cache[pid]
        name = ""
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            try:
                size = wintypes.DWORD(600)
                buf = ctypes.create_unicode_buffer(600)
                if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    full = buf.value or ""
                    name = os.path.basename(full).lower()
            except Exception:
                name = ""
            finally:
                try:
                    kernel32.CloseHandle(handle)
                except Exception:
                    pass
        exe_cache[pid] = name
        return name

    def _callback(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = (buf.value or "").strip()
            if not title:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == own_pid:
                return True
            exe = _exe_for_pid(pid.value)
            if exe:
                found.append((exe, title))
        except Exception:
            pass
        return True

    try:
        callback = enum_proc_type(_callback)
        user32.EnumWindows(callback, 0)
    except Exception:
        return []
    return found


def _scan_windows():
    """带 0.8 秒缓存的窗口扫描，返回 [(exe, title), ...]。"""
    global _window_cache
    now = time.time()
    with _window_lock:
        stamp, cached = _window_cache
        if cached is not None and now - stamp < _WINDOW_CACHE_SECONDS:
            return cached
    rows = _iter_candidate_windows()
    with _window_lock:
        _window_cache = (now, rows)
    return rows


def _window_supported():
    """窗口后端是否可用（真正枚举一次窗口来验证 ctypes 调用链）。"""
    global _window_supported
    if _window_supported is not None:
        return _window_supported
    if sys.platform != "win32":
        _window_supported = False
        return False
    try:
        _scan_windows()
        _window_supported = True
    except Exception:
        _window_supported = False
    return _window_supported


def _is_app_word(text):
    """这一段是不是「应用名」而不是歌名/歌手。"""
    return _clean_text(text).lower() in _APP_TITLE_WORDS


def _split_title_parts(title):
    """把「歌名 - 歌手 - 应用」切成片段。

    返回 (片段列表, 末尾是否带播放器名)。只做结构拆分，不做「像不像歌」的判断。
    """
    raw = _clean_text(title)
    if not raw:
        return [], False
    pieces = [raw]
    for sep in _TITLE_SEPS:
        if sep in raw:
            pieces = [p.strip() for p in raw.split(sep)]
            break
    parts = [p for p in pieces if p]
    trailing_app = False
    # 播放器常在标题末尾缀自己的名字（"七里香 - 周杰伦 - QQ音乐"），剔除
    while len(parts) > 1 and _is_app_word(parts[-1]):
        parts.pop()
        trailing_app = True
    # 偶尔应用名在前（"网易云音乐 - 晴天 - 周杰伦"），也剔除
    while len(parts) > 1 and _is_app_word(parts[0]):
        parts.pop(0)
    return parts, trailing_app


def _looks_like_garbage(text):
    """明显不是歌名/歌手的片段（导航项、页面标题、URL…）。"""
    low = _clean_text(text).lower()
    if not low:
        return True
    if low in _NON_SONG_TITLES:
        return True
    if "和另外" in low:            # Edge 多标签页标题
        return True
    if low.startswith(("http://", "https://", "www.")):
        return True
    return False


def _parse_window_title(title):
    """从已知播放器的窗口标题猜 (歌名, 歌手, 可信度)；不可靠时返回 ("", "", 0.0)。

    规则（宁缺毋滥，宁可返回空也不猜）：
      * 标题必须含分隔符（" - " / "—" / "|" 等），单段标题一律不猜；
      * 末尾是播放器名时先剔除：剩两段 -> 歌名 + 歌手（0.65），否则 0.7；
      * 只剩一段（"晴天 - 网易云音乐"）-> 只有歌名，可信度 0.5；
      * 片段命中导航词黑名单 / URL / 多标签页标题 -> 视为垃圾，返回空。
    """
    parts, trailing_app = _split_title_parts(title)
    if not parts:
        return "", "", 0.0
    if len(parts) >= 2:
        track_title, artist = parts[0], parts[1]
        confidence = _CONF_WINDOW_SONG_ARTIST_APP if trailing_app else _CONF_WINDOW_SONG_ARTIST
    elif trailing_app:
        track_title, artist = parts[0], ""
        confidence = _CONF_WINDOW_SONG_ONLY
    else:
        return "", "", 0.0
    if _looks_like_garbage(track_title):
        return "", "", 0.0
    if artist and _looks_like_garbage(artist):
        return "", "", 0.0
    return track_title, artist, confidence


def _window_track():
    """从已知播放器窗口标题里拿曲目；拿不到 / 可信度太低时返回 None。

    只扫描 _WINDOW_PLAYERS 里的播放器进程，浏览器窗口直接跳过（交给 SMTC）。
    """
    rows = _scan_windows()
    if not rows:
        return None
    best = None
    for exe, title in rows:
        app = _WINDOW_PLAYERS.get(exe)
        if not app:
            continue
        track_title, artist, confidence = _parse_window_title(title)
        if not track_title or confidence < _WINDOW_MIN_CONFIDENCE:
            continue
        if best is None or confidence > best["confidence"]:
            best = {
                "title": track_title,
                "artist": artist,
                "album": "",
                "app": app,
                "playing": True,
                "position": None,
                "duration": None,
                "confidence": confidence,
            }
    return best


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------

def _smtc_has_session():
    """SMTC 当前是否有会话（不看是否在播，暂停也算）。"""
    try:
        return _smtc_manager.get_current_session() is not None
    except Exception:
        return False


def backend():
    """当前后端名。

    - SMTC 有活跃会话（正在播 / 暂停）时返回 "smtc"；
    - SMTC 没会话、但窗口标题后端能给出曲目时返回 "window"（例如网易云旧版
      根本没有注册 SMTC，只能靠窗口标题）；
    - 两者都没曲目时返回"能力兜底"值：winrt 可用 -> "smtc"，否则窗口枚举可用
      -> "window"，都不行 -> "none"。
    """
    try:
        smtc_ready = _smtc_init()
    except Exception:
        smtc_ready = False
    if smtc_ready:
        try:
            if _smtc_has_session():
                return "smtc"
        except Exception:
            pass
    try:
        if _window_supported() and _window_track() is not None:
            return "window"
    except Exception:
        pass
    if smtc_ready:
        return "smtc"
    try:
        if _window_supported():
            return "window"
    except Exception:
        pass
    return "none"


def is_available():
    """是否有可用的探测后端（真正探测一次；不抛异常）。

    注意语义是「能力」而非「此刻正在播放」：SMTC 可用（或窗口枚举可用）就返回 True，
    即使当前没有在放歌。是否在听歌请用 current_track() 判断。
    """
    try:
        return backend() != "none"
    except Exception:
        return False


def preload():
    """可选：提前初始化后端，把首次 ~100ms 的加载成本挪到主程序启动阶段。"""
    try:
        _smtc_init()
        _window_supported()
        return True
    except Exception:
        return False


def current_track():
    """当前曲目 dict；没有任何信息时安静返回 None（不抛异常）。"""
    try:
        track = _smtc_track()
    except Exception:
        track = None
    if track is not None:
        return track
    try:
        return _window_track()
    except Exception:
        return None


def status_text():
    """中文一行，例如「正在听《晴天》— 周杰伦（网易云音乐）」；无信息时 ""。"""
    try:
        track = current_track()
        if not track:
            return ""
        title = track.get("title") or ""
        if not title:
            return ""
        artist = track.get("artist") or ""
        app = track.get("app") or ""
        try:
            confidence = float(track.get("confidence"))
        except (TypeError, ValueError):
            confidence = _CONF_SMTC
        playing = bool(track.get("playing"))
        if confidence < 0.9:
            # 窗口弱证据：只说「好像听到」，不断言；标明来源是窗口标题
            line = ("麟好像听到《" if playing else "麟好像刚听到《") + title + "》"
            if artist:
                line += "— " + artist
            line += "～（来自" + (app + "窗口标题" if app else "窗口标题") + "）"
            return line
        line = ("正在听《" if playing else "已暂停《") + title + "》"
        if artist:
            line += "— " + artist
        if app:
            line += "（" + app + "）"
        return line
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------

def _self_test():
    print("INFO_VERSION :", INFO_VERSION)
    print("platform     :", sys.platform)
    t0 = time.time()
    avail = is_available()
    print("is_available :", avail, " (%.1f ms)" % ((time.time() - t0) * 1000.0))
    print("backend      :", backend())
    track = current_track()
    print("current_track:", track)
    print("status_text  :", status_text() or "(空)")
    print("单次耗时（后端已预热）:")
    for i in range(5):
        t0 = time.perf_counter()
        current_track()
        print("  #%d  %.2f ms" % (i + 1, (time.perf_counter() - t0) * 1000.0))


if __name__ == "__main__":
    _self_test()
