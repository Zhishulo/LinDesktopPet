# -*- coding: utf-8 -*-
"""上下文预算与滚动摘要（移植 billion-context 的「分层摘要 + 按预算保留」思想）。

背景
----
billion-context 是 agent harness 用的 TypeScript MCP 上下文压缩服务，本桌宠是
PyQt5 直接 requests.post 调 DeepSeek，无法原样接入；所以这里只移植它的核心思想：

1. 分层：把对话分成「最近若干轮（原样保留）」和「更早的对话（压成一段摘要）」；
2. 按预算保留：每次请求按估算 token 预算从新到旧装填，装不下的旧消息不再发送；
3. 滚动摘要：被挤出去的旧消息合并进一段累计摘要，下次继续和新增旧消息一起压缩。

与 memory.json 的关系
--------------------
memory.json 是「按天」的人类可读记忆（每日总结 + 长期事实）；本模块的摘要
是「按对话进度」的滚动上下文，存在独立的 chat_context.json 里，两者互不覆盖。

依赖约束
--------
只依赖标准库 + data.load_json/save_json + config 常量，**不依赖 Qt**，
方便单测与 offscreen 环境复用；任何读写失败都静默降级，绝不打断聊天主流程。

token 估算说明（重要）
----------------------
下面是「估算」，不是真实分词：DeepSeek 分词器上大致 1 token ≈ 1.6 个汉字
≈ 3.3 个 ASCII 字符。任务书里写的「ASCII 约 0.3 /词」按字符计更接近实测
（英文平均一个词约 1.3~1.5 token），因此本实现把 ASCII 系数按「字符」计。
估算只用于预算裁剪，误差 ±20% 不影响正确性；如需更保守可把系数调大。
"""
import time

try:
    from data import load_json, save_json
except Exception:      # pragma: no cover - 单测兜底（data 不可用时用内存假实现）
    _FAKE = {}

    def load_json(name, default):
        return _FAKE.get(name, default)

    def save_json(name, obj):
        _FAKE[name] = obj

try:
    import config as _cfg
except Exception:      # pragma: no cover
    _cfg = None


def _cfg_get(name, default):
    """从 config 取常量；缺失时用默认值（兼容旧档 config.py）"""
    try:
        if _cfg is not None and hasattr(_cfg, name):
            return getattr(_cfg, name)
    except Exception:
        pass
    return default


# ==================== 常量 ====================
CTX_VERSION = 1                      # chat_context.json 结构版本，不符则丢弃重来
CONTEXT_FILE = "chat_context.json"   # 独立持久化文件（不影响 memory.json）
DEFAULT_BUDGET_TOKENS = 2600         # 一次聊天请求的估算 token 预算
KEEP_RECENT_TURNS = 6                # 始终原样保留最近 N 轮（1 轮 = 2 条消息）
MAX_MSG_CHARS = 500                  # 单条历史消息最多保留字符数（超出截断）
SUMMARY_MAX_CHARS = 700              # 滚动摘要最大字符数
SUMMARY_EVERY = 8                    # 最近窗口之外每多 N 条消息触发一次摘要
SUMMARY_INPUT_MAX_MSGS = 40          # 一次摘要请求最多喂多少条旧消息
MSG_OVERHEAD_TOKENS = 4              # 每条消息的角色/分隔符开销（估算）

# token 估算系数（可按实际分词器微调）
CJK_TOKEN_PER_CHAR = 0.6             # 汉字 / 假名：每字约 0.6 token
ASCII_TOKEN_PER_CHAR = 0.3           # ASCII：每字符约 0.3 token（≈1 token / 3.3 字符）
OTHER_TOKEN_PER_CHAR = 0.75          # 中文标点 / emoji / 其它宽字符

# 摘要消息的注入方式：True = 作为独立的第二条 system 消息（推荐：system 本身逐字不变，
# DeepSeek 自动前缀缓存更容易命中）；False = 拼到首条 system 末尾（个别网关只允许一条 system）。
SUMMARY_AS_SEPARATE_SYSTEM = True
SUMMARY_PREFIX = "【之前的对话概要】"


