# -*- coding: utf-8 -*-
"""数据读写工具（原 oc.py 三区）：路径解析、json/日志/API Key 读写、
设置加载、首次运行数据文件初始化。"""
import os
import sys
import json
import time   # append_log 用 time.strftime 写时间戳（缺失会导致聊天时崩溃）
from pathlib import Path

from config import DEFAULT_ITEMS, DEFAULT_SETTINGS, DEFAULT_README, MEMORY_FILE, MEMORY_LOG_DIR

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
def data_dir():
    """数据文件（json/txt/readme）存放目录：源码运行时=当前文件夹，打包后=exe所在文件夹"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return BASE_DIR


def safe_data_path(name):
    """拼接数据文件路径并做防穿越校验：规范化后必须仍落在数据目录内。

    所有调用点的 name 都是程序内常量，这是纵深防御而非修复已知漏洞。
    """
    base = os.path.realpath(data_dir())
    p = os.path.realpath(os.path.join(base, name))
    if p != base and not p.startswith(base + os.sep):
        raise ValueError("数据文件路径越界: %r" % (name,))
    return p


def resource_path(name):
    """图片查找：先找打包资源，再找数据目录（两者都找不到就返回数据目录路径）"""
    for base in (getattr(sys, "_MEIPASS", None), data_dir()):
        if base:
            p = os.path.join(base, name)
            if os.path.exists(p):
                return p
    return os.path.join(data_dir(), name)


def load_json(name, default):
    try:
        return json.loads(Path(safe_data_path(name)).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(name, obj):
    try:
        Path(safe_data_path(name)).write_text(
            json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("[保存失败]", name, e)


def load_memory():
    return load_json(MEMORY_FILE, {"facts": [], "daily": {}, "last_archive_date": "", "pending": []})


def save_memory(mem):
    save_json(MEMORY_FILE, mem)


def load_api_key():
    """从本地文件读取 API Key（打包后放在 exe 旁边）"""
    try:
        return Path(safe_data_path("api_key.txt")).read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def save_api_key(key):
    """把 API Key 存到本地文件"""
    try:
        Path(safe_data_path("api_key.txt")).write_text(key.strip(), encoding="utf-8")
    except Exception as e:
        print("[保存 Key 失败]", e)



def load_settings():
    """读取设置：旧档缺字段自动补默认值（配置兼容升级）"""
    data = load_json("settings.json", {})
    merged = dict(DEFAULT_SETTINGS)
    for k, v in data.items():
        if k in merged:
            merged[k] = v
    return merged

def load_log():
    return load_json("chat_log.json", [])


def save_log(log):
    save_json("chat_log.json", log)


def append_log(role, text):
    log = load_log()
    log.append({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "role": role, "text": text})
    if len(log) > 500:
        log = log[-500:]
    save_log(log)


def ensure_data_files():
    """首次运行时自动生成所有数据文件（不用你手动建）"""
    if not os.path.exists(os.path.join(data_dir(), "items.json")):
        save_json("items.json", DEFAULT_ITEMS)
    if not os.path.exists(os.path.join(data_dir(), "economy.json")):
        save_json("economy.json", {"coins": 100, "last_checkin": "", "outfits": [],
                                   "affinity": 0, "mood": 60, "current_outfit": ""})
    if not os.path.exists(os.path.join(data_dir(), "profile.json")):
        save_json("profile.json", {"nickname": "幸歌", "birthday": "4月4日",
                                   "how_to_call": "幸歌", "relation": "家庭教师兼最好的伙伴"})
    if not os.path.exists(os.path.join(data_dir(), "schedule.json")):
        save_json("schedule.json", [])
    if not os.path.exists(os.path.join(data_dir(), "launcher.json")):
        save_json("launcher.json", {"记事本": "notepad.exe", "计算器": "calc.exe",
                                    "浏览器": "https://www.bing.com"})
    if not os.path.exists(os.path.join(data_dir(), "bookmarks.json")):
        save_json("bookmarks.json", {"B站": "https://www.bilibili.com",
                                     "GitHub": "https://github.com",
                                     "DeepSeek": "https://chat.deepseek.com"})
    if not os.path.exists(os.path.join(data_dir(), "chat_log.json")):
        save_json("chat_log.json", [])
    notes = safe_data_path("notes.txt")
    if not os.path.exists(notes):
        Path(notes).write_text("# 麟的随手记\n", encoding="utf-8")
    if not os.path.exists(safe_data_path("settings.json")):
        save_json("settings.json", DEFAULT_SETTINGS)
    readme = safe_data_path("readme.md")
    if not os.path.exists(readme):
        Path(readme).write_text(DEFAULT_README, encoding="utf-8")
    if not os.path.exists(safe_data_path(MEMORY_FILE)):
        save_memory(load_memory())
    os.makedirs(os.path.join(data_dir(), MEMORY_LOG_DIR), exist_ok=True)
    if not os.path.exists(os.path.join(data_dir(), "mood.json")):
        save_json("mood.json", {"records": [], "custom_tags": [], "streak_date": "",
                                "streak": 0, "last_care_date": ""})
    if not os.path.exists(os.path.join(data_dir(), "assessment.json")):
        save_json("assessment.json", {"records": []})

