# -*- coding: utf-8 -*-
"""麟的「主动关心」感知层（care.py）。

定位
    只读 music/mood.json 与 music/assessment.json，把「幸歌最近的状态」
    翻译成几种可用的关心素材：主动关心的一句话、注入 LLM 的易变提示段、
    以及「麟关心一下」按钮用的一段话。所有文案温柔、具体、不评判。

设计约束
    - 纯标准库（json / os / time），不依赖 Qt、网络或第三方包；
      可被 UI 线程低频调用，单次 observe 目标 < 20ms。
    - 只读心理数据，绝不回写 mood.json / assessment.json；
      本模块自己的冷却状态写在 data_dir/care.json（原子替换）。
    - 数据只用来「描述看到了什么」，不贴诊断标签、不评判、不说教。
    - 数据不足 / 空的 mood.json（甚至 []）都优雅返回 level="none"。

对外 API
    CARE_VERSION
    COOLDOWN_SECONDS / INVITE_COOLDOWN_SECONDS / SAME_SIGNATURE_COOLDOWN
    CareState
    CareTracker(data_dir=None)
        observe(now=None) -> dict(level, reasons, stats, signature)
        should_care(now=None) -> str | None      # 不写盘
        mark_cared(now=None, signature=None)     # 写 care.json
        care_message(level=None, reasons=None, now=None) -> str
        prompt_segment(now=None) -> str
        analysis_invite(now=None) -> str
        status_line(now=None) -> str
        should_invite(now=None) -> bool          # 额外便利方法
        mark_invited(now=None)                   # 额外便利方法
        reset()
"""

import json
import os
import time

CARE_VERSION = 1

CARE_FILE = "care.json"
MOOD_FILE = "mood.json"
ASSESSMENT_FILE = "assessment.json"

# ---- 观察窗口（天，含今天） ----
CARE_WINDOW_DAYS = 3
LONG_WINDOW_DAYS = 7

# ---- 心情信号阈值 ----
MIN_RECORDS_FOR_RATIO = 3     # 短窗至少 3 条才计算负面占比，避免单条就报警
NEGATIVE_RATIO_GENTLE = 0.40  # 负面占比 >= 40% -> 轻提示
NEGATIVE_RATIO_NOTICE = 0.60  # >= 60% -> 留意
NEGATIVE_RATIO_CONCERN = 0.80 # >= 80% -> 更重的措辞（仍不单独升到 concern）
HEAVY_INTENSITY = 4           # 强度 >= 4 视为「强度较高」
HEAVY_COUNT_NOTICE = 3        # 7 天内高强度负面 >= 3 次 -> 留意
HEAVY_COUNT_STRONG = 5        # 7 天内高强度负面 >= 5 次 -> 更强权重

# ---- 沉默（长期不记录）信号 ----
SILENT_DAYS = 3               # 距最后一条心情 >= 3 天算沉默
SILENT_LOOKBACK_DAYS = 10     # 回看沉默前 10 天
SILENT_MIN_NEGATIVES = 3      # 那阵子至少有 3 条负面
SILENT_MIN_INTENSITY = 3      # 且强度 >= 3

# ---- 评测信号 ----
ASSESS_LOOKBACK_DAYS = 30     # 最近 30 天内的评测才算「最近一次」
CRISIS_LOOKBACK_DAYS = 14     # 危机标记回看 14 天
ASSESS_SEVERITY_NOTICE = 0.45 # severity >= 0.45 -> 留意
ASSESS_SEVERITY_CONCERN = 0.70# severity >= 0.70 -> concern

# ---- 冷却（秒，可调） ----
COOLDOWN_SECONDS = 3 * 3600        # 两次主动关心之间至少 3 小时
INVITE_COOLDOWN_SECONDS = 8 * 3600 # 两次「邀请一起分析」之间至少 8 小时
SAME_SIGNATURE_COOLDOWN = 12 * 3600# 状态没变化时，至少 12 小时不重复关心
MESSAGE_ROTATE_SECONDS = 1800      # 同一等级的文案按 30 分钟轮换
MAX_MESSAGE_CHARS = 60             # 单句关心话不超过 60 字

