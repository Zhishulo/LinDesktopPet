"""听歌陪伴状态机（Lead 集成层，无 Qt / 无 winrt 依赖）

配合 now_playing.py 使用：
    tr = ListenTracker()
    r = tr.update(now_playing.current_track())   # 每 15 秒左右调一次
    if r["event"] in ("started", "resumed"):
        line = tr.greeting()
        if line: pet.say(line)
    seg = tr.prompt_segment()                    # 注入 build_system_prompt

设计要点：
- 纯函数 + 一个小状态机，方便离线单测；时间用 time.time()，可注入 now。
- 「同一首歌最多主动说一次」用曲目指纹去重；另加最小全局间隔，避免用户切歌时刷屏。
- 检测不到（track 为 None / playing False）时一律静默，不打扰。
"""

LISTEN_VERSION = 1

# 同一首歌两次主动搭话的最小间隔（秒）；用户循环听同一首歌时不至于反复念
SAME_TRACK_COOLDOWN = 180

# 任意两次主动搭话的最小间隔（秒），切歌很快时也不会刷屏
GLOBAL_COOLDOWN = 90

# 曲目信息的可信度门槛：>= 这个值才敢当成「确实在听这首」
# now_playing 的 SMTC 后端是 1.0；窗口标题推断是 0.7/0.65/0.5。
TRACK_CONFIDENT = 0.9

# 主动搭话的候选句（{} 由 title 填充）——证据强（SMTC）时用
GREETINGS = (
    "唔？幸歌在听《{title}》呀～麟也凑过来听一会儿。",
    "《{title}》！麟听到啦，一边听一边陪着你～",
    "这首《{title}》感觉不错诶，幸歌今天心情怎么样？",
    "原来在听《{title}》呀，麟悄悄跟着哼两句～",
)

# 证据弱（只能读到播放器窗口标题）时用：不硬断言「你在听」，留出错余地，
# 也避免把网易云那种「上次残留的标题」当成正在播放而说错话。
SOFT_GREETINGS = (
    "唔…麟好像听到《{title}》？是幸歌在放歌吗～",
    "（竖起小耳朵）好像有《{title}》的旋律，是幸歌在听吗？",
    "麟隐约听到《{title}》诶…要一起听吗？",
)


def track_key(track):
    """曲目指纹：标题+艺人+播放器，用于判断是否换歌"""
    if not track:
        return ""
    parts = []
    for k in ("title", "artist", "app"):
        v = track.get(k) or ""
        parts.append(str(v).strip().lower())
    return "|".join(parts)


def describe(track):
    """《标题》— 艺人（播放器），缺字段时自动省略"""
    if not track:
        return ""
    title = (track.get("title") or "").strip()
    artist = (track.get("artist") or "").strip()
    app = (track.get("app") or "").strip()
    if not title:
        return ""
    s = "《" + title + "》"
    if artist:
        s += " — " + artist
    if app:
        s += "（" + app + "）"
    return s


def confidence_of(track):
    """取曲目可信度；缺字段按 1.0（老版本 now_playing 没有这个字段）"""
    try:
        c = float(track.get("confidence", 1.0))
    except (TypeError, ValueError):
        return 1.0
    return min(1.0, max(0.0, c))


def prompt_segment(track, playing=None):
    """给 build_volatile_prompt 用的实时段；没有信息时返回空串。

    注意：这段每秒都可能变（换歌就变），只能放易变段，不能放稳定段，
    否则会把整段 system 前缀缓存打断。
    """
    desc = describe(track)
    if not desc:
        return ""
    weak = confidence_of(track) < TRACK_CONFIDENT
    is_playing = (track.get("playing") is not False) if playing is None else bool(playing)
    if weak:
        # 只读到窗口标题时不能断言播放状态：说「可能/隐约」，并让模型别追问细节
        return ("【幸歌可能在听】麟隐约听到幸歌在放 " + desc +
                "（只看到播放器窗口标题，未必准确）。可以顺口问一句是不是在听歌，"
                "但不要断言、不要编造歌曲细节。")
    if is_playing:
        return "【幸歌正在听】幸歌正在听 " + desc + "。可以自然地和幸歌聊聊这首歌，但不要每句都提。"
    return "【幸歌刚在听】幸歌刚才在听 " + desc + "（已暂停）。"