# ==================== token 估算 ====================
def est_tokens(text):
    """估算一段文本的 token 数（CJK 0.6/字、ASCII 0.3/字符、其它 0.75/字符）。

    纯估算，用于预算裁剪，不做任何分词。空/None 返回 0。
    """
    if not text:
        return 0
    s = str(text)
    cjk = ascii_chars = other = 0
    for ch in s:
        o = ord(ch)
        if o <= 0x7F:
            ascii_chars += 1
        elif (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF
              or 0xF900 <= o <= 0xFAFF or 0x3040 <= o <= 0x30FF
              or 0xAC00 <= o <= 0xD7AF):
            cjk += 1
        else:
            other += 1
    est = (cjk * CJK_TOKEN_PER_CHAR + ascii_chars * ASCII_TOKEN_PER_CHAR
           + other * OTHER_TOKEN_PER_CHAR)
    return int(round(est))


def est_messages_tokens(msgs):
    """估算一整组 messages 的 token 数：每条 + 角色开销，逐条累加估算。"""
    total = 0
    for m in (msgs or []):
        try:
            total += MSG_OVERHEAD_TOKENS + est_tokens(m.get("content", ""))
        except Exception:
            continue
    return total


# ==================== 内部工具 ====================
def _clean_summary(text):
    """规整摘要文本：去空白/代码块/前缀、合并成一段、限长。异常时返回空串。"""
    try:
        s = str(text or "").replace("\r", "").strip()
    except Exception:
        return ""
    if not s:
        return ""
    s = s.replace("```", "")          # 去掉模型偶尔加的 Markdown 代码围栏
    lines = []
    for ln in s.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        for head in (SUMMARY_PREFIX, "【摘要】", "摘要：", "摘要:"):
            if ln.startswith(head):
                ln = ln[len(head):].strip()
                break
        if ln:
            lines.append(ln)
    s = "；".join(lines) if len(lines) > 1 else "".join(lines)
    s = " ".join(s.split())
    s = s.strip("；;，, ")
    if len(s) > SUMMARY_MAX_CHARS:
        s = s[:SUMMARY_MAX_CHARS].rstrip() + "…"
    return s


def _norm_msg(m):
    """校验并规整一条历史消息；非法返回 None。只接受 user/assistant。"""
    try:
        if not isinstance(m, dict):
            return None
        role = str(m.get("role") or "").strip()
        if role not in ("user", "assistant"):
            return None
        content = m.get("content")
        content = "" if content is None else str(content)
        content = content.strip()
        if not content:
            return None
        return {"role": role, "content": content}
    except Exception:
        return None


def _truncate(text, limit=MAX_MSG_CHARS):
    """超长内容截断（保留开头，末尾加省略号）"""
    s = str(text or "")
    if limit <= 0 or len(s) <= limit:
        return s
    return s[:limit].rstrip() + "…"


def _truncate_msg(m, limit=MAX_MSG_CHARS):
    return {"role": m["role"], "content": _truncate(m["content"], limit)}


