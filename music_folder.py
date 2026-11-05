# -*- coding: utf-8 -*-
"""麟桌宠 · 本地音乐播放器内核（QMediaPlayer + 内置/外部两级曲库）。

替代原八音盒（music_box.py 保留作降级后备）：
  * 曲库两级合并：内置原声曲库（曲库包，放 exe 旁边 music/原声/；老版本是
    PyInstaller --add-data 打进 exe 的，两种布局都认）+ exe 旁边 music/ 文件夹
    （用户导入/手动放置，可写），按文件名去重；哪些算「内置原声」由随 exe
    打包的清单 builtin_tracks.json 判定（见 BUILTIN_SUBDIR / _load_builtin_manifest）；
  * 播放走 PyQt5 自带的 QtMultimedia，但强制切到 Windows Media Foundation
    后端（启动时把 dsengine.dll 挪走，见文件后半 `_prefer_wmf_backend`）：
    DirectShow 打不开很多现代 mp3/flac/m4a，WMF 可以。全程主线程、不新增 QTimer；
  * 四种播放模式：顺序 / 列表循环 / 单曲循环 / 随机播放（随机 = 洗牌式，
    一轮内每首只放一次、放完重洗，不会反复同一首），
    队列由本类自己管理——
    不用 QMediaPlaylist：它在「移除+重建+坏文件」组合下会在 DirectShow 的
    自动推进里原生栈溢出（_qa 探针实测），自己管索引才可控；
  * 某个文件播不了 -> 延迟 700ms 复核（DirectShow 在正常切歌瞬间也会发伪
    InvalidMedia，不能立刻跳）确认真坏后自动跳下一首；连坏一圈才停下报错。

自测：  C:/LinVenv/Scripts/python.exe music_folder.py --scan [--play]

对外 API（MusicFolderPlayer，必须在主线程创建，要已有 QApplication）：
    is_available()      QtMultimedia 是否可用 -> bool（False 时上层回落八音盒）
    scan_tracks()       扫描两级曲库 -> [(path, title, source)]（去重、排序稳定）
    MusicFolderPlayer   play/pause/toggle/stop/next/previous/seek_ms/set_volume/
                        mute/set_mode/cycle_mode/current_track/is_playing/
                        add_files/remove_at/restore_all/tracks/play_at/set_scope/library_tracks
    （分类夹：set_scope() 把播放范围收窄到某个分类夹，范围外不会跳出去）
    信号：trackChanged / stateChanged / modeChanged / queueChanged /
          positionChanged / playerError
"""

import os
import sys
import json
import shutil
import zipfile
import random as _random

try:
    from PyQt5.QtCore import QObject, QUrl, QTimer, pyqtSignal
    from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
    _QTMM = True
except Exception:          # 打包环境缺 QtMultimedia 或无显示平台：回落八音盒
    _QTMM = False

from data import data_dir


# ---------------------------------------------------------------------------
# 播放后端选择：强制走 Windows Media Foundation（WMF）
# ---------------------------------------------------------------------------
# 为什么要有这段：Qt5 在 Windows 上按文件名字典序加载 mediaservice 插件，
# 先命中 DirectShow（dsengine.dll）。实测 DirectShow 后端打不开相当一部分
# mp3（网易云/ffmpeg 编码的 320k、带 LAME/Xing 头的文件），表现就是
# 「时长 00:00、点播放没反应」；而内置那批老 192k mp3 恰好能放 —— 用户看到的
# 「本地音乐全都放不了、内置音乐正常」正是这个后端差异，跟文件路径无关
# （同内容换个目录照样失败，字节拷贝到别处的内置曲照样成功）。
# WMF 后端（wmfengine.dll）能放 mp3 / flac / m4a / wav（ogg 除外），
# 所以启动时把 dsengine.dll 改名，让 Qt 回落到 WMF。
# Qt5 没有切换媒体后端的接口（QT_MEDIA_BACKEND 是 Qt6 才有的），只能挪文件；
# 打包版里这个目录在 _MEIPASS 临时目录，源码版就是本机 PyQt5 目录，都可写。
# 调试：设环境变量 LIN_MUSIC_DEBUG=<文件路径>，会把后端选择结果写进去。
_BACKEND_STATE = "unknown"          # wmf / directshow / unknown（写调试文件用）


