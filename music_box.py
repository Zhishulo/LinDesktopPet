# -*- coding: utf-8 -*-
"""麟桌宠 · 八音盒摇篮曲（纯标准库合成 + Windows winsound 播放）。

给桌面宠物「麟」陪用户听歌用的背景音乐模块，特点：
  * 只依赖 Python 标准库（wave / array / math / os / sys / tempfile / threading / time），
    不依赖 numpy、PyQt、网络或任何二进制素材；
  * 首次使用时用正弦叠加 + 指数衰减包络合成一段 24 秒的 C 大调五声音阶八音盒摇篮曲，
    写入 %TEMP%/lin_pet_music/ 缓存，之后直接复用；
  * 用 winsound.PlaySound(..., SND_ASYNC | SND_LOOP) 在后台线程里循环播放，
    play() 立即返回，绝不阻塞 Qt UI 线程。

自测：  C:/LinVenv/Scripts/python.exe music_box.py [--play]

对外 API：
    MUSIC_VERSION   旋律版本号，修改旋律时自增即可让旧缓存失效
    is_available()  是否可播放（winsound 是否可用）-> bool
    music_file()    返回 WAV 绝对路径（首次调用时合成）-> str
    build_music(p)  把合成结果写入指定路径
    MusicPlayer     play(loop=True)->bool / stop() / is_playing()->bool
    play() / stop() 模块级单例快捷方式
"""

import array
import math
import os
import sys
import tempfile
import threading
import time
import wave

try:
    import winsound
except Exception:  # 非 Windows 或精简解释器
    winsound = None

__all__ = [
    "MUSIC_VERSION",
    "is_available",
    "music_file",
    "build_music",
    "MusicPlayer",
    "play",
    "stop",
]

# ---------------------------------------------------------------------------
# 基本参数
# ---------------------------------------------------------------------------

MUSIC_VERSION = 1          # 改旋律时自增，让旧缓存失效

_SAMPLE_RATE = 22050       # 采样率
_CHANNELS = 1              # 单声道
_SAMPLE_WIDTH = 2          # 16-bit

_BEAT = 0.75               # 基本音符时长（秒）= 80 BPM 的四分音符
_TOTAL_BEATS = 32          # 8 小节 x 4 拍
_TAIL = 1.45               # 每个音符击弦后的余音尾巴（秒），会环绕到文件开头
_ATTACK = 0.005            # 5ms 起音，避免爆音
_RELEASE = 0.045           # 结尾 45ms 淡出，避免截断爆音
_PEAK_LIMIT = 0.45         # 峰值上限（< 0.5，绝不削波）
_TARGET_RMS = 0.135        # 整体 RMS 约 -18 dBFS

_CACHE_DIR_NAME = "lin_pet_music"
_CACHE_NAME = "musicbox_v%d.wav" % MUSIC_VERSION

# 音名 -> 频率（十二平均律，A4 = 440Hz）
_NOTE_FREQ = {
    "C3": 130.813, "D3": 146.832, "E3": 164.814, "G3": 195.998, "A3": 220.000,
    "C4": 261.626, "D4": 293.665, "E4": 329.628, "G4": 391.995, "A4": 440.000,
    "C5": 523.251, "D5": 587.330, "E5": 659.255, "G5": 783.991, "A5": 880.000,
}

# 右手旋律：8 小节，每小节 4 拍，C 大调五声音阶（C D E G A），温柔、可循环
_MELODY = [
    [("C5", 1.0), ("E5", 1.0), ("G5", 1.0), ("E5", 1.0)],
    [("D5", 1.5), ("C5", 0.5), ("A4", 1.0), ("G4", 1.0)],
    [("E5", 1.0), ("D5", 1.0), ("C5", 1.0), ("D5", 1.0)],
    [("E5", 2.0), ("G5", 1.0), ("A5", 1.0)],
    [("G5", 1.5), ("E5", 0.5), ("D5", 1.0), ("C5", 1.0)],
    [("A4", 1.0), ("C5", 1.0), ("D5", 1.0), ("E5", 1.0)],
    [("D5", 2.0), ("C5", 1.0), ("A4", 1.0)],
    [("G4", 1.0), ("A4", 1.0), ("C5", 2.0)],   # 回到主音，可无缝接回开头
]

# 左手：每小节一个低八度长音（很轻）
_BASS = ["C3", "G3", "A3", "E3", "C3", "A3", "G3", "C3"]


# ---------------------------------------------------------------------------
# 合成
# ---------------------------------------------------------------------------

