# -*- coding: utf-8 -*-
"""麟桌宠 · 本地音乐播放器内核（QMediaPlayer + 内置/外部两级曲库）。

替代原八音盒（music_box.py 保留作降级后备）：
  * 曲库两级合并：exe 内置曲库（PyInstaller --add-data 打包的 mp3，只读）
    + exe 旁边 music/ 文件夹（用户导入/手动放置，可写），按文件名去重；
  * 播放走 PyQt5 自带的 QtMultimedia（Windows 下 DirectShow/WMF 后端），
    mp3/flac/m4a 等都能播，全程主线程、不新增 QTimer；
  * 四种播放模式：顺序 / 列表循环 / 单曲循环 / 随机，队列由本类自己管理——
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
                        add_files/remove_at/restore_all/tracks/play_at
    信号：trackChanged / stateChanged / modeChanged / queueChanged /
          positionChanged / playerError
"""

import os
import sys
import shutil
import random as _random

try:
    from PyQt5.QtCore import QObject, QUrl, QTimer, pyqtSignal
    from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
    _QTMM = True
except Exception:          # 打包环境缺 QtMultimedia 或无显示平台：回落八音盒
    _QTMM = False

from data import data_dir

__all__ = ["is_available", "scan_tracks", "builtin_dir", "external_dir",
           "MusicFolderPlayer", "MODES", "AUDIO_EXTS", "fmt_ms"]

AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma")

# (settings 键值, 中文名)——顺序即 cycle_mode 的循环顺序
MODES = [
    ("sequential", "顺序播放"),
    ("loop", "列表循环"),
    ("one", "单曲循环"),
    ("random", "随机播放"),
]


def is_available():
    """QtMultimedia 可导入且当前有 QApplication 实例（QMediaPlayer 的硬前提）。"""
    if not _QTMM:
        return False
    try:
        from PyQt5.QtCore import QCoreApplication
        return QCoreApplication.instance() is not None
    except Exception:
        return False


def builtin_dir():
    """exe 内置曲库目录（_MEIPASS/music）；源码模式没有内置曲库，返回 None。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "music")
    return None


def external_dir():
    """外部曲库目录：源码运行 = 源码目录/music，打包后 = exe 旁边/music。
    导入的歌复制到这里，升级换 exe 也不丢。"""
    return os.path.join(data_dir(), "music")


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
    """扫描两级曲库：[(path, title, source)]。

    外部曲库优先（用户自己导入的同名文件盖过内置），合并后按曲名排序；
    文件名（去扩展名）就是曲名，特殊情况字符（【】（）等）原样保留。
    """
    items, seen = [], set()
    for source, base in (("本地", external_dir()), ("内置", builtin_dir())):
        if not base or not os.path.isdir(base):
            continue
        try:
            names = os.listdir(base)
        except Exception:
            continue
        for name in names:
            low = name.lower()
            if not low.endswith(AUDIO_EXTS):
                continue
            if low in seen:            # 按完整文件名去重（大小写不敏感）
                continue
            path = os.path.join(base, name)
            try:
                if os.path.getsize(path) <= 0:
                    continue
            except Exception:
                continue
            seen.add(low)
            items.append((path, name.rsplit(".", 1)[0], source))
    items.sort(key=lambda t: t[1].casefold())
    return items


class MusicFolderPlayer(QObject):
    """本地曲库播放器：封装 QMediaPlayer，队列与播放模式由本类自己管理。"""

    trackChanged = pyqtSignal(int, str, str)   # 队列序号, 曲名, 来源（内置/本地）
    stateChanged = pyqtSignal(bool)            # 是否正在播放
    modeChanged = pyqtSignal(str)              # 模式键（sequential/loop/one/random）
    queueChanged = pyqtSignal()                # 曲库/队列变化
    positionChanged = pyqtSignal(int, int)     # 已播毫秒, 总时长毫秒
    playerError = pyqtSignal(str)              # 给上层说话用的错误摘要

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tracks = []                      # [(path, title, source)]
        self._idx = -1                         # 当前曲目下标；-1 = 没选
        self._mode_key = "random"
        self._err_streak = 0                   # 连续坏文件计数（防坏曲库空转）
        self._volume = 70
        self._muted = False
        self._pending_skip = None              # 延迟复核定时器（见 _schedule_skip_check）
        self._verify_retries = 0               # 慢盘加载的宽限拍数
        self._in_advance = False               # 重入守卫：EndOfMedia/坏文件跳歌时，
                                               # setMedia 内部泵消息可能再次触发本类信号

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
                self.playerError.emit("这些文件麟播不动，先跳过啦（可能不是真正的音频）")
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
                self.playerError.emit("这些文件麟播不动，先跳过啦（可能不是真正的音频）")
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
        """一曲自然播完：按当前模式决定下一首。"""
        if self._in_advance or not self._tracks or self._idx < 0:
            return
        self._in_advance = True
        try:
            n = len(self._tracks)
            if self._mode_key == "one":
                self._play_index(self._idx)            # 单曲循环
            elif self._mode_key == "loop":
                self._play_index((self._idx + 1) % n)  # 列表循环
            elif self._mode_key == "random":
                self._play_index(self._random_index())
            else:                                       # sequential：放完就停
                self.stop()
                self._err_streak = 0
        finally:
            self._in_advance = False

    def _random_index(self):
        n = len(self._tracks)
        if n <= 1:
            return 0
        i = self._idx
        while i == self._idx:                # 随机到别的曲（避免原地重复）
            i = _random.randrange(n)
        return i

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
        return list(self._tracks)

    def restore_all(self, keep_title=""):
        """重新扫描两级曲库；keep_title 用来恢复上次曲目（只选曲，不自动播）。"""
        was_playing = self.is_playing()
        if was_playing:
            self.stop()
        self._tracks = scan_tracks()
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

    def add_files(self, paths):
        """把选中的音频复制进外部曲库并重新扫描，返回成功导入的数量。"""
        ext_dir = external_dir()
        n = 0
        keep = (self.current_track() or {}).get("title", "")
        for src in paths or []:
            try:
                if not os.path.isfile(src):
                    continue
                if not src.lower().endswith(AUDIO_EXTS):
                    continue
                os.makedirs(ext_dir, exist_ok=True)
                dst = os.path.join(ext_dir, os.path.basename(src))
                if os.path.abspath(src) != os.path.abspath(dst):
                    shutil.copy2(src, dst)
                n += 1
            except Exception as e:
                print("[music_folder] 导入失败:", src, e)
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
        """开始播放；title 给了就优先播这首。曲库为空返回 False。"""
        if not self._tracks:
            return False
        if title:
            for i, (_p, t, _s) in enumerate(self._tracks):
                if t == title:
                    self._idx = i
                    break
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
                self._play_index((self._idx + 1) % len(self._tracks))
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
            self.modeChanged.emit(self._mode_key)

    def cycle_mode(self):
        """循环切换 4 种模式，返回新模式的键。"""
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
