# -*- coding: utf-8 -*-
"""心理陪伴助手：情绪记录数据模型、统计分析与暖心台词库。

情绪体系参照 mood-tab（ClouderyStudio）：10 种情绪类型、5 级强度、
16 个预设触发标签 + 自定义标签、备注与日记。
所有数据 100% 本地存储（mood.json），绝不上传。
"""
import math
import random
import time
import datetime

from data import data_dir, load_json, save_json

MOOD_FILE = "mood.json"

# ---- 10 种情绪：键 / 中文名 / emoji / 颜色 / 麟式陪伴台词 ----
MOOD_TYPES = [
    {"key": "happy",    "name": "开心", "emoji": "😊", "color": "#FFB74D",
     "reply": "太好了！麟的尾巴都跟着摇起来啦～"},
    {"key": "calm",     "name": "平静", "emoji": "😌", "color": "#81C7E4",
     "reply": "嗯～和幸歌安安静静待着，也很好。"},
    {"key": "grateful", "name": "感恩", "emoji": "🙏", "color": "#A5D6A7",
     "reply": "被幸歌惦记着，麟也觉得好幸福～"},
    {"key": "excited",  "name": "兴奋", "emoji": "🤩", "color": "#FF8A65",
     "reply": "哇！幸歌这么兴奋，麟也好奇起来啦！"},
    {"key": "neutral",  "name": "一般", "emoji": "😐", "color": "#B0BEC5",
     "reply": "平平淡淡的一天，麟陪着幸歌就好～"},
    {"key": "tired",    "name": "疲惫", "emoji": "😴", "color": "#9575CD",
     "reply": "幸歌辛苦了……要不要麟给你捶捶肩、讲个小故事？"},
    {"key": "sad",      "name": "难过", "emoji": "😢", "color": "#64B5F6",
     "reply": "幸歌，麟在这里。想说的话，麟都会好好听的。"},
    {"key": "anxious",  "name": "焦虑", "emoji": "😰", "color": "#E57373",
     "reply": "别怕，深呼吸～麟陪着你，一步一步来。"},
    {"key": "angry",    "name": "愤怒", "emoji": "😠", "color": "#EF5350",
     "reply": "麟知道幸歌很生气……先喝口水，麟陪你缓缓。"},
    {"key": "lonely",   "name": "孤独", "emoji": "🥺", "color": "#78909C",
     "reply": "幸歌不是一个人哦，麟一直都在的。"},
]
MOOD_KEYS = [m["key"] for m in MOOD_TYPES]
MOOD_BY_KEY = {m["key"]: m for m in MOOD_TYPES}
# 需要重点关注的负向情绪
NEGATIVE_KEYS = {"sad", "anxious", "angry", "lonely", "tired"}

# ---- 16 个预设触发标签（参照 mood-tab） ----
MOOD_TAGS = [
    ("工作", "💼"), ("学习", "📚"), ("家庭", "🏠"), ("朋友", "👫"),
    ("恋爱", "❤️"), ("健康", "💊"), ("睡眠", "🛏️"), ("运动", "🏃"),
    ("美食", "🍜"), ("天气", "🌤️"), ("财务", "💰"), ("社交", "🗣️"),
    ("独处", "🧘"), ("音乐", "🎵"), ("旅行", "✈️"), ("其他", "✨"),
]

# 情绪强度 1-5 的名称
INTENSITY_NAMES = ["很轻微", "轻微", "中等", "较强", "非常强"]

# ---- 危机支持热线（请以官方最新信息为准） ----
CRISIS_LINES = [
    "全国统一心理援助热线：12356",
    "希望24热线：400-161-9995",
    "北京心理危机研究与干预中心：010-82951332",
]

# ---- 暖心寄语（本地台词，不构成医疗建议） ----
WARM_WORDS = {
    "通用": [
        "情绪没有对错，每一种感受都值得被认真对待。",
        "你不必总是坚强，偶尔停下来喘口气也没关系。",
        "今天也是努力活着的一天，这本身就很了不起。",
        "心里的乌云不会永远停着，天会亮的，你也会好的。",
        "慢慢来，你已经走了很远的路了。",
    ],
    "抑郁": [
        "此刻觉得累是正常的，不是你的错。",
        "黑暗里待久的人，也依然有资格期待光。",
        "你的感受真实存在，麟愿意陪着你慢慢走出来。",
    ],
    "焦虑": [
        "把注意力放回呼吸上：吸气……呼气……你现在是安全的。",
        "一次只做一件事，一件小事也算前进。",
        "焦虑是大脑在过度保护你，谢谢你，但可以先休息一下。",
    ],
    "双相": [
        "高高低低都是你的一部分，不必为此感到羞耻。",
        "波动不是失败，你正在学习与自己和平相处。",
    ],
    "强迫": [
        "那个念头不是你，你只是暂时被它打扰了。",
        "不需要完美，做到「足够好」就已经很好了。",
    ],
    "PTSD": [
        "你熬过了很艰难的事，现在你安全了。",
        "闪回来的时候，试着看看周围：此刻，你很安全。",
    ],
    "ADHD": [
        "不是你不努力，是你的大脑用了不同的运作方式。",
        "小步前进，每个开始都值得被庆祝。",
    ],
}


def default_moods():
    return {"records": [], "custom_tags": [], "streak_date": "", "streak": 0,
            "last_care_date": "",
            # 聊天情绪总结（mood_summary.py）的状态：上次总结时间 + 攒的待总结消息数
            "chat_summary_at": "", "chat_pending": 0}