# ---- 等级 ----
LEVEL_NONE = "none"
LEVEL_GENTLE = "gentle"
LEVEL_NOTICE = "notice"
LEVEL_CONCERN = "concern"
LEVEL_ORDER = {LEVEL_NONE: 0, LEVEL_GENTLE: 1, LEVEL_NOTICE: 2, LEVEL_CONCERN: 3}

# ---- 数据词表（与 mood.py 的 MOOD_KEYS 一致，另兼容中文写法） ----
NEGATIVE_MOODS = {"sad", "anxious", "angry", "lonely", "tired"}
MOOD_ALIASES = {
    "难过": "sad", "悲伤": "sad", "低落": "sad",
    "焦虑": "anxious", "紧张": "anxious",
    "愤怒": "angry", "生气": "angry",
    "孤独": "lonely", "孤单": "lonely",
    "疲惫": "tired", "累": "tired",
}

# 只认「症状/压力」类量表：人格画像、情绪智力、心理需求等量表即使 severity
# 算得高也常常是计分方向不同，参与判定只会误报，所以按 key 白名单 + 症状筛查类。
RISK_SCALES = {"phq9", "gad7", "sds", "sas", "bdc", "pss", "scl90"}
RISK_CATEGORIES = ("症状筛查",)
CRITICAL_WORDS = ("重度", "中重度", "严重", "极重", "高风险", "危机")
ELEVATED_WORDS = ("中度", "较高", "偏高", "明显", "较差", "不和谐",
                  "待提升", "偏低", "满足不足")

CRISIS_HOTLINE = "12356"

_DEFAULT_DETAIL = {
    LEVEL_GENTLE: "你最近好像有点累",
    LEVEL_NOTICE: "最近的状态有点沉",
    LEVEL_CONCERN: "最近的状态有点重",
}

_OPENERS = {
    LEVEL_GENTLE: [
        "幸歌，麟好像察觉到{d}。",
        "（轻轻蹭了蹭你）{d}……",
        "麟悄悄看了一眼，{d}。",
    ],
    LEVEL_NOTICE: [
        "幸歌，麟有点担心：{d}。",
        "麟注意到{d}。",
        "（把尾巴搭在你手边）{d}。",
    ],
}

_CLOSERS = {
    LEVEL_GENTLE: [
        "不用急着说什么，麟就在旁边。",
        "今天要不要稍微慢一点？",
        "喝口水、伸个懒腰，好吗？",
    ],
    LEVEL_NOTICE: [
        "这不是你的错，想说什么麟都听着。",
        "要不要跟麟说说今天怎么了？",
        "先不用撑着，歇一会儿也可以。",
    ],
    LEVEL_CONCERN: [
        "这不是你的错。很难受的话，可以打心理援助热线 12356。",
        "你不是一个人。需要时可以拨打心理援助热线 12356。",
        "这不是你的错。12356 是心理援助热线，随时可以打。",
    ],
}

_INVITES = [
    "要不要和麟一起看看最近是什么让你有点累？没有对错，慢慢说就好。",
    "想不想跟麟聊聊最近发生的事？没有对错，也不用急着说清楚。",
    "要不要一起理一理最近的感受？没有对错，说什么都可以。",
]

_MESSAGE_LEVELS = (LEVEL_GENTLE, LEVEL_NOTICE, LEVEL_CONCERN)


