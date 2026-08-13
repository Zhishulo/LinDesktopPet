# -*- coding: utf-8 -*-
"""数据读写工具（原 oc.py 三区）：路径解析、json/日志/API Key 读写、
设置加载、首次运行数据文件初始化。"""
import os
import sys
import json

from config import DEFAULT_ITEMS, DEFAULT_SETTINGS, DEFAULT_README, MEMORY_FILE, MEMORY_LOG_DIR

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
def data_dir():
    """数据文件（json/txt/readme）存放目录：源码运行时=当前文件夹，打包后=exe所在文件夹"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return BASE_DIR


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
        with open(os.path.join(data_dir(), name), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(name, obj):
    try:
        with open(os.path.join(data_dir(), name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("[保存失败]", name, e)


def load_memory():
    return load_json(MEMORY_FILE, {"facts": [], "daily": {}, "last_archive_date": "", "pending": []})


def save_memory(mem):
    save_json(MEMORY_FILE, mem)


def load_api_key():
    """从本地文件读取 API Key（打包后放在 exe 旁边）"""
    try:
        with open(os.path.join(data_dir(), "api_key.txt"), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def save_api_key(key):
    """把 API Key 存到本地文件"""
    try:
        with open(os.path.join(data_dir(), "api_key.txt"), "w", encoding="utf-8") as f:
            f.write(key.strip())
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
    notes = os.path.join(data_dir(), "notes.txt")
    if not os.path.exists(notes):
        with open(notes, "w", encoding="utf-8") as f:
            f.write("# 麟的随手记\n")
    if not os.path.exists(os.path.join(data_dir(), "settings.json")):
        save_json("settings.json", DEFAULT_SETTINGS)
    readme = os.path.join(data_dir(), "readme.md")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write(DEFAULT_README)
    if not os.path.exists(os.path.join(data_dir(), MEMORY_FILE)):
        save_memory(load_memory())
    os.makedirs(os.path.join(data_dir(), MEMORY_LOG_DIR), exist_ok=True)
    if not os.path.exists(os.path.join(data_dir(), "mood.json")):
        save_json("mood.json", {"records": [], "custom_tags": [], "streak_date": "",
                                "streak": 0, "last_care_date": ""})
    if not os.path.exists(os.path.join(data_dir(), "assessment.json")):
        save_json("assessment.json", {"records": []})