def load_moods():
    data = load_json(MOOD_FILE, None)
    if data is None:
        data = default_moods()
        save_moods(data)
    for k, v in default_moods().items():
        data.setdefault(k, v)
    return data


def save_moods(data):
    save_json(MOOD_FILE, data)


def add_record(mood_key, intensity, tags, note="", diary="", source=""):
    """新增一条情绪记录，更新连续打卡天数，返回新记录。

    source：记录来源。手动记录留空；聊天总结写 "chat"（情绪历史面板
    会显示「AI总结」标记），与手动记录同一体系、可照常删除/统计。
    """
    data = load_moods()
    rec = {
        "mood": mood_key,
        "intensity": max(1, min(5, int(intensity))),
        "tags": [t for t in (tags or []) if t],
        "note": (note or "").strip(),
        "diary": (diary or "").strip(),
        "source": source or "",
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
    }
    data.setdefault("records", []).append(rec)
    # 连续打卡：与最近记录同一天不重复计数
    today = rec["created_at"][:10]
    last_date = data.get("streak_date", "")
    if last_date == today:
        pass
    elif last_date == (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d"):
        data["streak"] = data.get("streak", 0) + 1
        data["streak_date"] = today
    else:
        data["streak"] = 1
        data["streak_date"] = today
    save_moods(data)
    return rec


def delete_record(index):
    """按列表倒序索引删除记录（历史面板从左到右是最新）"""
    data = load_moods()
    records = data.get("records", [])
    if 0 <= index < len(records):
        records.pop(index)
        save_moods(data)
        return True
    return False


def mood_name(key):
    m = MOOD_BY_KEY.get(key)
    return m["name"] if m else key


def mood_emoji(key):
    m = MOOD_BY_KEY.get(key)
    return m["emoji"] if m else "❓"


def mood_reply(key):
    m = MOOD_BY_KEY.get(key)
    return m["reply"] if m else "麟会一直陪着幸歌的～"


def recent_records(records, days=7):
    """最近 N 天的记录"""
    cutoff = (datetime.date.today() - datetime.timedelta(days=days - 1)).strftime("%Y-%m-%d")
    return [r for r in records if (r.get("created_at") or "")[:10] >= cutoff]


def analyze(records):
    """情绪统计（移植自 mood-tab 的 MoodAnalysisService）：
    返回波动标准差、平均强度、记录数、预警原因、时段规律。"""
    if not records:
        return {"volatility": 0.0, "avg_intensity": 0.0, "count": 0,
                "warnings": [], "cycles": {}}
    recent = recent_records(records, 7)
    if not recent:
        return {"volatility": 0.0, "avg_intensity": 0.0, "count": 0,
                "warnings": [], "cycles": {}}
    ints = [max(1, min(5, int(r.get("intensity", 3)))) for r in recent]
    avg = sum(ints) / len(ints)
    std = 0.0
    if len(ints) > 1:
        var = sum((x - avg) ** 2 for x in ints) / len(ints)
        std = math.sqrt(var)
    warnings = []
    if std > 1.5:
        warnings.append("最近7天情绪波动较大（标准差 {:.2f}），建议多留意自己的情绪。".format(std))
    if _check_consecutive_diff(recent):
        warnings.append("检测到连续3天情绪强度剧烈变化，建议好好照顾自己。")
    cycles = _analyze_cycles(recent)
    return {"volatility": std, "avg_intensity": avg, "count": len(recent),
            "warnings": warnings, "cycles": cycles}


def _check_consecutive_diff(records):
    """按天分组，检查是否存在连续 3 天相邻平均强度差异 > 2"""
    daily = {}
    for r in records:
        day = (r.get("created_at") or "")[:10]
        daily.setdefault(day, []).append(max(1, min(5, int(r.get("intensity", 3)))))
    days = sorted(daily.keys())
    avgs = [sum(daily[d]) / len(daily[d]) for d in days]
    for i in range(len(avgs) - 2):
        if abs(avgs[i] - avgs[i + 1]) > 2 and abs(avgs[i + 1] - avgs[i + 2]) > 2:
            return True
    return False


def _analyze_cycles(records):
    """时段情绪规律：清晨/上午/下午/晚间/深夜 → 最常见情绪"""
    periods = {"清晨 (06-09)": {}, "上午 (09-12)": {}, "下午 (12-18)": {},
               "晚间 (18-22)": {}, "深夜 (22-06)": {}}
    for r in records:
        try:
            hour = int((r.get("created_at") or "")[11:13])
        except Exception:
            continue
        if 6 <= hour < 9:
            p = "清晨 (06-09)"
        elif 9 <= hour < 12:
            p = "上午 (09-12)"
        elif 12 <= hour < 18:
            p = "下午 (12-18)"
        elif 18 <= hour < 22:
            p = "晚间 (18-22)"
        else:
            p = "深夜 (22-06)"
        key = r.get("mood", "neutral")
        periods[p][key] = periods[p].get(key, 0) + 1
    result = {}
    for p, cnt in periods.items():
        if cnt:
            top = max(cnt.items(), key=lambda kv: kv[1])[0]
            result[p] = mood_name(top)
    return result


def pick_warm_word(condition=None):
    """随机一条暖心寄语；condition 可选：通用/抑郁/焦虑/双相/强迫/PTSD/ADHD"""
    if condition in WARM_WORDS:
        return random.choice(WARM_WORDS[condition])
    return random.choice(WARM_WORDS["通用"])