# ==================== 预算器 ====================
class ContextBudget(object):
    """聊天上下文的预算器 + 滚动摘要状态。

    典型用法（由 PetWindow 调用，本模块不主动请求 API）：

        ctx = ContextBudget().load()
        msgs, dropped = ctx.build_messages(self.build_system_prompt(), self.history)
        ask_llm(msgs, self.bus)                     # 正常聊天
        if ctx.needs_summary(self.history):         # 攒够旧消息再异步摘要
            old = self.history[:len(self.history) - KEEP_RECENT_TURNS * 2]
            ask_llm(ctx.make_summary_prompt(old), self.bus, kind="summary")
        # 收到 summary_ready 后：
        ctx.apply_summary(content, covered=len(old))
    """

    def __init__(self, file_name=CONTEXT_FILE, budget=None,
                 keep_recent_turns=None, summary_every=None):
        self.file_name = file_name
        self.budget = int(budget if budget is not None else
                          _cfg_get("CHAT_CONTEXT_BUDGET", DEFAULT_BUDGET_TOKENS))
        self.keep_recent_turns = int(
            keep_recent_turns if keep_recent_turns is not None else
            _cfg_get("KEEP_RECENT_TURNS", KEEP_RECENT_TURNS))
        self.summary_every = int(
            summary_every if summary_every is not None else
            _cfg_get("CONTEXT_SUMMARY_EVERY", SUMMARY_EVERY))
        if self.keep_recent_turns < 0:
            self.keep_recent_turns = 0
        if self.summary_every < 1:
            self.summary_every = 1
        self.version = CTX_VERSION
        self.summary = ""
        self.covered = 0
        self.updated = ""

    # ---------- 持久化 ----------
    def load(self):
        """读取 chat_context.json；文件缺失/损坏/版本不符时保持空状态（不抛异常）"""
        try:
            raw = load_json(self.file_name, None)
        except Exception:
            raw = None
        if not isinstance(raw, dict):
            return self
        try:
            if int(raw.get("version", CTX_VERSION)) != CTX_VERSION:
                return self
            self.summary = _clean_summary(raw.get("summary", ""))
            self.covered = max(0, int(raw.get("covered", 0) or 0))
            self.updated = str(raw.get("updated", "") or "")
        except (TypeError, ValueError):
            self.summary, self.covered, self.updated = "", 0, ""
        return self

    def save(self):
        """写回 chat_context.json；失败静默（save_json 内部已兜底）"""
        try:
            save_json(self.file_name, {
                "version": CTX_VERSION,
                "summary": self.summary,
                "covered": int(self.covered),
                "updated": self.updated,
            })
            return True
        except Exception:
            return False

    def reset(self):
        """清空摘要（清历史/改人设时调用），并立即落盘"""
        self.summary = ""
        self.covered = 0
        self.updated = ""
        self.save()
        return self

    # ---------- 预算裁剪 ----------
    def build_messages(self, system_prompt, history, summary=None):
        """构建一次聊天请求。

        返回 (msgs, dropped_count)：
        - msgs = [system] + [可选摘要 system] + 预算内的历史消息；
        - dropped_count = 因为超出保留窗口/预算而没有发送的历史条数。
        永远保留最后一条历史消息（即本轮用户提问）；system 本身不裁剪。
        """
        sys_prompt = "" if system_prompt is None else str(system_prompt)
        hist = []
        for m in (history or []):
            nm = _norm_msg(m)
            if nm:
                hist.append(_truncate_msg(nm))
        sm = _clean_summary(self.summary if summary is None else summary)

        used = est_tokens(sys_prompt) + MSG_OVERHEAD_TOKENS
        summary_msg = None
        if sm:
            summary_msg = {"role": "system",
                           "content": SUMMARY_PREFIX + "\n" + sm}
            used += est_tokens(summary_msg["content"]) + MSG_OVERHEAD_TOKENS

        keep = self.keep_recent_turns * 2
        if keep > 0 and len(hist) > keep:
            tail, head = hist[-keep:], hist[:-keep]
        else:
            tail, head = hist, []

        kept = list(tail)
        avail = self.budget - used - est_messages_tokens(tail)
        if avail > 0:
            for m in reversed(head):
                t = est_tokens(m["content"]) + MSG_OVERHEAD_TOKENS
                if t > avail:
                    break
                kept.insert(0, m)
                avail -= t
        else:
            # 最近窗口本身也超预算：从最旧的丢，但永远至少保留最后一条
            while len(kept) > 1 and used + est_messages_tokens(kept) > self.budget:
                kept.pop(0)

        dropped = len(hist) - len(kept)
        msgs = [{"role": "system", "content": sys_prompt}]
        if summary_msg is not None:
            if SUMMARY_AS_SEPARATE_SYSTEM:
                msgs.append(summary_msg)
            else:
                msgs[0] = {"role": "system",
                           "content": sys_prompt + "\n\n" + summary_msg["content"]}
        msgs.extend(kept)
        return msgs, dropped

    # ---------- 摘要触发 ----------
    def needs_summary(self, history):
        """最近窗口之外是否已攒够 SUMMARY_EVERY 条「还没被摘要覆盖」的旧消息"""
        n = 0
        for m in (history or []):
            if _norm_msg(m):
                n += 1
        foldable = n - self.keep_recent_turns * 2
        return (foldable - int(self.covered or 0)) >= self.summary_every

    def covered_target(self, history):
        """当前应该被摘要覆盖到的条数（= 最近窗口之前的全部消息数）"""
        n = 0
        for m in (history or []):
            if _norm_msg(m):
                n += 1
        return max(0, n - self.keep_recent_turns * 2)

    def should_summarize(self, history, system_prompt=""):
        """是否值得「现在」花一次摘要请求。省钱优先：只在预算真被撑爆时才做。

        两个条件同时满足才返回 True：
        1) 最近窗口之外还有 >= summary_every 条「还没进摘要」的旧消息；
        2) 全部历史一起发会超预算（dropped > 0）——否则直接发更省，
           没必要为没被挤掉的内容花钱做摘要。
        """
        n = 0
        for m in (history or []):
            if _norm_msg(m):
                n += 1
        foldable = n - self.keep_recent_turns * 2
        if (foldable - int(self.covered or 0)) < self.summary_every:
            return False
        _msgs, dropped = self.build_messages(system_prompt, history)
        return dropped > 0

    def summary_window(self, history):
        """本次应送去摘要的增量区间 [start, end)（下标按「规范化后」的消息序列）。

        只取「最近窗口之外、还没被 covered 覆盖」的部分，
        避免每次都把全部旧消息重新喂给模型（那是摘要请求变贵的主因）。
        """
        n = 0
        for m in (history or []):
            if _norm_msg(m):
                n += 1
        fold_to = max(0, n - self.keep_recent_turns * 2)
        start = min(max(0, int(self.covered or 0)), fold_to)
        return start, fold_to

    def summary_slice(self, history):
        """summary_window 的便捷版：直接返回要摘要的消息列表（调用方无需算下标）"""
        norm = []
        for m in (history or []):
            nm = _norm_msg(m)
            if nm:
                norm.append(nm)
        start, end = self.summary_window(norm)
        return norm[start:end]

    def make_summary_prompt(self, old_msgs, prev_summary=None):
        """生成「把旧摘要 + 新增旧对话合并成新摘要」的 messages（纯函数，不调 API）。

        调用方应使用 kind="summary"、temperature 低、max_tokens 适中（如 600）。
        """
        prev = _clean_summary(self.summary if prev_summary is None else prev_summary)
        lines = []
        olds = [m for m in (old_msgs or []) if _norm_msg(m)]
        for m in olds[-SUMMARY_INPUT_MAX_MSGS:]:
            nm = _norm_msg(m)
            role = "幸歌" if nm["role"] == "user" else "麟"
            lines.append(role + "：" + _truncate(nm["content"]))
        convo = "\n".join(lines)
        sys_prompt = (
            "你是对话摘要器，为虚构角色扮演桌宠「麟」压缩聊天记忆。"
            "请把【旧摘要】和【新增对话】合并成一段新的中文摘要，"
            "只输出摘要正文，不要任何解释、标题、编号或代码块。\n"
            "要求：1.不超过{max_chars}字；2.用第三人称陈述，"
            "保留幸歌的称呼/喜好/生日/工作等长期事实、双方约定与未完成的事、"
            "幸歌近期情绪与烦恼、聊过的话题；3.删掉寒暄、重复和语气词；"
            "4.不要编造没出现过的信息；5.不要写成对话形式，"
            "不要出现“以下是摘要”。"
        ).format(max_chars=SUMMARY_MAX_CHARS)
        user = "【旧摘要】\n{prev}\n\n【新增对话】\n{convo}".format(
            prev=prev or "（无）", convo=convo or "（无）")
        return [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": user}]

    def apply_summary(self, text, covered=None):
        """把模型返回的摘要写回并持久化。返回是否接受（空摘要返回 False）。"""
        s = _clean_summary(text)
        if not s:
            return False
        self.summary = s
        if covered is not None:
            try:
                self.covered = max(int(self.covered or 0), int(covered))
            except (TypeError, ValueError):
                pass
        self.updated = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save()
        return True

    # ---------- 自检 / 调试 ----------
    def report(self, system_prompt, history):
        """打包一次构建结果，方便测量与日志（不改变状态）"""
        msgs, dropped = self.build_messages(system_prompt, history)
        total = est_messages_tokens(msgs)
        return {
            "messages": len(msgs),
            "tokens": total,
            "budget": self.budget,
            "over_budget": total > self.budget,
            "system_tokens": est_tokens(system_prompt),
            "summary_chars": len(self.summary or ""),
            "summary_tokens": est_tokens(self.summary or ""),
            "history_chars": sum(len(str(m.get("content", ""))) for m in (history or [])),
            "dropped": dropped,
        }