# --------------------------------------------------------------------------
# 小工具（不引入 datetime：用整数的「连续天数」做日期比较）
# --------------------------------------------------------------------------
def _civil_day(year, month, day):
    """把年月日换算成连续的整数天数（Gregorian，忽略时区/DST）。"""
    y = int(year)
    m = int(month)
    d = int(day)
    if m <= 2:
        y -= 1
        m += 12
    return (365 * y + y // 4 - y // 100 + y // 400
            + (153 * (m - 3) + 2) // 5 + d)


def _day_number(text):
    """'YYYY-MM-DD HH:MM' / 'YYYY-MM-DD' -> 整数天数；解析失败返回 None。"""
    s = (text or "")
    if not isinstance(s, str):
        return None
    s = s.strip()[:10]
    if len(s) < 10:
        return None
    try:
        t = time.strptime(s, "%Y-%m-%d")
    except Exception:
        return None
    return _civil_day(t.tm_year, t.tm_mon, t.tm_mday)


def _now(now=None):
    """now 允许传 int/float（秒）或 None。"""
    if now is None:
        return time.time()
    try:
        return float(now)
    except (TypeError, ValueError):
        return time.time()


def _today_number(now):
    lt = time.localtime(now)
    return _civil_day(lt.tm_year, lt.tm_mon, lt.tm_mday)


def _age_days(today, day):
    """记录距今天数；未来时间（系统时间被改过）按 0 处理。"""
    return max(0, int(today) - int(day))


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _contains(text, words):
    if not text:
        return False
    for w in words:
        if w in text:
            return True
    return False


def _clip(text, limit=MAX_MESSAGE_CHARS):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:max(1, limit - 1)] + "…"


def _rotate(items, now, salt=0, period=MESSAGE_ROTATE_SECONDS):
    """按时间从若干句里轮换一句（同一时间点结果稳定）。"""
    if not items:
        return ""
    index = int(now // max(1, period)) + int(salt)
    return items[index % len(items)]


def _records_of(payload):
    """兼容 mood.json 的两种形态：{'records': [...]} 或直接 [...]。"""
    if isinstance(payload, dict):
        records = payload.get("records")
    elif isinstance(payload, list):
        records = payload
    else:
        records = None
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict)]


def _record_day(record):
    return _day_number(record.get("created_at") or record.get("time")
                       or record.get("date") or "")


def _record_mood(record):
    key = str(record.get("mood") or record.get("emotion") or "").strip()
    if not key:
        return ""
    if key in NEGATIVE_MOODS:
        return key
    return MOOD_ALIASES.get(key, key.lower())


def _record_intensity(record):
    value = _number(record.get("intensity"))
    if value is None:
        value = 3.0
    return max(1, min(5, int(round(value))))


def _is_negative(record):
    return _record_mood(record) in NEGATIVE_MOODS


def _severity(record):
    """评测的严重度 0~1：优先用 severity 字段，缺失时用 total/max 推算。"""
    sev = _number(record.get("severity"))
    if sev is None:
        sev = 0.0
    if sev <= 0.0:
        total = _number(record.get("total"))
        maxv = _number(record.get("max"))
        if total is not None and maxv:
            sev = total / maxv
    return max(0.0, min(1.0, sev))


def _is_risk_assessment(record):
    key = str(record.get("scale_key") or "").strip()
    category = str(record.get("category") or "").strip()
    return key in RISK_SCALES or category in RISK_CATEGORIES


def _detail_from_reasons(reasons):
    """取第一条原因做「具体一点」的细节；去尾标点。"""
    if not reasons:
        return ""
    first = reasons[0]
    if not isinstance(first, str):
        return ""
    return first.strip().rstrip("。；;，,？?！!")


def _empty_observation():
    return {
        "level": LEVEL_NONE,
        "reasons": [],
        "stats": {
            "mood_total": 0, "mood_3d": 0, "negative_3d": 0,
            "negative_ratio_3d": 0.0, "avg_intensity_3d": 0.0,
            "mood_7d": 0, "heavy_7d": 0, "days_since_last_mood": None,
            "assessment": {"present": False, "scale_key": "", "total": None,
                           "max": None, "severity": 0.0, "level": "",
                           "days_ago": None, "crisis": False},
            "crisis": False, "score": 0, "signals": [],
        },
        "signature": LEVEL_NONE,
    }


