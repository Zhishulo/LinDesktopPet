# -*- coding: utf-8 -*-
"""API 仓库（原 oc.py 三、四区相关部分）：多 API 配置读写、
OpenAI 兼容请求（主备自动切换）、连通测试、首次运行向导、信号桥。"""
import json
import threading

import requests
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QInputDialog, QLineEdit

from config import DEFAULT_API_CONFIG
from data import data_dir, load_json, save_json, load_api_key, save_api_key
def load_api_config():
    """读取 API 仓库配置；首次运行自动生成，并迁移旧 api_key.txt"""
    cfg = load_json("api_config.json", None)
    if cfg is None:
        cfg = json.loads(json.dumps(DEFAULT_API_CONFIG))
        old = load_api_key()
        if old:
            for a in cfg["apis"]:
                if a["id"] == "deepseek":
                    a["key"] = old
        save_json("api_config.json", cfg)
    return cfg


def save_api_config(cfg):
    save_json("api_config.json", cfg)


def test_api(api):
    """向指定 API 发一条最短请求验证连通性，返回 (ok, 消息)"""
    url = (api.get("base_url") or "").strip()
    model = (api.get("model") or "").strip()
    key = (api.get("key") or "").strip()
    if not url or not model:
        return False, "接口地址或模型为空"
    try:
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        payload = {"model": model, "messages": [{"role": "user", "content": "你好"}],
                   "max_tokens": 10, "stream": False}
        r = requests.post(url, json=payload, headers=headers, timeout=20)
        if r.status_code == 200:
            return True, "连通正常"
        return False, "HTTP {}: {}".format(r.status_code, r.text[:100])
    except Exception as e:
        return False, repr(e)[:100]

def ask_api_key_once():
    """首次运行配置向导：把用户自己的 DeepSeek Key 存入 API 仓库"""
    while True:
        key, ok = QInputDialog.getText(
            None, "初次见面，麟在这里！",
            "欢迎使用《丰裕之角》同人桌宠·麟～\n\n"
            "本程序不内置任何 API，请接入【你自己的】API Key（推荐 DeepSeek）：\n"
            "1. 打开 https://platform.deepseek.com 注册并创建 API Key\n"
            "2. 复制以 sk- 开头的那串密钥，粘贴到下面：\n\n"
            "没有 Key 也可以直接点取消，麟会用离线保底模式陪你～\n"
            "之后右键 → 🔌 API 管理 随时配置/切换多家 API。\n",
            QLineEdit.Normal, "")
        if not ok:
            return ""
        key = key.strip()
        if key.startswith("sk-") and len(key) > 10:
            save_api_key(key)   # 兼容旧档
            cfg = load_api_config()
            for a in cfg["apis"]:
                if a["id"] == "deepseek":
                    a["key"] = key
                    a["enabled"] = True
            cfg["current"] = "deepseek"
            save_api_config(cfg)
            return key
        # 输错了就循环再问

class SignalBus(QObject):
    """跨线程信号桥：不同请求类型走不同信号，避免混乱"""
    reply_ready = pyqtSignal(str)       # 普通聊天
    reply_error = pyqtSignal(str)
    event_ready = pyqtSignal(str)       # 随机事件 JSON
    food_ready  = pyqtSignal(str)       # 食物评价
    memory_ready = pyqtSignal(str)      # 每日记忆归档 JSON
    weather_ready = pyqtSignal(str)     # 天气 JSON
    ping_ready  = pyqtSignal(bool, int) # (是否在线, 延迟ms)


def ask_llm(messages, bus, kind="chat", temperature=1.2, max_tokens=400):
    """多 API 后台调用：从 api_config.json 的当前 API 开始请求，
    失败自动切换下一个启用的 API；全部失败时发回错误信号。"""
    def run():
        cfg = load_api_config()
        cur = cfg.get("current", "")
        apis = [a for a in cfg.get("apis", []) if a.get("enabled")]
        order = [a for a in apis if a.get("id") == cur] + [a for a in apis if a.get("id") != cur]
        last_err = "NO_KEY"
        for api in order:
            url = (api.get("base_url") or "").strip()
            model = (api.get("model") or "").strip()
            key = (api.get("key") or "").strip()
            if not url or not model:
                continue
            local = "localhost" in url.lower() or "127.0.0.1" in url.lower()
            if not key and not local:
                continue
            try:
                payload = {"model": model, "messages": messages,
                           "temperature": temperature, "top_p": 0.9,
                           "max_tokens": max_tokens, "stream": False}
                headers = {"Content-Type": "application/json"}
                if key:
                    headers["Authorization"] = "Bearer " + key
                resp = requests.post(url, json=payload, headers=headers, timeout=60)
                if resp.status_code != 200:
                    last_err = "HTTP {}: {}".format(resp.status_code, resp.text[:120])
                    continue
                content = resp.json()["choices"][0]["message"]["content"]
                if kind == "chat":
                    bus.reply_ready.emit(content)
                elif kind == "event":
                    bus.event_ready.emit(content)
                elif kind == "food":
                    bus.food_ready.emit(content)
                elif kind == "memory":
                    bus.memory_ready.emit(content)
                return
            except Exception as exc:
                last_err = repr(exc)
        bus.reply_error.emit(last_err)
    threading.Thread(target=run, daemon=True).start()