def _expected_frames():
    """整段音乐应有的采样帧数。"""
    return int(round(_TOTAL_BEATS * _BEAT * _SAMPLE_RATE))


def _synth_note(freq, n_samples, bright=1.0):
    """合成单个八音盒音符。

    正弦基频 + 2/3 次泛音，三个泛音各自带指数衰减（高次泛音衰减更快，
    模拟金属簧片"叮"的一下），外加 5ms 起音和 45ms 收尾淡出。
    """
    n = int(n_samples)
    if n <= 0 or freq <= 0.0:
        return array.array("d", bytes(8 * max(0, n)))
    buf = array.array("d", bytes(8 * n))
    w1 = 2.0 * math.pi * freq / _SAMPLE_RATE
    w2 = w1 * 2.0
    w3 = w1 * 3.0
    atk = max(1, int(_ATTACK * _SAMPLE_RATE))
    rel = max(1, int(_RELEASE * _SAMPLE_RATE))
    rel_start = n - rel
    # (振幅, 衰减时间常数)
    a1, t1 = 1.00, 0.95
    a2, t2 = 0.30 * bright, 0.40
    a3, t3 = 0.12 * bright, 0.18
    d1 = math.exp(-1.0 / (t1 * _SAMPLE_RATE))
    d2 = math.exp(-1.0 / (t2 * _SAMPLE_RATE))
    d3 = math.exp(-1.0 / (t3 * _SAMPLE_RATE))
    k1 = k2 = k3 = 1.0
    p1 = p2 = p3 = 0.0
    sin = math.sin
    for i in range(n):
        if k1 < 0.0006 and k2 < 0.004 and k3 < 0.004:
            break  # 已经衰减到听不见，后面全是 0
        g = 1.0
        if i < atk:
            g = float(i) / atk
        if i >= rel_start:
            g *= float(n - i) / rel
        v = a1 * k1 * sin(p1)
        if k2 > 0.004:
            v += a2 * k2 * sin(p2)
        if k3 > 0.004:
            v += a3 * k3 * sin(p3)
        buf[i] = v * g
        p1 += w1
        p2 += w2
        p3 += w3
        k1 *= d1
        k2 *= d2
        k3 *= d3
    return buf


def _add_wrapped(dst, src, start, gain=1.0):
    """把 src 叠加到 dst 的 start 位置，超出结尾的部分环绕回开头。

    这样每个音符的余音会自然"接"到下一轮循环的开头，循环点没有断口。
    """
    n = len(dst)
    if not n:
        return
    idx = int(start) % n
    if gain == 1.0:
        for v in src:
            dst[idx] += v
            idx += 1
            if idx >= n:
                idx = 0
    else:
        for v in src:
            dst[idx] += v * gain
            idx += 1
            if idx >= n:
                idx = 0


def _synthesize():
    """把整段曲子合成到一个 float64 缓冲区（长度 = 24 秒）。"""
    n_total = _expected_frames()
    mix = array.array("d", bytes(8 * n_total))
    cache = {}

    def note(freq, nsamp, bright):
        key = (round(freq, 3), int(nsamp), int(bright * 100))
        buf = cache.get(key)
        if buf is None:
            buf = _synth_note(freq, nsamp, bright)
            cache[key] = buf
        return buf

    beat_pos = 0.0
    for bar_i, bar in enumerate(_MELODY):
        for name, beats in bar:
            dur = beats * _BEAT
            nsamp = int(round((dur + _TAIL) * _SAMPLE_RATE))
            start = int(round(beat_pos * _BEAT * _SAMPLE_RATE))
            _add_wrapped(mix, note(_NOTE_FREQ[name], nsamp, 1.0), start, 0.85)
            beat_pos += beats
        # 左手低八度长音：每小节一个，很轻
        bass_name = _BASS[bar_i % len(_BASS)]
        bass_start = int(round(bar_i * 4.0 * _BEAT * _SAMPLE_RATE))
        bass_n = int(round((4.0 * _BEAT + 2.0) * _SAMPLE_RATE))
        _add_wrapped(mix, note(_NOTE_FREQ[bass_name], bass_n, 0.45), bass_start, 0.30)
    return mix


