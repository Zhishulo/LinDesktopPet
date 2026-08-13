# -*- coding: utf-8 -*-
"""记忆系统纯函数：每日日志写入、本地事实提取。
业务编排（归档时机/API 总结/提示词注入）保留在 PetWindow。"""
import os
import re
import time

from config import MEMORY_LOG_DIR, MAX_FACTS, FACT_PATTERNS
from data import data_dir, load_memory, save_memory


def memory_log_dir():
    """记忆日志目录（不存在则创建）"""
    d = os.path.join(data_dir(), MEMORY_LOG_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def write_daily_log(d, summary, highlights, facts):
    """把某天的总结写入 memory_log/YYYY-MM-DD.md"""
    try:
        path = os.path.join(memory_log_dir(), "{}.md".format(d))
        lines = ["# {} · 麟的记忆日志".format(d), "",
                 "## 今日总结", summary, "",
                 "## 值得记住的事"]
        lines += ["- " + h for h in highlights] if highlights else ["- 今天没有特别值得记录的事"]
        if facts:
            lines += ["", "## 新记住的关于幸歌的事"]
            lines += ["- " + f for f in facts]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        print("[记忆日志写入失败]", e)


def extract_facts_local(text):
    """本地规则即时提取用户事实（无需联网），让麟越用越懂使用者。
    返回本次新增的事实列表（已持久化到 memory.json）。"""
    mem = load_memory()
    existing = [f.get("text", "") for f in mem.get("facts", [])]
    added = []
    for pat, tmpl in FACT_PATTERNS:
        m = re.search(pat, text)
        if not m:
            continue
        if "生日" in tmpl:
            f = tmpl.format(m.group(1), m.group(2))
        else:
            f = tmpl.format(m.group(1))
        if f and f not in existing:
            mem.setdefault("facts", []).append({"text": f, "date": time.strftime("%Y-%m-%d")})
            existing.append(f)
            added.append(f)
    if added:
        mem["facts"] = mem["facts"][-MAX_FACTS:]
        save_memory(mem)
    return added