def _backend_debug(msg):
    """LIN_MUSIC_DEBUG 打开时，把一行诊断信息追加到该文件（正常用户不受影响）。"""
    path = os.environ.get("LIN_MUSIC_DEBUG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except Exception:
        pass


def _prefer_wmf_backend():
    """把 DirectShow 媒体插件挪走，让 QtMultimedia 回落到 WMF 后端。"""
    global _BACKEND_STATE
    if not _QTMM:
        _BACKEND_STATE = "no-qtmm"
        return
    try:
        from PyQt5.QtCore import QLibraryInfo
        base = QLibraryInfo.location(QLibraryInfo.PluginsPath)
    except Exception as e:
        _BACKEND_STATE = "unknown"
        _backend_debug("qm-plugins-path-error: %r" % (e,))
        return
    media_dir = os.path.join(base, "mediaservice")
    ds = os.path.join(media_dir, "dsengine.dll")
    wmf = os.path.join(media_dir, "wmfengine.dll")
    try:
        if os.path.isfile(ds) and os.path.isfile(wmf):
            off = ds + ".disabled"
            if not os.path.exists(off):
                os.rename(ds, off)          # 挪走，Qt 就不会加载 DirectShow
            _BACKEND_STATE = "wmf"
        elif os.path.isfile(wmf):
            _BACKEND_STATE = "wmf"
        elif os.path.isfile(ds):
            _BACKEND_STATE = "directshow"   # 没有 WMF 可用，只能凑合用旧的
            _backend_debug("wmfengine 不存在，仍用 DirectShow")
        _backend_debug("backend=%s plugins=%s" % (_BACKEND_STATE, media_dir))
    except Exception as e:                  # 只读目录等：放不了就退回 DirectShow
        _BACKEND_STATE = "directshow"
        _backend_debug("backend-rename-failed: %r" % (e,))


if _QTMM:
    _prefer_wmf_backend()

__all__ = ["is_available", "scan_tracks", "builtin_dir", "builtin_dirs", "external_dir",
           "missing_builtin", "BUILTIN_SUBDIR", "BUILTIN_MANIFEST",
           "MusicFolderPlayer", "MODES", "AUDIO_EXTS", "ZIP_EXTS",
           "extract_zip_audio", "fmt_ms"]

ZIP_EXTS = (".zip",)

AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma")

# (settings 键值, 中文名)——顺序即 cycle_mode 的循环顺序
MODES = [
    ("sequential", "顺序播放"),     # 顺着队列放到最后一首就停
    ("loop", "列表循环"),           # 到队尾绕回第一首继续
    ("one", "单曲循环"),            # 一直重放当前这首
    ("random", "随机播放"),         # 洗牌式随机：一轮内不重复，放完重洗
]

# 系统分类 id（与 playlists.SYSTEM_SCOPES 一致；这里不 import 是为了避免循环依赖）
SCOPE_ALL = "sys_all"
SCOPE_BUILTIN = "sys_builtin"
SCOPE_EXTERNAL = "sys_external"


def is_available():
    """QtMultimedia 可导入且当前有 QApplication 实例（QMediaPlayer 的硬前提）。"""
    if not _QTMM:
        return False
    try:
        from PyQt5.QtCore import QCoreApplication
        return QCoreApplication.instance() is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 曲库布局：内置原声 = 曲库包（exe 旁 music\原声\），我的导入 = exe 旁 music\
# ---------------------------------------------------------------------------
# 为什么改：老版本把 77 首 mp3 用 --add-data 打进 exe，运行库+曲库一共 592 MiB，
# 每版更新都得重下 592 MiB（其实只有几 MiB 的代码在变）。现在曲库单独发一次
# 「原声曲库.zip」，解压成 exe 旁 music\原声\*.mp3；exe 只装代码，更新包只有 ~85 MiB。
# 哪些文件算内置不靠目录猜，而靠随 exe 打包的清单 builtin_tracks.json：
# 这样即使用户把曲库解压到错位置（music\ 根目录），标签也不会错。
BUILTIN_SUBDIR = "原声"                    # 曲库包解压后的子目录名
BUILTIN_MANIFEST = "builtin_tracks.json"   # 打包时生成的曲目清单（随 exe 打包）


def _load_builtin_manifest():
    """读内置曲目清单 -> {文件名小写: 原名}；没有清单返回 None（源码模式就是这样）。

    清单随 exe 打包在 _MEIPASS 里；也接受 exe 旁边放一份，方便调试。
    """
    paths = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        paths.append(os.path.join(base, BUILTIN_MANIFEST))
    try:
        paths.append(os.path.join(data_dir(), BUILTIN_MANIFEST))
    except Exception:
        pass
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        tracks = data.get("tracks") if isinstance(data, dict) else data
        if not tracks:
            continue
        out = {}
        for name in tracks:
            try:
                out[str(name).casefold()] = str(name)
            except Exception:
                continue
        if out:
            return out
    return None


def builtin_dirs():
    """内置曲库可能所在的目录，按优先级排列（存在的才返回）。

    * 打包版：_MEIPASS/music（老版本内嵌曲库，兼容）→ exe旁/music/原声（曲库包）
      → exe旁/music（用户把曲库包解压错位置的兜底）
    * 源码版：源码目录/music/原声 → 源码目录/music
    """
    dirs = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        dirs.append(os.path.join(base, "music"))
    try:
        root = data_dir()
    except Exception:
        root = ""
    if root:
        dirs.append(os.path.join(root, "music", BUILTIN_SUBDIR))  # 标准位置
        dirs.append(os.path.join(root, BUILTIN_SUBDIR))           # 解压到 exe 旁
        dirs.append(os.path.join(root, "music"))                  # 解压到 music 根

    return [d for d in dirs if os.path.isdir(d)]


def builtin_dir():
    """内置曲库目录（第一个存在的候选目录；一个都没有时返回 None）。"""
    dirs = builtin_dirs()
    return dirs[0] if dirs else None


def _zip_entry_name(info):
    """还原 zip 条目里的真实文件名。

    Windows 资源管理器/WinRAR 打的包如果名字含中文，有的不写 UTF-8 标记，
    zipfile 会按 cp437 硬解成乱码（"ÖÐÎÄ.mp3"）。这里先按 cp437 编回原字节，
    再依次试 gbk / utf-8 / big5 —— 中文 Windows 上基本都是 gbk，能还原。
    """
    name = info.filename or ""
    if not (info.flag_bits & 0x800):          # 0x800 = 条目名是 UTF-8
        for enc in ("gbk", "utf-8", "big5"):
            try:
                return name.encode("cp437").decode(enc).replace("\\", "/")
            except Exception:
                continue
    return name.replace("\\", "/")


_BAD_NAME_CHARS = '<>:"/\\|?*'


def _clean_audio_name(raw):
    """从 zip 里的路径取出干净的可写音频文件名（不是音频返回 None）。"""
    base = os.path.basename((raw or "").replace("\\", "/")).strip().strip(".")
    base = base.replace("\x00", "")
    for ch in _BAD_NAME_CHARS:
        base = base.replace(ch, "_")
    if not base:
        return None
    stem, ext = os.path.splitext(base)
    if ext.lower() not in AUDIO_EXTS or not stem.strip():
        return None
    return base


def extract_zip_audio(zip_path, dest_dir, max_files=4000, max_bytes=4 * 1024 ** 3):
    """把压缩包里的音频解到 dest_dir，返回 (成功数, 跳过数)。

    * 子目录随便嵌套：解压后一律拍平放进 dest_dir，文件名保留；
    * Windows 压缩工具打的 GBK 中文名会还原成正常中文（见 _zip_entry_name）；
    * 垃圾自动跳过：__MACOSX/ 资源叉、隐藏文件、非音频、目录项；
    * 不覆盖也不重复：同名同大小视为「已经导入过」直接跳过，
      同名不同大小（真·另一首）自动改名 "曲名 (2).mp3"；
    * max_files / max_bytes 只是防压缩炸弹（正常歌单远到不了）。
    任何异常都不往外抛，坏包返回 (0, 0)。
    """
    got = skipped = 0
    existing = {}
    try:
        os.makedirs(dest_dir, exist_ok=True)
        for n in os.listdir(dest_dir):
            try:
                existing[n.casefold()] = os.path.getsize(os.path.join(dest_dir, n))
            except Exception:
                existing[n.casefold()] = -1
    except Exception:
        pass
    total = 0
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                raw = _zip_entry_name(info)
                if not raw or "__MACOSX/" in raw:
                    continue
                base = os.path.basename(raw)
                if not base or base.startswith("."):
                    continue
                name = _clean_audio_name(raw)
                if not name:
                    skipped += 1
                    continue
                size = int(info.file_size or 0)
                if size <= 0:
                    skipped += 1
                    continue
                key = name.casefold()
                if existing.get(key) == size:     # 同一首已经导过：静默跳过
                    skipped += 1
                    continue
                if got >= max_files or total + size > max_bytes:
                    skipped += 1
                    continue
                stem, ext = os.path.splitext(name)
                cand, i = name, 2
                while cand.casefold() in existing:
                    cand = "{} ({}){}".format(stem, i, ext)
                    i += 1
                dst = os.path.join(dest_dir, cand)
                try:
                    with zf.open(info) as src, open(dst, "wb") as out:
                        shutil.copyfileobj(src, out, 1024 * 1024)
                except Exception as e:
                    print("[music_folder] 解压失败:", raw, e)
                    skipped += 1
                    try:
                        os.remove(dst)
                    except Exception:
                        pass
                    continue
                existing[cand.casefold()] = size
                got += 1
                total += size
    except Exception as e:
        print("[music_folder] 打开压缩包失败:", zip_path, e)
        return (got, skipped)
    return (got, skipped)


def _walk_audio(root, max_depth=6):
    """递归找出文件夹里的音频文件（拖文件夹进来时用）；出错就停。"""
    out = []
    root = os.path.abspath(root)
    for cur, dirs, files in os.walk(root):
        depth = cur[len(root):].count(os.sep)
        if depth >= max_depth:
            dirs[:] = []
        for f in files:
            if f.lower().endswith(AUDIO_EXTS):
                out.append(os.path.join(cur, f))
    return out


def external_dir():
    """外部曲库目录：源码运行 = 源码目录/music，打包后 = exe 旁边/music。
    导入的歌复制到这里，升级换 exe 也不丢；内置曲库包解压到 music/原声/ 子目录。"""
    return os.path.join(data_dir(), "music")


def missing_builtin():
    """清单里应有几首、现在找到几首 -> (应有, 找到)。没有清单时返回 (0, 0)。

    启动自检用：曲库包没解压/解压错/被删时提醒用户，而不是让「内置原声」莫名变空。
    """
    manifest = _load_builtin_manifest()
    if not manifest:
        return (0, 0)
    found = set()
    for base in builtin_dirs():
        try:
            names = os.listdir(base)
        except Exception:
            continue
        for name in names:
            low = name.casefold()
            if low in manifest:
                found.add(low)
    return (len(manifest), len(found))


def fmt_ms(ms):
    """毫秒 -> mm:ss（负数/超一小时也不崩）。"""
    try:
        ms = int(ms)
    except Exception:
        ms = 0
    if ms < 0:
        ms = 0
    s = ms // 1000
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%02d:%02d" % (m, s)


def scan_tracks():
    """扫描曲库：[(path, title, source)]，source 是「内置」或「本地」。

    先扫内置（多个候选目录，按 builtin_dirs 优先级），有清单时只认清单里的
    文件名；再扫外部目录补齐。同名文件一律算「内置」（不会同一首出现两次）。
    合并后按曲名排序；文件名（去扩展名）就是曲名，【】（）等原样保留。
    """
    items, seen = [], set()
    manifest = _load_builtin_manifest()

    def _take(base, source, only_manifest=False):
        try:
            names = os.listdir(base)
        except Exception:
            return
        for name in names:
            low = name.casefold()
            if not low.endswith(AUDIO_EXTS):
                continue
            if only_manifest and manifest is not None and low not in manifest:
                continue        # 清单里的才算内置；其余留给外部扫描当「我的导入」
            if low in seen:     # 按完整文件名去重（大小写不敏感）
                continue
            path = os.path.join(base, name)
            try:
                if os.path.getsize(path) <= 0:
                    continue
            except Exception:
                continue
            seen.add(low)
            items.append((path, name.rsplit(".", 1)[0], source))

    for base in builtin_dirs():
        _take(base, "内置", True)
    ext = external_dir()
    if ext and os.path.isdir(ext):
        _take(ext, "本地")
    # 回收站里的歌 / 彻底删掉的内置曲不进曲库（否则重启后又冒出来，
    # 面板「彻底删除」就变成了假的）。过滤钩子在 trash.py，Qt 无关。
    try:
        from trash import is_hidden
        items = [t for t in items if not is_hidden(t[2], t[1])]
    except Exception:
        pass
    items.sort(key=lambda t: t[1].casefold())
    return items


class MusicFolderPlayer(QObject):
    """本地曲库播放器：封装 QMediaPlayer，队列与播放模式由本类自己管理。"""

    trackChanged = pyqtSignal(int, str, str)   # 队列序号, 曲名, 来源（内置/本地）
    scopeChanged = pyqtSignal(str)             # 播放范围（分类夹）真正切换后的 id
    stateChanged = pyqtSignal(bool)            # 是否正在播放
    modeChanged = pyqtSignal(str)              # 模式键（sequential/loop/one/random）
    queueChanged = pyqtSignal()                # 曲库/队列变化
    positionChanged = pyqtSignal(int, int)     # 已播毫秒, 总时长毫秒
    playerError = pyqtSignal(str)              # 给上层说话用的错误摘要

    def __init__(self, parent=None):
        super().__init__(parent)
        self._library = []                     # 两级曲库全量 [(path, title, source)]
        self._tracks = []                      # 当前播放队列（= 一个分类夹范围内的曲目）
        self._scope_id = SCOPE_ALL             # 当前播放范围（分类夹 id，见 playlists.SYSTEM_SCOPES）
        self._pending_scope = None             # 播放中切了分类夹：等当前这首放完再切 (id, tracks)
        self._idx = -1                         # 当前曲目下标；-1 = 没选
        self._mode_key = "sequential"          # 顺序播放（需求：默认顺序）
        self._shuffle_pool = []                # 随机播放的洗牌池（待放的队列下标）
        self._err_streak = 0                   # 连续坏文件计数（防坏曲库空转）
        self._volume = 70
        self._muted = False
        self._pending_skip = None              # 延迟复核定时器（见 _schedule_skip_check）
        self._verify_retries = 0               # 慢盘加载的宽限拍数
        self._in_advance = False               # 重入守卫：EndOfMedia/坏文件跳歌时，
                                               # setMedia 内部泵消息可能再次触发本类信号
        # 最近一次导入的结果摘要（面板拿它给用户说话；不参与播放逻辑）
        self.last_import = {"files": 0, "zips": 0, "skipped": 0, "folders": 0}

        self._player = QMediaPlayer(self, QMediaPlayer.LowLatency)
        self._player.stateChanged.connect(self._on_state)
        self._player.positionChanged.connect(self._on_pos)
        self._player.durationChanged.connect(self._on_pos)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.error.connect(self._on_media_error)

    # ---- 状态回调 -------------------------------------------------------
    def _on_state(self, state):
        try:
            playing = (state == QMediaPlayer.PlayingState)
            if playing:
                pass                        # 计数清零交给 _on_pos（真的出声才算好）
            self.stateChanged.emit(playing)
        except Exception:
            pass

    def _on_pos(self, *_):
        try:
            pos = int(self._player.position())
            if pos > 0:
                self._err_streak = 0        # 真的在出声：坏文件计数清零
            self.positionChanged.emit(pos, int(self._player.duration()))
        except Exception:
            pass

    def _on_media_status(self, status):
        try:
            if status == QMediaPlayer.EndOfMedia:
                self._advance_auto()       # 一曲播完，按模式推进
            elif status == QMediaPlayer.InvalidMedia:
                self._schedule_skip_check()
        except Exception:
            pass

    def _on_media_error(self, *_):
        # 错误信号是坏文件的直接证据：连续达到阈值 = 一圈都播不动，停下报错；
        # 不够阈值就安排延迟复核——正常切歌的伪 InvalidMedia 会被复核放过。
        try:
            if not self._tracks or self._idx < 0:
                return
            self._err_streak += 1
            if self._err_streak >= max(3, min(len(self._tracks), 8)):
                self._err_streak = 0
                self.stop()
                self.playerError.emit("这些文件麟播不动，先跳过啦（支持 mp3 / flac / m4a / wav）")
                return
            self._schedule_skip_check()
        except Exception:
            pass

    # ---- 坏文件延迟复核 ---------------------------------------------------
    def _schedule_skip_check(self):
        try:
            if self._pending_skip is None:
                t = QTimer(self)
                t.setSingleShot(True)
                t.timeout.connect(self._verify_skip)
                self._pending_skip = t
            self._pending_skip.start(700)
        except Exception:
            pass

    def _verify_skip(self):
        """错误后的复核：确认真坏才跳。

        DirectShow 的怪状态都在这排除：失败会转 NoMedia/InvalidMedia、
        切歌瞬间会有一次伪 InvalidMedia、慢盘加载会停在 LoadingMedia。
        「在播且已出声/已缓冲」= 健康；「加载中」= 再给几拍；其余 = 真坏。
        """
        try:
            if self._in_advance or not self._tracks or self._idx < 0:
                return
            if self.is_paused():
                return
            status = self._player.mediaStatus()
            if (self.is_playing()
                    and status in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia,
                                   QMediaPlayer.EndOfMedia)):
                return                      # 切歌噪音：新曲已经播起来了，不跳
            if (self.is_playing()
                    and status == QMediaPlayer.LoadingMedia
                    and self._verify_retries < 6):
                self._verify_retries += 1   # 慢盘加载中：再给几拍（共约 4 秒）
                self._schedule_skip_check()
                return
            self._verify_retries = 0
            self._skip_broken()
        except Exception:
            pass

    def _skip_broken(self):
        if self._in_advance:
            return
        self._in_advance = True
        try:
            self._err_streak += 1
            if self._err_streak >= max(3, min(len(self._tracks), 20)):
                self.stop()
                self._err_streak = 0
                self.playerError.emit("这些文件麟播不动，先跳过啦（支持 mp3 / flac / m4a / wav）")
                return
            if len(self._tracks) > 1:
                self._play_index((self._idx + 1) % len(self._tracks))
            else:
                self.stop()
                self.playerError.emit("这一首麟播不出来，已经停下啦")
        finally:
            self._in_advance = False

    # ---- 模式推进 ---------------------------------------------------------
    def _advance_auto(self):
        """一曲自然播完：先看有没有「等这首放完再切」的分类夹，再按模式推进。"""
        if self._in_advance:
            return
        if self._pending_scope is not None:
            sid, tracks = self._pending_scope
            self._pending_scope = None
            self._in_advance = True
            try:
                self._apply_scope(sid, tracks, stop_current=False)
                if self._tracks:
                    self._play_index(0)        # 切到新分类夹后从第一首接着放
            finally:
                self._in_advance = False
            return
        if not self._tracks or self._idx < 0:
            return
        self._in_advance = True
        try:
            n = len(self._tracks)
            if self._mode_key == "one":
                self._play_index(self._idx)            # 单曲循环
            elif self._mode_key == "loop":
                self._play_index((self._idx + 1) % n)  # 列表循环
            elif self._mode_key == "random":
                self._play_index(self._random_next_index(n))  # 随机播放
            else:
                # 顺序播放：顺着队列往下走一首，走到队尾就停。
                # 以前这里是「放完这首直接停」——用户切到「顺序播放」后
                # 一曲终了不换歌，看起来就像坏了；顺序播放本来就该接着放。
                if self._idx + 1 < n:
                    self._play_index(self._idx + 1)
                else:
                    self.stop()                     # 队尾了，正常收住
                    self._err_streak = 0
        finally:
            self._in_advance = False

    def _random_next_index(self, n):
        """随机播放挑下一首：洗牌池里取，一轮内每首只放一次，放完一轮重新洗。

        用「洗牌袋」而不是每次乱摇：纯粹的 random 会连着放同一首或
        刚放过的歌，听着像坏了；洗牌袋保证一轮把所有歌轮一遍再重洗。
        """
        pool = [i for i in self._shuffle_pool if 0 <= i < n and i != self._idx]
        if not pool:
            pool = [i for i in range(n) if i != self._idx]
            _random.shuffle(pool)
        if not pool:
            return max(0, min(self._idx, n - 1))    # 只有一首：原地重放
        pick = pool.pop(0)
        self._shuffle_pool = pool
        return pick

    def _play_index(self, idx):
        """真正把第 idx 首装进 QMediaPlayer 并播放。"""
        if not (0 <= idx < len(self._tracks)):
            return
        self._idx = idx
        path = self._tracks[idx][0]
        try:
            self._player.setMedia(QMediaContent(QUrl.fromLocalFile(path)))
            self._player.play()
        except Exception as e:
            print("[music_folder] 播放失败:", path, e)
        _p, title, source = self._tracks[idx]
        self.trackChanged.emit(idx, title, source)

    def _on_current_track_emit(self):
        try:
            if 0 <= self._idx < len(self._tracks):
                _p, title, source = self._tracks[self._idx]
                self.trackChanged.emit(self._idx, title, source)
        except Exception:
            pass

    # ---- 曲库 / 队列 ----------------------------------------------------
    def tracks(self):
        """当前播放队列（= 当前分类夹范围内的曲目，面板列表就显示这个）。"""
        return list(self._tracks)

    def library_tracks(self):
        """两级曲库全量（不受分类夹限制，分类计数/添加歌曲用）。"""
        return list(self._library)

    def scope_id(self):
        return self._scope_id

    def pending_scope(self):
        """正在排队等切歌的分类夹 id；没有则 None。"""
        p = self._pending_scope
        return p[0] if p else None

    def set_scope(self, tracks, scope_id=SCOPE_ALL, defer=False):
        """把播放范围切到某个分类夹（tracks = 该范围内的曲目）。

        defer=True 且正在放歌时：当前这首不打断，等它自然播完再换队列
        （需求 3.2.1「切换分类夹时，若当前正在播放，默认保留当前歌曲播放
        至结束后再切换队列」）。返回 True=已立即切换，False=排队等这首放完。
        """
        try:
            tracks = list(tracks or [])
            if defer and self.is_playing() and scope_id != self._scope_id:
                self._pending_scope = (scope_id, tracks)
                return False
            self._pending_scope = None
            self._apply_scope(scope_id, tracks, stop_current=True)
            return True
        except Exception as e:
            print("[music_folder] set_scope:", e)
            return False

    def _apply_scope(self, scope_id, tracks, stop_current=True):
        """真正替换队列：尽量把游标留在同一首上；当前曲不在新范围里就停掉旧媒体。"""
        cur = self.current_track()
        cur_key = (cur["source"], cur["title"]) if cur else None
        found = -1
        if cur_key:
            for i, (_p, t, s) in enumerate(tracks):
                if (s, t) == cur_key:
                    found = i
                    break
        if cur_key and found < 0 and stop_current:
            # 旧歌不在新分类里：必须停，否则界面显示新队列、耳朵里还是旧歌
            self.stop()
        self._tracks = tracks
        self._scope_id = scope_id
        self._idx = found if found >= 0 else (0 if tracks else -1)
        self._err_streak = 0
        self._shuffle_pool = []                # 换了范围，随机池作废
        self.queueChanged.emit()
        self.scopeChanged.emit(scope_id)
        self._on_current_track_emit()

    def restore_all(self, keep_title=""):
        """重新扫描两级曲库；保留当前分类夹范围（内置/导入过滤照旧）。

        自建分类夹的范围由面板在 refresh 时重新 set_scope（歌单在 playlists.py 里）。
        """
        was_playing = self.is_playing()
        if was_playing:
            self.stop()
        self._library = scan_tracks()
        self._pending_scope = None
        if self._scope_id == SCOPE_BUILTIN:
            tracks = [t for t in self._library if t[2] == "内置"]
        elif self._scope_id == SCOPE_EXTERNAL:
            tracks = [t for t in self._library if t[2] == "本地"]
        else:
            tracks = list(self._library)
            self._scope_id = SCOPE_ALL
        self._tracks = tracks
        self._shuffle_pool = []
        self._idx = -1
        if keep_title:
            for i, (_p, t, _s) in enumerate(self._tracks):
                if t == keep_title:
                    self._idx = i
                    break
        if self._idx < 0 and self._tracks:
            self._idx = 0
        self.queueChanged.emit()
        self._on_current_track_emit()

    def refresh_library(self):
        """轻量重扫曲库：只更新 _library / 当前队列，不打断正在放的音乐。

        回收站「恢复 / 彻底删除」后用：启动扫描时被过滤掉的歌要能马上回到列表
        （或者马上从列表消失），不用等下一次重启。正在播的那首如果还在新曲库里
        就保持不动，只是重算一下下标。
        """
        try:
            self._library = scan_tracks()
        except Exception:
            return
        if self._scope_id not in (SCOPE_ALL, SCOPE_BUILTIN, SCOPE_EXTERNAL):
            self.queueChanged.emit()
            return                      # 自建分类夹的范围由面板自己重设
        cur = self.current_track() or {}
        title = cur.get("title", "")
        if self._scope_id == SCOPE_BUILTIN:
            tracks = [t for t in self._library if t[2] == "内置"]
        elif self._scope_id == SCOPE_EXTERNAL:
            tracks = [t for t in self._library if t[2] == "本地"]
        else:
            tracks = list(self._library)
        self._tracks = tracks
        self._shuffle_pool = []
        if title:
            for i, (_p, t, _s) in enumerate(self._tracks):
                if t == title:
                    self._idx = i
                    break
            else:
                self._idx = -1
        self.queueChanged.emit()

    def add_files(self, paths):
        """导入音频 / 压缩包 / 文件夹到外部曲库，返回新收进来的曲目数。

        * 音频文件：复制进 exe 旁的 music 文件夹（同名同大小视为已导入过）；
        * .zip：自动解压里面的音频（拍平目录、GBK 中文名还原、垃圾文件跳过）；
        * 文件夹：递归找出里面的音频再复制（拖文件夹进来才会走到）。
        结果摘要写进 self.last_import，面板用它决定说哪句话。
        """
        ext_dir = external_dir()
        n = 0
        stat = {"files": 0, "zips": 0, "skipped": 0, "folders": 0}
        keep = (self.current_track() or {}).get("title", "")
        try:
            os.makedirs(ext_dir, exist_ok=True)
        except Exception as e:
            print("[music_folder] 建外部曲库目录失败:", e)
        existing = {}
        try:
            for name in os.listdir(ext_dir):
                try:
                    existing[name.casefold()] = os.path.getsize(os.path.join(ext_dir, name))
                except Exception:
                    existing[name.casefold()] = -1
        except Exception:
            pass

        def _copy_one(src_path, src_name):
            """复制单个音频；返回 1=新导入，0=跳过。"""
            try:
                size = os.path.getsize(src_path)
            except Exception:
                return 0
            if size <= 0:
                return 0
            base = _clean_audio_name(src_name) or os.path.basename(src_path)
            key = base.casefold()
            if existing.get(key) == size:
                return 0
            stem, ext = os.path.splitext(base)
            cand, i = base, 2
            while cand.casefold() in existing:
                cand = "{} ({}){}".format(stem, i, ext)
                i += 1
            dst = os.path.join(ext_dir, cand)
            if os.path.abspath(src_path) != os.path.abspath(dst):
                shutil.copy2(src_path, dst)
                try:
                    existing[cand.casefold()] = os.path.getsize(dst)
                except Exception:
                    existing[cand.casefold()] = size
            return 1

        for src in paths or []:
            try:
                low = str(src).lower()
                if os.path.isdir(src):
                    stat["folders"] += 1
                    for p in _walk_audio(src):
                        try:
                            n += _copy_one(p, os.path.basename(p))
                        except Exception as e:
                            print("[music_folder] 导入失败:", p, e)
                    continue
                if low.endswith(ZIP_EXTS):
                    stat["zips"] += 1
                    got, skipped = extract_zip_audio(src, ext_dir)
                    n += got
                    stat["skipped"] += skipped
                    continue
                if not os.path.isfile(src):
                    continue
                if not low.endswith(AUDIO_EXTS):
                    stat["skipped"] += 1
                    continue
                got = _copy_one(src, os.path.basename(src))
                n += got
                if not got:
                    stat["skipped"] += 1
            except Exception as e:
                print("[music_folder] 导入失败:", src, e)
        self.last_import = dict(stat, files=n)
        if n:
            self.restore_all(keep_title=keep)
        return n
    def remove_at(self, idx):
        """把第 idx 首移出本次播放队列（不删文件）；恢复用 restore_all()。"""
        try:
            if 0 <= idx < len(self._tracks):
                was_current = (idx == self._idx)
                self._tracks.pop(idx)
                if was_current:
                    self.stop()
                    self._idx = min(idx, len(self._tracks) - 1) if self._tracks else -1
                elif idx < self._idx:
                    self._idx -= 1
                self.queueChanged.emit()
                self._on_current_track_emit()
                return True
        except Exception as e:
            print("[music_folder] remove_at:", e)
        return False

    # ---- 播放控制 --------------------------------------------------------
    def play(self, title=None):
        """开始播放；title 给了就优先播这首。曲库为空返回 False。

        分类夹生效时（队列只是曲库的一个子集），点名的歌如果在别的分类里，
        先把播放范围让回「全部音乐」再放——否则用户会觉得「点了没反应」。
        """
        if not self._tracks:
            return False
        if title:
            hit = -1
            for i, (_p, t, _s) in enumerate(self._tracks):
                if t == title:
                    hit = i
                    break
            if hit < 0:
                for i, (_p, t, _s) in enumerate(self._library):
                    if t == title:
                        self._apply_scope(SCOPE_ALL, list(self._library))
                        hit = i
                        break
            if hit >= 0:
                self._idx = hit
        if self._idx < 0:
            self._idx = 0
        self._play_index(self._idx)
        return True

    def play_at(self, idx):
        """直接跳到队列第 idx 首并播放（播放列表双击用）。"""
        try:
            if 0 <= idx < len(self._tracks):
                self._play_index(idx)
                return True
        except Exception:
            pass
        return False

    def pause(self):
        try:
            self._player.pause()
        except Exception:
            pass

    def toggle(self):
        if self.is_playing():
            self.pause()
        else:
            if self._idx < 0 and self._tracks:
                self._idx = 0
            if self.is_paused():
                try:
                    self._player.play()
                except Exception:
                    pass
            else:
                self.play()

    def stop(self):
        try:
            if self._pending_skip is not None:
                self._pending_skip.stop()   # 用户主动停：别再触发坏文件跳歌
            self._player.stop()
        except Exception:
            pass

    def is_playing(self):
        try:
            return self._player.state() == QMediaPlayer.PlayingState
        except Exception:
            return False

    def is_paused(self):
        try:
            return self._player.state() == QMediaPlayer.PausedState
        except Exception:
            return False

    def next(self):
        try:
            if self._tracks:
                n = len(self._tracks)
                if self._mode_key == "random":
                    self._play_index(self._random_next_index(n))
                else:
                    self._play_index((self._idx + 1) % n)
                return True
        except Exception:
            pass
        return False

    def previous(self):
        """3 秒内算「上一首」，超过 3 秒先回到本曲开头（通用播放器习惯）。"""
        try:
            if self._player.position() > 3000:
                self._player.setPosition(0)
                return True
            if self._tracks:
                self._play_index((self._idx - 1) % len(self._tracks))
                return True
        except Exception:
            pass
        return False

    def seek_ms(self, ms):
        try:
            if self._player.duration() > 0:
                self._player.setPosition(max(0, int(ms)))
        except Exception:
            pass

    def position_ms(self):
        try:
            return int(self._player.position())
        except Exception:
            return 0

    def duration_ms(self):
        try:
            return int(self._player.duration())
        except Exception:
            return 0

    def current_track(self):
        """当前曲目的 dict(index/title/path/source)；没选曲返回 None。"""
        try:
            if 0 <= self._idx < len(self._tracks):
                path, title, source = self._tracks[self._idx]
                return {"index": self._idx, "title": title,
                        "path": path, "source": source}
        except Exception:
            pass
        return None

    # ---- 音量 / 模式 ------------------------------------------------------
    def set_volume(self, v):
        try:
            v = int(v)
        except Exception:
            v = 70
        v = max(0, min(100, v))
        self._volume = v
        try:
            self._player.setVolume(v)
            if v > 0 and self._player.isMuted():
                self._player.setMuted(False)
                self._muted = False
        except Exception:
            pass

    def volume(self):
        return self._volume

    def mute(self, on):
        try:
            self._muted = bool(on)
            self._player.setMuted(bool(on))
        except Exception:
            pass

    def is_muted(self):
        return self._muted

    def set_mode(self, key):
        if any(key == m[0] for m in MODES):
            self._mode_key = key
            if key == "random":
                self._shuffle_pool = []        # 刚切进随机：从头开始洗一轮
            self.modeChanged.emit(self._mode_key)

    def cycle_mode(self):
        """循环切四种模式（顺序 → 列表循环 → 单曲循环 → 随机），返回新模式的键。"""
        keys = [m[0] for m in MODES]
        try:
            i = keys.index(self._mode_key)
        except ValueError:
            i = -1
        self._mode_key = keys[(i + 1) % len(keys)]
        self.modeChanged.emit(self._mode_key)
        return self._mode_key

    def mode_key(self):
        return self._mode_key

    @staticmethod
    def mode_name(key):
        for k, name in MODES:
            if k == key:
                return name
        return key


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------

