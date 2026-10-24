# -*- coding: utf-8 -*-
"""API 仓库（原 oc.py 三、四区相关部分）：多 API 配置读写、
OpenAI 兼容请求（主备自动切换）、连通测试、首次运行向导、信号桥。"""
import base64
import ipaddress
import json
import os
import socket
import sys
import threading

import requests
from pathlib import Path
from urllib.parse import urlparse
from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QInputDialog, QLineEdit

from config import DEFAULT_API_CONFIG
from data import data_dir, load_json, save_json, load_api_key, save_api_key

# 本地接口白名单：readme 明确支持「本地 Ollama」（http://localhost:11434），
# 环回/私网地址只对该形态放行；其余主机名一律不得指向内网/环回/保留段。
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")

# ---- 冻结环境 ssl 自愈 ----
# 本项目构建用的 Python 是带安全补丁的定制版：ssl.py 创建 SSLContext 时
# 会强制加载自身同目录的 codex-rejected-cas.pem（吊销 emSign ECC Root CA
# - G3 用）。源码运行时该文件就在 Python 目录里；PyInstaller 打包后
# ssl.__file__ 指向解包临时目录，pem 没跟进去 → 任何 https 请求在创建
# SSLContext 时直接抛 FileNotFoundError(2)，表现为「Connection aborted」。
# 导入本模块时自检一次：缺文件就把内置的原始 pem（与基础 Python 附带的
# 逐字节一致，906 字节）补写过去。只允许写进 realpath(sys._MEIPASS)
# 本目录、且路径不含 ..，其余情况一律放弃并保持原行为。
_REJECTED_CAS_PEM_B64 = "LS0tLS1CRUdJTiBUUlVTVEVEIENFUlRJRklDQVRFLS0tLS0NCk1JSUNUakNDQWRPZ0F3SUJBZ0lLUFBZSHFXaHdEdHFMaERBS0JnZ3Foa2pPUFFRREF6QnJNUXN3Q1FZRFZRUUcNCkV3SkpUakVUTUJFR0ExVUVDeE1LWlcxVGFXZHVJRkJMU1RFbE1DTUdBMVVFQ2hNY1pVMTFaR2h5WVNCVVpXTm8NCmJtOXNiMmRwWlhNZ1RHbHRhWFJsWkRFZ01CNEdBMVVFQXhNWFpXMVRhV2R1SUVWRFF5QlNiMjkwSUVOQklDMGcNClJ6TXdIaGNOTVRnd01qRTRNVGd6TURBd1doY05ORE13TWpFNE1UZ3pNREF3V2pCck1Rc3dDUVlEVlFRR0V3SkoNClRqRVRNQkVHQTFVRUN4TUtaVzFUYVdkdUlGQkxTVEVsTUNNR0ExVUVDaE1jWlUxMVpHaHlZU0JVWldOb2JtOXMNCmIyZHBaWE1nVEdsdGFYUmxaREVnTUI0R0ExVUVBeE1YWlcxVGFXZHVJRVZEUXlCU2IyOTBJRU5CSUMwZ1J6TXcNCmRqQVFCZ2NxaGtqT1BRSUJCZ1VyZ1FRQUlnTmlBQVFqcFF5NExSTDFLUE94c3QzaUFoS0FuamxmU1UyZnlTVTANCldYVHN1d1ljNThCeXIraXVMK0ZCVkljVXFFcXk2SHlDNWx0cXRkeXpkYzZMQnRDR0k3OUcxWTRQUHdUMDF4eVMNCmZ2YWxZOEwxWDQ0dVQ2RVlHUUlyTWdxQ1pIMFdrOUdqUWpCQU1CMEdBMVVkRGdRV0JCUjhYUUtFRTlUTWlwdUINCnpoY2NMaWtlbkVoalFqQU9CZ05WSFE4QkFmOEVCQU1DQVFZd0R3WURWUjBUQVFIL0JBVXdBd0VCL3pBS0JnZ3ENCmhrak9QUVFEQXdOcEFEQm1BakVBdnZOaHp3SVFIV1NWQjdnWWJvaUZCUytEQ0JlUXloK0tUT2dORzNxeHJkV0INCkNVZnZPNndJQkh4Y21iSHRSd2ZTQWpFQW5icFYvS2xLNk8zdDVuWUJRbnZJK0dEWmpWR0xWVHY3akh2clpRbkQNCitKYk5SNmlDOGhaVmR5UitFaENWQkN5ak1BaWdCZ1lFVlIwbEFBPT0NCi0tLS0tRU5EIFRSVVNURUQgQ0VSVElGSUNBVEUtLS0tLQ0K"


def _heal_frozen_ssl_pem():
    """打包运行时补齐 ssl 补丁需要的 pem（详见上方注释）；任何失败都静默，
    不影响正常流程——构建参数通常已把 pem 带进包，这里只是兜底。"""
    try:
        if not getattr(sys, "frozen", False):
            return
        import ssl
        pem = Path(os.path.dirname(ssl.__file__ or ".")).resolve() \
            / "codex-rejected-cas.pem"
        if pem.exists():
            return
        base_dir = Path(getattr(sys, "_MEIPASS", "") or ".").resolve()
        if pem.parent != base_dir or ".." in str(pem):
            return
        pem.write_bytes(base64.b64decode(_REJECTED_CAS_PEM_B64))
    except Exception:
        pass


