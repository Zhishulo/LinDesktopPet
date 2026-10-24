# -*- coding: utf-8 -*-
"""聊天情绪总结：按需把最近的聊天记录交给 AI 总结成一条情绪记录，
写入 mood.json（与手动记录同一体系，source="chat" 标记来源）。

设计要点：
- 纯函数模块（构建提示词 / 解析与校验 / 触发判定），便于离线探针覆盖；
- 默认关闭，由设置面板 chat_mood_summary 开启；
- 触发门槛：自上次总结起又有 >= MIN_NEW_MSGS 句用户消息，且距上次
  总结超过 COOLDOWN_SEC；无可用 API / AI 总开关关闭时绝不发起；
- 记录仍然只保存在本地 mood.json；总结请求走 api_client.ask_llm 的
  既有通道（kind="mood"），不引入新的网络代码。
"""
import datetime
import json
import re

from mood import MOOD_KEYS

MIN_NEW_MSGS = 4          # 攒够几句用户消息才值得总结一次
COOLDOWN_SEC = 60 * 60    # 两次总结的最小间隔（1 小时）
DIGEST_USER_MSGS = 30     # 提示词里最多带最近多少句用户消息
DIGEST_MSG_CHARS = 150    # 每句截断长度（省 token，情绪判断足够）

_SUMMARY_SYSTEM = (
    "你是情绪分析助手。根据用户（幸歌）最近说的话，总结他当前的主导情绪。"
    "只输出一个 JSON 对象，不要输出任何其他文字。格式："
    '{"mood":"情绪键","intensity":1,"tags":["标签"],"note":"一句话依据"}。'
    "其中 mood 只能是这些键之一：happy, calm, grateful, excited, neutral, "
    "tired, sad, anxious, angry, lonely；intensity 是 1-5 的整数"
    "（1 很轻微，5 非常强）；tags 最多 3 个简短中文词；note 不超过 20 字。"
    "判断不了就用 {\"mood\":\"neutral\",\"intensity\":2,\"tags\":[],\"note\":\"看不出明显情绪\"}。"
)


def build_prompt(history):
    """从聊天历史构造总结请求。只带用户消息（情绪信号主要来自幸歌自己
    说的话），每句截断，控制 token 开销。返回 messages 列表。"""
    users = [str(m.get("content", "")) for m in (history or [])
             if isinstance(m, dict) and m.get("role") == "user"]
    users = [u.replace("\n", " ").strip() for u in users if u.strip()]
    digest = "\n".join("- " + u[:DIGEST_MSG_CHARS] for u in users[-DIGEST_USER_MSGS:])
    if not digest:
        digest = "- （幸歌最近没说什么话）"
    return [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": "幸歌最近说的话：\n" + digest},
    ]


def parse_mood_json(text):
    """把模型回包解析成 (mood, intensity, tags, note)；解析/校验失败返回
    None（调用方静默放弃这一轮，绝不写入脏数据）。宽容处理：代码围栏、
    JSON 前后混入的说明文字、强度越界、标签过多等。"""
    text = str(text or "").strip()
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    mood = str(obj.get("mood", "")).strip().lower()
    if mood not in MOOD_KEYS:
        return None
    try:
        intensity = int(round(float(obj.get("intensity", 3))))
    except (TypeError, ValueError):
        intensity = 3
    intensity = max(1, min(5, intensity))
    raw_tags = obj.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    if not isinstance(raw_tags, list):
        raw_tags = []
    tags = [str(t).strip()[:12] for t in raw_tags if str(t).strip()][:3]
    note = str(obj.get("note", "")).strip()[:60] or "来自聊天总结"
    return {"mood": mood, "intensity": intensity, "tags": tags, "note": note}


def should_summarize(settings, mood_data, has_api, pending, now=None):
    """触发判定（纯函数，方便探针）。返回 (是否触发, 原因)。"""
    if not (settings or {}).get("chat_mood_summary", False):
        return False, "选项未开启"
    if not has_api:
        return False, "无可用 API"
    try:
        pending = int(pending or 0)
    except (TypeError, ValueError):
        pending = 0
    if pending < MIN_NEW_MSGS:
        return False, "新消息不足"
    last = str((mood_data or {}).get("chat_summary_at") or "").strip()
    if last:
        try:
            t = datetime.datetime.strptime(last, "%Y-%m-%d %H:%M")
            now = now or datetime.datetime.now()
            if (now - t).total_seconds() < COOLDOWN_SEC:
                return False, "冷却中"
        except ValueError:
            pass    # 时间格式不对就当作没有历史记录
    return True, ""