# --------------------------------------------------------------------------
# 持久化状态
# --------------------------------------------------------------------------
class CareState(object):
    """care.json 的内存形态；损坏/缺失时全部按默认值处理。"""

    FIELDS = ("version", "last_care_ts", "last_invite_ts", "last_signature",
              "last_level", "cared_count", "invited_count")

    def __init__(self, data=None):
        if not isinstance(data, dict):
            data = {}
        self.version = CARE_VERSION
        self.last_care_ts = _number(data.get("last_care_ts")) or 0.0
        self.last_invite_ts = _number(data.get("last_invite_ts")) or 0.0
        signature = data.get("last_signature")
        self.last_signature = signature if isinstance(signature, str) else ""
        level = data.get("last_level")
        self.last_level = level if isinstance(level, str) else ""
        self.cared_count = self._count(data.get("cared_count"))
        self.invited_count = self._count(data.get("invited_count"))

    @staticmethod
    def _count(value):
        number = _number(value)
        if number is None or number < 0:
            return 0
        return int(number)

    def to_dict(self):
        return {
            "version": CARE_VERSION,
            "last_care_ts": self.last_care_ts,
            "last_invite_ts": self.last_invite_ts,
            "last_signature": self.last_signature,
            "last_level": self.last_level,
            "cared_count": self.cared_count,
            "invited_count": self.invited_count,
        }

    @classmethod
    def from_dict(cls, data):
        return cls(data)

    def reset(self):
        self.last_care_ts = 0.0
        self.last_invite_ts = 0.0
        self.last_signature = ""
        self.last_level = ""
        self.cared_count = 0
        self.invited_count = 0
        self.version = CARE_VERSION
        return self