_heal_frozen_ssl_pem()


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


def post_with_fallback(url, payload, headers, timeout):
    """带「代理失败自动直连重试」的 POST。

    现象：系统代理（如 Clash 127.0.0.1:xxxx）进程挂掉或抖动时，requests 默认
    走系统代理，所有请求全部失败——但 DeepSeek/Kimi/通义/智谱这些国内接口
    直连本来就能通。所以连接层失败（连接拒绝/超时/SSL 断流）时，用
    trust_env=False 的会话绕开系统代理再试一次；仍失败才报错。
    返回 (resp, None) 或 (None, err)——err 是说人话的短句。
    """
    try:
        return requests.post(url, json=payload, headers=headers, timeout=timeout), None
    except Exception as first:
        direct = requests.Session()
        direct.trust_env = False          # 忽略系统/环境代理，强制直连
        try:
            return direct.post(url, json=payload, headers=headers,
                               timeout=timeout), None
        except Exception as second:
            return None, _friendly_net_error(first, second)


def _friendly_net_error(first, second):
    """把两层网络异常压成一句能看懂的话。"""
    txt = (repr(first) + repr(second)).lower()
    if "proxy" in txt or "127.0.0.1" in txt or "proxyerror" in txt:
        return ("网络不通：系统代理疑似挂了（已自动尝试直连也失败），"
                "检查代理软件或关掉系统代理再试")
    if "timed out" in txt or "timeout" in txt:
        return "连接超时：网络到接口不通（已自动尝试直连）"
    if "ssl" in txt:
        return "SSL 连接被中断（已自动尝试直连），多为网络/代理不稳"
    return "网络错误（已自动尝试直连）：{}".format(repr(first)[:120])


def friendly_http_error(status, body):
    """HTTP 非 200 时的人话摘要；模型名错误时直接带上官方支持名单。"""
    body = (body or "")[:300]
    low = body.lower()
    if status in (400, 404) and "model" in low:
        import re as _re
        m = _re.search(r"supported API model names\s*(?:are\s*)?(.+?)"
                       r"(?:\s*,?\s*but\b|\.|$)", body)
        if m:
            return "模型名不对！官方支持：{}".format(m.group(1).strip())
    if status == 401:
        return "API Key 无效或已过期（HTTP 401）"
    if status == 402:
        return "账户余额不足（HTTP 402）"
    if status == 429:
        return "请求太频繁被限流（HTTP 429），稍等再试"
    return "HTTP {}：{}".format(status, body[:150])


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
    """向指定 API 发一条最短请求验证连通性，返回 (ok, 消息)。

    网络层失败会自动直连重试（绕开可能挂掉的系统代理）；
    模型名错误时把官方支持名单直接带回给用户。
    """
    url = (api.get("base_url") or "").strip()
    model = (api.get("model") or "").strip()
    key = (api.get("key") or "").strip()
    if not url or not model:
        return False, "接口地址或模型为空"
    bad = check_api_url(url)
    if bad:
        return False, bad
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    payload = {"model": model, "messages": [{"role": "user", "content": "你好"}],
               "max_tokens": 10, "stream": False}
    r, err = post_with_fallback(url, payload, headers, timeout=20)
    if err:
        return False, err
    if r.status_code == 200:
        return True, "连通正常（模型 {}）".format(model)
    return False, friendly_http_error(r.status_code, r.text)

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
    mood_ready = pyqtSignal(str)        # 聊天情绪总结 JSON（mood_summary → mood.json）
    weather_ready = pyqtSignal(str)     # 天气 JSON
    ping_ready  = pyqtSignal(bool, int) # (是否在线, 延迟ms)


def ask_llm(messages, bus, kind="chat", temperature=1.2, max_tokens=400):
    """多 API 后台调用：从 api_config.json 的当前 API 开始请求，
    失败自动切换下一个启用的 API；全部失败时发回错误信号。"""
    def run():
        cfg = load_api_config()
        # AI 总开关：关掉时不发任何请求，直接走本地保底（配置里的 Key 原样保留）
        if not cfg.get("ai_enabled", True):
            bus.reply_error.emit("AI_DISABLED")
            bus.reply_error_kind.emit("AI_DISABLED", kind)
            return
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
                resp, net_err = post_with_fallback(url, payload, headers, timeout=60)
                if net_err:
                    last_err = net_err
                    continue
                if resp.status_code != 200:
                    last_err = friendly_http_error(resp.status_code, resp.text)
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
                elif kind == "mood":
                    # 聊天情绪总结：由 PetWindow._on_mood_summary 解析并写入 mood.json
                    bus.mood_ready.emit(content)
                return
            except Exception as exc:
                last_err = _friendly_net_error(exc, exc)
        bus.reply_error.emit(last_err)
        bus.reply_error_kind.emit(last_err, kind)
    threading.Thread(target=run, daemon=True).start()