def _normalize(mix):
    """归一化：峰值压在 0.45 以下，整体响度控制在 -18 dBFS 附近。"""
    n = len(mix)
    if not n:
        return mix
    peak = 0.0
    acc = 0.0
    for v in mix:
        av = v if v >= 0.0 else -v
        if av > peak:
            peak = av
        acc += v * v
    if peak <= 0.0:
        return mix
    scale = _PEAK_LIMIT / peak
    rms = math.sqrt(acc / n) * scale
    if rms > _TARGET_RMS > 0.0:
        scale *= _TARGET_RMS / rms
    if abs(scale - 1.0) > 1e-9:
        for i in range(n):
            mix[i] *= scale
    return mix


def _to_int16(samples):
    """float64 -> 16-bit PCM（小端由 array + byteswap 保证）。"""
    frames = array.array("h", bytes(2 * len(samples)))
    limit_hi = 32767
    limit_lo = -32768
    for i, v in enumerate(samples):
        x = int(v * 32767.0)
        if x > limit_hi:
            x = limit_hi
        elif x < limit_lo:
            x = limit_lo
        frames[i] = x
    if sys.byteorder == "big":
        frames.byteswap()
    return frames


def _write_wav(path, samples):
    path = os.path.abspath(path)
    frames = _to_int16(samples)
    payload = frames.tobytes()
    tmp_path = path + ".part"
    try:
        with wave.open(tmp_path, "wb") as fp:
            fp.setnchannels(_CHANNELS)
            fp.setsampwidth(_SAMPLE_WIDTH)
            fp.setframerate(_SAMPLE_RATE)
            fp.writeframes(payload)
        try:
            os.replace(tmp_path, path)
        except OSError:
            # 目标可能被占用（比如正在播放），退化为直接覆盖
            with wave.open(path, "wb") as fp:
                fp.setnchannels(_CHANNELS)
                fp.setsampwidth(_SAMPLE_WIDTH)
                fp.setframerate(_SAMPLE_RATE)
                fp.writeframes(payload)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def build_music(path):
    """合成八音盒摇篮曲并写入 path（会被 music_file 调用）。"""
    if not path:
        raise ValueError("path 不能为空")
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    samples = _normalize(_synthesize())
    _write_wav(path, samples)
    return path


# ---------------------------------------------------------------------------
# 缓存路径与可用性
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_cached_path = None


def is_available():
    """winsound 可用（Windows）返回 True。"""
    return winsound is not None and hasattr(winsound, "PlaySound")


def _is_valid_wav(path):
    """缓存校验：声道/采样宽/帧率/帧数都符合预期才算可用。"""
    try:
        if not path or not os.path.isfile(path) or os.path.getsize(path) <= 44:
            return False
        with wave.open(path, "rb") as fp:
            if fp.getnchannels() != _CHANNELS:
                return False
            if fp.getsampwidth() != _SAMPLE_WIDTH:
                return False
            if fp.getframerate() != _SAMPLE_RATE:
                return False
            if fp.getnframes() != _expected_frames():
                return False
        return True
    except Exception:
        return False


def _candidate_paths():
    """优先 %TEMP%，其次模块同目录。"""
    paths = []
    try:
        cache_dir = os.path.join(tempfile.gettempdir(), _CACHE_DIR_NAME)
        os.makedirs(cache_dir, exist_ok=True)
        paths.append(os.path.join(cache_dir, _CACHE_NAME))
    except Exception:
        pass
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        paths.append(os.path.join(here, "musicbox.wav"))
    except Exception:
        pass
    return paths


def music_file():
    """返回 WAV 绝对路径；首次调用时合成并写盘，之后复用缓存。"""
    global _cached_path
    with _lock:
        if _cached_path and _is_valid_wav(_cached_path):
            return _cached_path
        candidates = _candidate_paths()
        if not candidates:
            raise RuntimeError("找不到可写的音乐缓存目录")
        last_error = None
        for path in candidates:
            try:
                if not _is_valid_wav(path):
                    build_music(path)
                if not _is_valid_wav(path):
                    raise IOError("生成的 WAV 校验失败: %s" % path)
                _cached_path = os.path.abspath(path)
                return _cached_path
            except Exception as exc:  # 写盘失败就换下一个位置
                last_error = exc
        raise RuntimeError("无法生成八音盒音乐文件: %r" % (last_error,))


def _wav_duration(path):
    """读 WAV 头得到时长（秒），失败返回 0。"""
    try:
        with wave.open(path, "rb") as fp:
            rate = fp.getframerate() or 1
            return float(fp.getnframes()) / float(rate)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# 播放器
# ---------------------------------------------------------------------------

