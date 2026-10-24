# -*- coding: utf-8 -*-
"""API 仓库（原 oc.py 三、四区相关部分）：多 API 配置读写、
OpenAI 兼容请求（主备自动切换）、连通测试、首次运行向导、信号桥。"""
import ipaddress
import json
import socket
import threading

import requests
from urllib.parse import urlparse
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QInputDialog, QLineEdit

from config import DEFAULT_API_CONFIG
from data import data_dir, load_json, save_json, load_api_key, save_api_key

# 本地接口白名单：readme 明确支持「本地 Ollama」（http://localhost:11434），
# 环回/私网地址只对该形态放行；其余主机名一律不得指向内网/环回/保留段。
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def check_api_url(url):
    """发请求前校验用户配置的接口地址（纵深防御）。

    规则：仅 http/https；解析出的 IP 一律禁止链路本地（含云元数据
    169.254.169.254）、保留段与多播；环回/私网仅当主机名本身是
    localhost 系才放行（同时挡住「公网域名解析到内网 IP」的绕过）。
    返回 None 表示通过，否则返回拒绝原因字符串。
    """
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https"):
            return "接口地址只支持 http/https"
        host = (u.hostname or "").strip().lower().rstrip(".")
        if not host:
            return "接口地址缺少主机名"
        is_local_name = host in _LOCAL_HOSTS or host.endswith(".localhost")
        port = u.port or (443 if u.scheme == "https" else 80)
        for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
                return "接口地址指向保留/链路本地网段（含云元数据），已拒绝"
            if (ip.is_loopback or ip.is_private or ip.is_reserved) and not is_local_name:
                return "非本地主机名不允许指向内网/环回地址"
        return None
    except socket.gaierror:
        return "接口主机名解析失败"
    except Exception as e:
        return "接口地址校验失败: {}".format(e)


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
    bad = check_api_url(url)
    if bad:
        return False, bad
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
    reply_error_kind = pyqtSignal(str, str)  # (错误信息, 请求类型) 让界面能区分是谁失败了
    event_ready = pyqtSignal(str)       # 随机事件 JSON
    food_ready  = pyqtSignal(str)       # 食物评价
    memory_ready = pyqtSignal(str)      # 每日记忆归档 JSON
    summary_ready = pyqtSignal(str)     # 滚动对话摘要（context_budget 上下文压缩）
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
            bad = check_api_url(url)
            if bad:
                last_err = bad
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
                data = resp.json()
                try:
                    msg = data["choices"][0]["message"]
                except (KeyError, IndexError, TypeError):
                    last_err = "返回格式异常：{}".format(str(data)[:120])
                    continue
                content = msg.get("content") or msg.get("reasoning_content") or ""
                if not str(content).strip():
                    last_err = "接口返回了空内容"
                    continue
                content = str(content)
                if kind == "chat":
                    bus.reply_ready.emit(content)
                elif kind == "event":
                    bus.event_ready.emit(content)
                elif kind == "food":
                    bus.food_ready.emit(content)
                elif kind == "memory":
                    bus.memory_ready.emit(content)
                elif kind == "summary":
                    # 滚动摘要：由 PetWindow._on_summary 回填并持久化；失败走 reply_error_kind
                    bus.summary_ready.emit(content)
                return
            except Exception as exc:
                last_err = repr(exc)
        bus.reply_error.emit(last_err)
        bus.reply_error_kind.emit(last_err, kind)
    threading.Thread(target=run, daemon=True).start()