def _self_test():
    print("QtMultimedia 可用:", _QTMM)
    print("内置曲库目录:", builtin_dir())
    print("外部曲库目录:", external_dir())
    tracks = scan_tracks()
    print("曲库共 %d 首，前 5 首:" % len(tracks))
    for p, t, s in tracks[:5]:
        print("  [%s] %s" % (s, t))
    if "--play" in sys.argv and is_available():
        from PyQt5.QtCore import QCoreApplication, QEventLoop, QTimer
        app = QCoreApplication.instance()
        if app is None:
            app = QCoreApplication(sys.argv)
        pl = MusicFolderPlayer()
        pl.stateChanged.connect(lambda on: print("state:", "playing" if on else "stopped"))
        pl.trackChanged.connect(lambda i, t, s: print("track[%d] %s (%s)" % (i, t, s)))
        pl.playerError.connect(lambda m: print("error:", m))
        ok = pl.play(tracks[0][1] if tracks else None)
        print("play() ->", ok)
        loop = QEventLoop()
        QTimer.singleShot(3000, loop.quit)
        loop.exec_()
        print("3 秒后 position:", pl.position_ms(), "is_playing:", pl.is_playing())
        pl.stop()
        print("已停止，is_playing:", pl.is_playing())


if __name__ == "__main__":
    _self_test()