class ListenTracker(object):
    def __init__(self, same_cooldown=SAME_TRACK_COOLDOWN, global_cooldown=GLOBAL_COOLDOWN):
        self.same_cooldown = float(same_cooldown)
        self.global_cooldown = float(global_cooldown)
        self.track = None
        self.key = ""
        self.playing = False
        self.confidence = 0.0
        self.started_at = 0.0
        self._greeted = {}      # key -> 上次搭话时间
        self._last_greet = 0.0

    # ---------- 输入 ----------
    def update(self, track, now=None):
        """喂入一次采样，返回事件字典：
        {event: none|started|resumed|paused|stopped|switched|same, track, key, got_track}
        """
        if now is None:
            import time as _t
            now = _t.time()
        got = bool(track) and bool((track.get("title") or "").strip())
        old_key, old_playing = self.key, self.playing

        if not got:
            event = "stopped" if self.track is not None else "none"
            self.track, self.key, self.playing, self.started_at = None, "", False, 0.0
            self.confidence = 0.0
            return {"event": event, "track": None, "key": "",
                    "got_track": False, "confidence": 0.0}

        new_key = track_key(track)
        is_playing = track.get("playing") is not False
        self.track = track
        self.key = new_key
        self.playing = is_playing
        self.confidence = confidence_of(track)

        if new_key != old_key:
            # 首次识别 / 换歌
            self.started_at = now
            event = "started" if not old_key else "switched"
        elif is_playing and not old_playing:
            event = "resumed"
            if not self.started_at:
                self.started_at = now
        elif (not is_playing) and old_playing:
            event = "paused"
        else:
            event = "same"
        return {"event": event, "track": track, "key": new_key, "got_track": True,
                "confidence": self.confidence}

    def reset(self):
        self.track, self.key, self.playing, self.started_at = None, "", False, 0.0
        self.confidence = 0.0
        self._greeted = {}
        self._last_greet = 0.0

    # ---------- 输出 ----------
    def greeting(self, now=None):
        """若此刻适合主动搭话，返回一句话并记录冷却；否则返回 None"""
        import time as _t
        if now is None:
            now = _t.time()
        if not self.track or not self.playing or not self.key:
            return None
        if now - self._last_greet < self.global_cooldown:
            return None
        if now - self._greeted.get(self.key, 0) < self.same_cooldown:
            return None
        title = (self.track.get("title") or "").strip()
        if not title:
            return None
        import random
        pool = GREETINGS if self.confidence >= TRACK_CONFIDENT else SOFT_GREETINGS
        line = random.choice(pool).format(title=title)
        self._greeted[self.key] = now
        self._last_greet = now
        # 只保留最近 50 条去重记录，防止长时间运行后无限增长
        if len(self._greeted) > 50:
            for k in sorted(self._greeted, key=self._greeted.get)[:len(self._greeted) - 50]:
                self._greeted.pop(k, None)
        return line

    def describe(self):
        return describe(self.track)

    def prompt_segment(self):
        return prompt_segment(self.track, self.playing)

    def status_line(self):
        """给 #在听 / 菜单 用的一句话"""
        desc = describe(self.track)
        if not desc:
            return "麟没听到幸歌在放音乐哦～（如果要麟自己放，就输入 #音乐）"
        if self.confidence < TRACK_CONFIDENT:
            # 弱证据：标题可能只是播放器残留，措辞要留余地
            return "麟好像听到 " + desc + "…不过只看到播放器窗口，未必准哦"
        if self.playing:
            return "幸歌正在听 " + desc + "，麟也在旁边悄悄听～"
        return "幸歌刚才在听 " + desc + "，现在暂停了。"