# --------------------------------------------------------------------------
# 主类
# --------------------------------------------------------------------------
class CareTracker(object):
    """读取心理数据、判定状态、生成关心文案。

    data_dir 传 None 时用 care.py 所在目录（即 music/，与 mood.json 同级）。
    只读 mood.json / assessment.json，只写 care.json。
    """

    def __init__(self, data_dir=None):
        base = data_dir or os.path.dirname(os.path.abspath(__file__))
        self.data_dir = os.path.realpath(os.path.abspath(base))
        self.mood_path = os.path.realpath(os.path.join(self.data_dir, MOOD_FILE))
        self.assessment_path = os.path.realpath(os.path.join(self.data_dir, ASSESSMENT_FILE))
        self.state_path = os.path.realpath(os.path.join(self.data_dir, CARE_FILE))
        # 纵深防御：三个文件路径都必须落在数据目录内（调用方传入的都是常量文件名）
        for p in (self.mood_path, self.assessment_path, self.state_path):
            if p != self.data_dir and not p.startswith(self.data_dir + os.sep):
                raise ValueError("care 数据路径越界: %r" % (p,))
        self._cache = {}
        self.state = CareState(self._read_json(self.state_path))

    # ---------------- 文件读取（带 mtime 缓存，保证 observe 够快） ----------------
    def _read_json(self, path):
        try:
            stat = os.stat(path)
        except OSError:
            self._cache.pop(path, None)
            return None
        key = (stat.st_mtime_ns, stat.st_size)
        cached = self._cache.get(path)
        if cached is not None and cached[0] == key:
            return cached[1]
        data = None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            data = None
        self._cache[path] = (key, data)
        return data

    def _save_state(self):
        """原子写入 care.json；写失败静默（关心功能不该弄崩 UI）。"""
        try:
            payload = self.state.to_dict()
            temp_path = self.state_path + ".tmp"
            # self.state_path 已在 __init__ 做过防穿越校验（必须落在数据目录内）
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.state_path)
            try:
                stat = os.stat(self.state_path)
                self._cache[self.state_path] = ((stat.st_mtime_ns, stat.st_size),
                                                dict(payload))
            except OSError:
                self._cache.pop(self.state_path, None)
        except Exception:
            pass

    # ---------------- 观察 ----------------
    def observe(self, now=None):
        """只读观察一次，返回 {'level','reasons','stats','signature'}；不写盘。"""
        moment = _now(now)
        try:
            return self._observe(moment)
        except Exception:
            return _empty_observation()

    def _observe(self, moment):
        today = _today_number(moment)

        # ---- 心情记录：按「距今天数」索引 ----
        mood_entries = []
        for record in _records_of(self._read_json(self.mood_path)):
            day = _record_day(record)
            if day is None:
                continue
            mood_entries.append((_age_days(today, day), record))
        mood_entries.sort(key=lambda item: item[0])

        recent3 = [item for item in mood_entries if item[0] < CARE_WINDOW_DAYS]
        recent7 = [item for item in mood_entries if item[0] < LONG_WINDOW_DAYS]
        count3 = len(recent3)
        negative3 = sum(1 for item in recent3 if _is_negative(item[1]))
        ratio3 = (negative3 / float(count3)) if count3 else 0.0
        intensities3 = [_record_intensity(item[1]) for item in recent3]
        avg3 = (sum(intensities3) / float(len(intensities3))) if intensities3 else 0.0
        heavy7 = sum(1 for item in recent7
                     if _is_negative(item[1])
                     and _record_intensity(item[1]) >= HEAVY_INTENSITY)
        days_since_last_mood = mood_entries[0][0] if mood_entries else None

        signals = []

        # a) 最近 3 天负面占比偏高
        if count3 >= MIN_RECORDS_FOR_RATIO and ratio3 >= NEGATIVE_RATIO_GENTLE:
            weight = 1
            if ratio3 >= NEGATIVE_RATIO_CONCERN:
                weight = 3
            elif ratio3 >= NEGATIVE_RATIO_NOTICE:
                weight = 2
            signals.append({
                "kind": "ratio",
                "weight": weight,
                "reason": "最近 {} 天记了 {} 次心情，其中 {} 次偏低落".format(
                    CARE_WINDOW_DAYS, count3, negative3),
            })

        # b) 最近 7 天多次高强度负面
        if heavy7 >= HEAVY_COUNT_NOTICE:
            weight = 3 if heavy7 >= HEAVY_COUNT_STRONG else 2
            signals.append({
                "kind": "heavy",
                "weight": weight,
                "reason": "最近 {} 天有 {} 次情绪偏沉、强度较高".format(
                    LONG_WINDOW_DAYS, heavy7),
            })

        # ---- 评测记录 ----
        assess_entries = []
        for record in _records_of(self._read_json(self.assessment_path)):
            day = _record_day(record)
            if day is None:
                continue
            assess_entries.append((_age_days(today, day), record))
        assess_entries.sort(key=lambda item: item[0])

        crisis = False
        for age, record in assess_entries:
            if age <= CRISIS_LOOKBACK_DAYS and (record.get("crisis")
                                                or record.get("crisis_items")):
                crisis = True
                break

        assess_info = {
            "present": False, "scale_key": "", "total": None, "max": None,
            "severity": 0.0, "level": "", "days_ago": None, "crisis": crisis,
        }
        critical = False

        # c) 最近一次评测：分数/风险提示偏高
        if assess_entries and assess_entries[0][0] <= ASSESS_LOOKBACK_DAYS:
            age, record = assess_entries[0]
            severity = _severity(record)
            key = str(record.get("scale_key") or "")
            level_text = str(record.get("level") or "")
            assess_info.update({
                "present": True,
                "scale_key": key,
                "total": record.get("total"),
                "max": record.get("max"),
                "severity": severity,
                "level": level_text,
                "days_ago": age,
            })
            if _is_risk_assessment(record):
                critical = (severity >= ASSESS_SEVERITY_CONCERN
                            or _contains(level_text, CRITICAL_WORDS))
                if (critical
                        or severity >= ASSESS_SEVERITY_NOTICE
                        or _contains(level_text, ELEVATED_WORDS)):
                    signals.append({
                        "kind": "assess",
                        "weight": 3,
                        "reason": "最近一次心理评测的分数偏高",
                    })

        # d) 长期没记录，但此前持续低落（只轻提示，不追问）
        if days_since_last_mood is not None and days_since_last_mood >= SILENT_DAYS:
            prior_negative = 0
            for age, record in mood_entries:
                if age < days_since_last_mood:
                    continue
                if age > days_since_last_mood + SILENT_LOOKBACK_DAYS:
                    continue
                if (_is_negative(record)
                        and _record_intensity(record) >= SILENT_MIN_INTENSITY):
                    prior_negative += 1
            if prior_negative >= SILENT_MIN_NEGATIVES:
                signals.append({
                    "kind": "silent",
                    "weight": 1,
                    "reason": "已经有 {} 天没有记录心情了，之前那阵子似乎不太好受".format(
                        days_since_last_mood),
                })

        # ---- 定级 ----
        priority = {"assess": 0, "ratio": 1, "heavy": 2, "silent": 3}
        signals.sort(key=lambda signal: priority.get(signal["kind"], 9))
        score = sum(signal["weight"] for signal in signals)
        kinds = set(signal["kind"] for signal in signals)

        if score >= 3:
            level = LEVEL_NOTICE
        elif score >= 1:
            level = LEVEL_GENTLE
        else:
            level = LEVEL_NONE

        # 只有「沉默」这一个信号时，最多轻提示，绝不追问
        if kinds and kinds <= {"silent"}:
            level = LEVEL_GENTLE

        # 危机 / 高风险评测 -> concern（只有评测能升到这一级）
        if crisis or critical:
            level = LEVEL_CONCERN

        reasons = [signal["reason"] for signal in signals]
        if crisis:
            crisis_reason = "最近一次心理评测里有需要留意的风险提示"
            if crisis_reason not in reasons:
                reasons.insert(0, crisis_reason)

        signature = "|".join([
            level,
            "3d%d/%d" % (count3, negative3),
            "7d%d/%d" % (len(recent7), heavy7),
            "as:%s@%s/%s%s" % (
                assess_info["scale_key"] or "-",
                assess_info["days_ago"] if assess_info["present"] else "-",
                "C" if crisis else "",
                "H" if critical else "",
            ),
            "silent%d" % (days_since_last_mood
                          if days_since_last_mood is not None else -1),
        ])

        stats = {
            "mood_total": len(mood_entries),
            "mood_3d": count3,
            "negative_3d": negative3,
            "negative_ratio_3d": round(ratio3, 3),
            "avg_intensity_3d": round(avg3, 2),
            "mood_7d": len(recent7),
            "heavy_7d": heavy7,
            "days_since_last_mood": days_since_last_mood,
            "assessment": assess_info,
            "crisis": crisis,
            "score": score,
            "signals": [signal["kind"] for signal in signals],
        }
        return {
            "level": level,
            "reasons": reasons,
            "stats": stats,
            "signature": signature,
        }

    # ---------------- 主动关心 ----------------
    def should_care(self, now=None):
        """满足条件时返回一句主动关心的话，否则 None。本方法不写盘。"""
        moment = _now(now)
        observation = self.observe(moment)
        if observation["level"] == LEVEL_NONE:
            return None
        state = self.state
        last_care_ts = state.last_care_ts
        if last_care_ts and moment - last_care_ts < COOLDOWN_SECONDS:
            return None
        if (state.last_signature
                and state.last_signature == observation["signature"]
                and moment - last_care_ts < SAME_SIGNATURE_COOLDOWN):
            return None
        return self.care_message(observation["level"],
                                 observation["reasons"], moment)

    def mark_cared(self, now=None, signature=None):
        """记录一次主动关心（写 care.json）。"""
        moment = _now(now)
        if signature is None:
            observation = self.observe(moment)
            signature = observation.get("signature", "")
            self.state.last_level = observation.get("level", "")
        state = self.state
        state.last_care_ts = moment
        state.last_signature = signature or ""
        if not state.last_level and state.last_signature:
            state.last_level = state.last_signature.split("|")[0]
        state.cared_count = int(state.cared_count or 0) + 1
        self._save_state()
        return state

    def should_invite(self, now=None):
        """是否值得邀请一起分析（供 UI 低频调用；不写盘）。"""
        moment = _now(now)
        observation = self.observe(moment)
        if observation["level"] == LEVEL_NONE:
            return False
        last = self.state.last_invite_ts
        if last and moment - last < INVITE_COOLDOWN_SECONDS:
            return False
        return True

    def mark_invited(self, now=None):
        """记录一次「邀请一起分析」（写 care.json）。"""
        moment = _now(now)
        self.state.last_invite_ts = moment
        self.state.invited_count = int(self.state.invited_count or 0) + 1
        self._save_state()
        return self.state

    # ---------------- 文案 ----------------
    def care_message(self, level=None, reasons=None, now=None):
        """按等级生成一句 <= 60 字的关心话；level 为 none 时返回空串。"""
        moment = _now(now)
        if level is None or reasons is None:
            observation = self.observe(moment)
            if level is None:
                level = observation.get("level")
            if reasons is None:
                reasons = observation.get("reasons") or []
        if level not in _MESSAGE_LEVELS:
            return ""

        detail = _detail_from_reasons(reasons) or _DEFAULT_DETAIL.get(level, "")
        detail = detail.strip()

        if level == LEVEL_CONCERN:
            if "评测" in detail:
                opener = "幸歌，麟看到最近一次评测的结果有点重。"
            else:
                opener = "幸歌，麟觉得最近的状态有点重。"
            closer = _rotate(_CLOSERS[LEVEL_CONCERN], moment)
            return _clip(opener + closer)

        openers = _OPENERS[level]
        closer = _rotate(_CLOSERS[level], moment, salt=len(openers))
        opener_template = _rotate(openers, moment)

        candidates = [detail]
        if len(detail) > 12:
            candidates.append(_clip(detail, 12))
        for candidate in candidates:
            try:
                text = opener_template.format(d=candidate) + closer
            except Exception:
                continue
            if len(text) <= MAX_MESSAGE_CHARS:
                return text
        # 兜底：保证关键句完整，绝不在中间截断热线号码
        return _clip(closer)

    def prompt_segment(self, now=None):
        """注入 LLM 的易变提示段；数据不足/level=none 时返回空串（保护前缀缓存）。"""
        moment = _now(now)
        observation = self.observe(moment)
        level = observation.get("level", LEVEL_NONE)
        reasons = observation.get("reasons") or []
        if level == LEVEL_NONE or not reasons:
            return ""

        head = "【麟察觉到的情绪】" + "；".join(reasons) + "。"
        stance = "麟只是陪着，不评判、没有对错、不说教、不诊断。"
        guide_common = ("想分析时可以一起梳理：发生了什么、当时的感受、"
                        "需要什么、下次想怎么照顾自己。")
        if level == LEVEL_CONCERN:
            guide = ("如果幸歌愿意说，就陪他慢慢说；若他提到很难受，可以轻轻提醒："
                     "联系信任的人或心理援助热线 {}，不催、不评判。").format(
                         CRISIS_HOTLINE)
        elif level == LEVEL_NOTICE:
            guide = "如果幸歌想聊，就陪他慢慢说，不用一次讲清楚。"
        else:
            guide = ("如果幸歌愿意说，就问问他最近累不累；他不想说也没关系。")
        return head + stance + guide + guide_common

    def analysis_invite(self, now=None):
        """邀请一起分析的一句话。"""
        moment = _now(now)
        return _rotate(_INVITES, moment)

    def status_line(self, now=None):
        """「麟关心一下」用的段落：最近状态摘要 + 邀请（非评判）。"""
        moment = _now(now)
        observation = self.observe(moment)
        level = observation.get("level", LEVEL_NONE)
        reasons = observation.get("reasons") or []
        if level == LEVEL_NONE or not reasons:
            return ("麟最近没看到你的心情记录，不过没关系。"
                    "如果愿意，可以说说今天感觉怎么样，麟就安静听着。")
        summary = "；".join(reasons)
        if level == LEVEL_CONCERN:
            return ("麟看到{}。这不是你的错，也没有对错。想说的话麟都听；"
                    "如果很难受，请联系信任的人，或打心理援助热线 {}。").format(
                        summary, CRISIS_HOTLINE)
        if level == LEVEL_NOTICE:
            return "麟看到{}。这不是你的错，也没有对错。{}".format(
                summary, self.analysis_invite(moment))
        return "麟看到{}。{}".format(summary, self.analysis_invite(moment))

    def reset(self):
        """清空冷却状态（care.json 归零）。"""
        self.state = CareState()
        self._save_state()
        return self.state