class MusicPlayer(object):
    """winsound 异步循环播放器，play() 不阻塞调用线程（Qt UI 线程安全）。"""

    def __init__(self, path=None):
        self._path = path
        self._lock = threading.RLock()
        self._playing = False
        self._thread = None
        self._gen = 0
        self._stop_evt = None
        self._last_error = None

    # -- 内部 ------------------------------------------------------------
    def _run(self, gen, stop_evt, ready_evt, path, loop, duration):
        ok = False
        try:
            flags = winsound.SND_FILENAME | winsound.SND_ASYNC
            if loop:
                flags |= winsound.SND_LOOP
            winsound.PlaySound(path, flags)
            ok = True
        except Exception:
            ok = False
        with self._lock:
            if self._gen == gen:
                self._playing = ok
                self._last_error = None if ok else "PlaySound 失败"
        ready_evt.set()
        if not ok:
            return
        if loop:
            while not stop_evt.wait(0.25):
                pass
        else:
            deadline = time.time() + max(0.0, duration)
            while not stop_evt.is_set() and time.time() < deadline:
                stop_evt.wait(0.1)
        with self._lock:
            if self._gen == gen:
                self._playing = False

    # -- 公开 ------------------------------------------------------------
    def play(self, loop=True):
        """开始播放；已在播放时直接返回 True。任何异常都返回 False。"""
        if not is_available():
            return False
        with self._lock:
            if self._playing:
                return True
        try:
            path = self._path or music_file()
        except Exception as exc:
            self._last_error = exc
            return False
        if not path or not os.path.isfile(path):
            self._last_error = "音乐文件不存在: %r" % (path,)
            return False
        duration = _wav_duration(path)
        with self._lock:
            if self._playing:
                return True
            self._gen += 1
            gen = self._gen
            stop_evt = threading.Event()
            ready_evt = threading.Event()
            self._stop_evt = stop_evt
        thread = threading.Thread(
            target=self._run,
            args=(gen, stop_evt, ready_evt, path, bool(loop), duration),
            name="LinPetMusicPlayer",
        )
        thread.daemon = True
        with self._lock:
            self._thread = thread
        thread.start()
        # 只等很短时间确认 PlaySound 没有抛异常，正常情况 < 10ms 返回
        ready_evt.wait(0.25)
        with self._lock:
            if self._gen != gen:
                return self._playing
            if ready_evt.is_set():
                return self._playing
            # PlaySound 还没返回（极慢的磁盘/杀软扫描）：先当作已开始
            return True

    def stop(self):
        """停止播放并复位状态；没有在播放时调用也安全。"""
        with self._lock:
            self._gen += 1
            self._playing = False
            stop_evt = self._stop_evt
            thread = self._thread
            self._stop_evt = None
            self._thread = None
        if stop_evt is not None:
            stop_evt.set()
        if is_available():
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        if thread is not None and thread is not threading.current_thread():
            try:
                thread.join(0.3)
            except Exception:
                pass

    def is_playing(self):
        """当前是否在播放（反映真实状态）。"""
        with self._lock:
            return bool(self._playing)


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------

_player = None
_player_lock = threading.Lock()


def _get_player():
    global _player
    with _player_lock:
        if _player is None:
            _player = MusicPlayer()
        return _player


def play():
    """用全局播放器开始循环播放，返回 bool。"""
    try:
        return _get_player().play(True)
    except Exception:
        return False


def stop():
    """用全局播放器停止播放。"""
    try:
        _get_player().stop()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------

def _self_test():
    print("winsound 可用:", is_available())
    t0 = time.time()
    path = music_file()
    cost = time.time() - t0
    size = os.path.getsize(path)
    with wave.open(path, "rb") as fp:
        ch = fp.getnchannels()
        sw = fp.getsampwidth()
        rate = fp.getframerate()
        nframes = fp.getnframes()
        dur = float(nframes) / float(rate or 1)
    print("WAV 路径 :", path)
    print("文件大小 : %d 字节 (%.2f MB)" % (size, size / 1048576.0))
    print("声道     :", ch, " 采样宽:", sw, " 帧率:", rate)
    print("帧数     :", nframes, " 时长: %.2f 秒" % dur)
    print("合成耗时 : %.2f 秒" % cost)
    print("MUSIC_VERSION:", MUSIC_VERSION)

    if "--play" in sys.argv and is_available():
        player = MusicPlayer(path)
        t0 = time.time()
        ok = player.play(True)
        print("play() ->", ok, " 返回耗时 %.3f 秒" % (time.time() - t0))
        time.sleep(3.0)
        player.stop()
        print("3 秒后已 stop，is_playing:", player.is_playing())


if __name__ == "__main__":
    _self_test()
