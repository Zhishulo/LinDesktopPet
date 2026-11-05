# -*- coding: utf-8 -*-
"""全部对话框（原 oc.py 四、五区）：历史/帮助/记忆/人设/档案/专注/背包/
日程/事件/猜拳/骰子/整理/便签/商城/API 管理/API 编辑/设置。"""
import os
import sys
import time
import random
import shutil

from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QItemSelectionModel
from PyQt5.QtGui import QPixmap, QPainter, QLinearGradient, QColor, QFont, QCursor
from PyQt5.QtWidgets import (
    QApplication, QDialog, QLabel, QLineEdit, QPushButton, QHBoxLayout,
    QVBoxLayout, QFrame, QPlainTextEdit, QSpinBox, QSlider, QFormLayout,
    QListWidget, QListWidgetItem, QGridLayout, QFileDialog, QMessageBox,
    QTextBrowser, QTimeEdit, QDialogButtonBox, QCheckBox, QWidget,
    QRadioButton, QStackedWidget, QScrollArea, QProgressBar, QComboBox,
    QAbstractItemView, QMenu, QAction, QInputDialog)

from config import SHOP_ITEMS, MEMORY_LOG_DIR
from data import data_dir, resource_path, load_json, save_json, load_log, save_log
from api_client import ask_llm, load_api_config, save_api_config, test_api
from mood import (MOOD_TYPES, MOOD_TAGS, INTENSITY_NAMES, CRISIS_LINES,
                  load_moods, add_record, delete_record, analyze,
                  mood_name, mood_emoji)
from assessment import (SCALES, SCALES_BY_KEY, load_assessments,
                        delete_assessment, add_assessment, scales_by_category)
from music_folder import fmt_ms, external_dir, AUDIO_EXTS, ZIP_EXTS, MODES
from playlists import (PlaylistStore, SYSTEM_SCOPES, SYS_ALL, SYS_BUILTIN,
                       SYS_EXTERNAL, NAME_MAX, FAV_ID, FAV_NAME, track_key, split_key)
from trash import TRASH_SCOPE, TRASH_NAME, get_store


class HistoryDialog(QDialog):
    """1. 历史记录面板：带时间戳 + 每行删除按钮（删除=物理失忆）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("历史记录（删除后角色会物理失忆）")
        self.setMinimumSize(560, 420)
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setStyleSheet("QListWidget{background:#fdf6ec;color:#3d2b1f;font-size:13px;}")
        lay.addWidget(self.list)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)
        self.refresh()

    def refresh(self):
        self.list.clear()
        log = load_log()
        for i, item in enumerate(reversed(log)):
            idx = len(log) - 1 - i
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(8, 2, 8, 2)
            lab = QLabel("{} [{}] {}".format(item.get("t", ""), item.get("role", ""), item.get("text", "")))
            lab.setWordWrap(True)
            lab.setMinimumWidth(400)
            btn = QPushButton("删除")
            btn.clicked.connect(lambda _, i0=idx: self.delete_row(i0))
            h.addWidget(lab, 1)
            h.addWidget(btn)
            w.adjustSize()
            it = QListWidgetItem()
            it.setSizeHint(w.sizeHint())
            self.list.addItem(it)
            self.list.setItemWidget(it, w)

    def delete_row(self, idx):
        log = load_log()
        if 0 <= idx < len(log):
            removed = log.pop(idx)
            save_log(log)
            win = self.parent()
            if hasattr(win, "history"):
                win.history = [m for m in win.history
                               if not (m.get("role") == removed.get("role")
                                       and m.get("content") == removed.get("text"))]
        self.refresh()


class ChatWindow(QDialog):
    """聊天窗口模式：气泡式的完整对话界面（用户右 / 麟左），与桌宠气泡是
    同一场对话。开着时聊天回复显示在这里（支持流式逐字输出），关掉自动
    回到气泡模式——「自由切换」就是开/关这个窗口。非模态 + 原生标题栏三键。

    显示要点：消息区用 QScrollArea+QVBoxLayout（不用 QListWidget——
    itemWidget 的 sizeHint 对自动换行的 QLabel 不可靠，长文本会被裁边）；
    气泡内边距用 QLabel.setMargin（会计入 sizeHint），样式表只负责
    底色/圆角/描边，从根上避免「字被气泡边框吃掉」。

    流式节奏：网络增量到达后先进缓冲队列，由 QTimer 按用户设置的速度
    （settings.stream_speed：instant/fast/medium/slow）逐批上屏——网络再快
    也不糊屏，打字节奏自己定。
    """
    STREAM_STEPS = {"instant": (999, 0), "fast": (4, 18),
                    "medium": (2, 34), "slow": (1, 55)}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_win = parent
        self.setWindowTitle("💬 和麟聊天（窗口模式）")
        self.setWindowFlags(Qt.Window
                            | Qt.WindowMinimizeButtonHint
                            | Qt.WindowMaximizeButtonHint
                            | Qt.WindowCloseButtonHint)
        self.setMinimumSize(480, 520)
        self._rows = 0                 # 消息行数（探针/统计用）
        self._stream_label = None      # 流式输出中的那个气泡（None=没有）
        self._stream_text = ""         # 流式气泡当前全文
        self._stream_buf = ""          # 待上屏的缓冲字符
        speed = "medium"
        try:
            speed = str((getattr(parent, "settings", {}) or {})
                        .get("stream_speed", "medium"))
        except Exception:
            pass
        self._stream_speed = speed if speed in self.STREAM_STEPS else "medium"
        self._stream_timer = QTimer(self)
        self._stream_timer.timeout.connect(self._flush_stream)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        tip = QLabel("同一场对话：这里发的消息 = 气泡模式的消息。关掉窗口（或点「回到气泡」）就切回桌宠气泡～")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#8a6f4d;font-size:11px;")
        lay.addWidget(tip)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            "QScrollArea{background:#f7efe2;border:1px solid #e0cdb2;border-radius:10px;}")
        self._body = QWidget()
        self._body.setStyleSheet("background:transparent;")
        self._vbox = QVBoxLayout(self._body)
        self._vbox.setContentsMargins(8, 8, 8, 8)
        self._vbox.setSpacing(8)
        self._vbox.addStretch(1)
        self._scroll.setWidget(self._body)
        lay.addWidget(self._scroll, 1)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("和麟说点什么…（Enter 发送）")
        self.input.returnPressed.connect(self._send)
        self.input.setStyleSheet(
            "QLineEdit{background:#fdf6ec;border:1px solid #cbb797;border-radius:16px;"
            "padding:7px 14px;color:#3d2b1f;font-size:13px;}")
        b_send = QPushButton("发送")
        b_send.setDefault(True)
        b_send.setStyleSheet(
            "QPushButton{background:#9c6b45;color:#fff;border:none;border-radius:8px;padding:7px 16px;}")
        b_send.clicked.connect(self._send)
        b_back = QPushButton("💬 回到气泡")
        b_back.setStyleSheet(
            "QPushButton{background:#e0c9a8;color:#3d2b1f;border:none;border-radius:8px;padding:7px 12px;}")
        b_back.clicked.connect(self.close)     # 关窗 = 切回气泡模式
        row.addWidget(self.input, 1)
        row.addWidget(b_send)
        row.addWidget(b_back)
        lay.addLayout(row)

    # ---- 对外接口 ----
    def load_history(self, history):
        """打开时把当前对话（win.history）装进来"""
        self._stream_timer.stop()
        self._stream_buf = ""
        while self._vbox.count() > 1:
            item = self._vbox.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._rows = 0
        self._stream_label = None
        for m in (history or []):
            role = m.get("role", "assistant")
            if role in ("user", "assistant"):
                self._add_row(role, m.get("content", ""))
        self._scroll_to_bottom()

    def append_message(self, role, text):
        """双方新消息都走这里（用户从桌宠输入框发的也会同步进来）。
        流式收尾时把最后那个流式气泡定稿，而不是再添一条。"""
        text = str(text or "").strip()
        if not text:
            return
        if role == "assistant" and self._stream_label is not None:
            self._set_bubble_text(self._stream_label, text)
            self._stream_label = None
            self._scroll_to_bottom()
            return
        self._add_row(role, text)
        self._scroll_to_bottom()

    # ---- 流式输出（api_client.ask_llm_stream 经 PetWindow 转发增量） ----
    def append_stream(self, piece):
        """流式增量：第一条增量开一个麟的气泡，之后的增量写进同一个。
        网络增量先进缓冲，由 QTimer 按设置的节奏上屏（instant 直接上）。"""
        piece = str(piece or "")
        if not piece:
            return
        if self._stream_label is None:
            self._add_row("assistant", piece, stream=True)
        else:
            self._stream_text += piece
            if self._stream_speed == "instant":
                self._set_bubble_text(self._stream_label, self._stream_text)
                self._scroll_to_bottom()
                return
            self._stream_buf += piece
            n, ms = self.STREAM_STEPS[self._stream_speed]
            if not self._stream_timer.isActive():
                self._stream_timer.start(ms)

    def _flush_stream(self):
        """把缓冲里的字符按设定节奏搬到气泡上。"""
        if self._stream_label is None or not self._stream_buf:
            self._stream_timer.stop()
            return
        n, _ms = self.STREAM_STEPS[self._stream_speed]
        take = self._stream_buf[:n]
        self._stream_buf = self._stream_buf[n:]
        shown = self._stream_text[:len(self._stream_text) - len(self._stream_buf)]
        self._set_bubble_text(self._stream_label, shown)
        self._scroll_to_bottom()
        if not self._stream_buf:
            self._stream_timer.stop()

    def has_stream_bubble(self):
        return self._stream_label is not None

    def end_stream(self, full_text):
        """流式收尾：停掉节奏定时器，用完整文本定稿（服务端全文替换分片
        拼装，防串字、也保证收尾瞬间补齐缓冲里没上完的字）。"""
        self._stream_timer.stop()
        self._stream_buf = ""
        if self._stream_label is not None:
            self._set_bubble_text(self._stream_label, str(full_text or "").strip())
            self._stream_label = None
        else:
            self.append_message("assistant", full_text)
        self._scroll_to_bottom()

    # ---- 内部 ----
    def _add_row(self, role, text, stream=False):
        bubble = QLabel(str(text or ""))
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # 内边距用 setMargin（会计入 sizeHint），样式表不写 padding——
        # 这是修「文字被气泡裁掉」的关键
        bubble.setMargin(9)
        if role == "user":
            bubble.setStyleSheet(
                "background:#e9d9b8;border-radius:12px;color:#3d2b1f;font-size:13px;")
        else:
            bubble.setStyleSheet(
                "background:#fdf6ec;border:1px solid #e0cdb2;border-radius:12px;"
                "color:#3d2b1f;font-size:13px;")
        bubble.setMaximumWidth(430)
        import time as _time
        tlab = QLabel(("幸歌" if role == "user" else "麟") + " · " + _time.strftime("%H:%M"))
        tlab.setStyleSheet("color:#b9a68c;font-size:10px;")
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        v.addWidget(bubble)
        v.addWidget(tlab, 0, Qt.AlignLeft)
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        if role == "user":
            h.addStretch(1)
            h.addWidget(box)
        else:
            h.addWidget(box)
            h.addStretch(1)
        # 插在末尾 stretch 之前，保持消息顺序
        self._vbox.insertWidget(self._vbox.count() - 1, w)
        self._rows += 1
        if stream:
            self._stream_label = bubble
            self._stream_text = str(text or "")

    def _set_bubble_text(self, bubble, text):
        bubble.setText(str(text or ""))
        self._body.adjustSize()      # 文本变高时让滚动区重算内容高度

    def _scroll_to_bottom(self):
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _send(self):
        text = self.input.text().strip()
        if not text:
            return
        win = self.parent_win
        if win is None:
            return
        self.input.clear()
        self.input.setFocus()
        # 走桌宠同一条发送管线（历史/日志/情绪总结/API 全都一致）
        win._send_text(text)


class HelpDialog(QDialog):
    """2. 动态使用说明书：实时读取本地 readme.md"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("帮助 / 使用说明")
        self.setMinimumSize(560, 480)
        lay = QVBoxLayout(self)
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        path = os.path.join(data_dir(), "readme.md")
        # 说明书可能被用户改坏/超长：任何异常都退化成纯文本，绝不让帮助窗口卡住或打不开
        text = ""
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read(200000)
        except Exception:
            text = ""
        if text.strip():
            try:
                tb.setMarkdown(text)
            except Exception:
                tb.setPlainText(text)
        else:
            tb.setPlainText("readme.md 还没生成或读不出来，先把麟运行一会儿，它会自动写一份说明书～")
        lay.addWidget(tb)
        h = QHBoxLayout()
        edit_btn = QPushButton("用系统记事本打开 readme.md")
        if sys.platform == "win32":
            edit_btn.clicked.connect(lambda: self._open_readme(path))
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        h.addWidget(edit_btn)
        h.addStretch(1)
        h.addWidget(close_btn)
        lay.addLayout(h)

    def _open_readme(self, path):
        """用记事本打开说明书；文件缺失或启动失败只提示，不抛异常（抛了会闪退/像卡死）"""
        if not os.path.exists(path):
            QMessageBox.information(self, "提示", "readme.md 还没生成，先把麟运行一会儿吧～")
            return
        try:
            os.startfile(path)
        except Exception as e:
            QMessageBox.information(self, "打不开", "没能打开记事本：{}".format(e))


class MemoryDialog(QDialog):
    """记忆日志面板：显示最近一篇每日日志 + 一键打开日志文件夹"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("麟的记忆日志")
        self.setMinimumSize(560, 480)
        lay = QVBoxLayout(self)
        tb = QTextBrowser()
        d = os.path.join(data_dir(), MEMORY_LOG_DIR)
        files = sorted([f for f in os.listdir(d) if f.endswith(".md")]) if os.path.isdir(d) else []
        if files:
            with open(os.path.join(d, files[-1]), "r", encoding="utf-8") as f:
                tb.setMarkdown(f.read())
        else:
            tb.setPlainText("还没有记忆日志。多和麟聊聊天，麟会在每天结束时写一篇～")
        lay.addWidget(tb)
        h = QHBoxLayout()
        if sys.platform == "win32":
            btn = QPushButton("打开日志文件夹")
            btn.clicked.connect(lambda: os.startfile(d))
            h.addWidget(btn)
        h.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        h.addWidget(close_btn)
        lay.addLayout(h)


class PersonaDialog(QDialog):
    """3. 核心人设即时修改"""
    def __init__(self, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle("核心人设 / 系统提示词（保存后立即生效）")
        self.setMinimumSize(520, 380)
        lay = QVBoxLayout(self)
        hint = QLabel("直接修改麟的人设、称呼、说话风格。保存后自动重置短期对话上下文，无需重启。")
        hint.setWordWrap(True)
        self.edit = QPlainTextEdit(current)
        lay.addWidget(hint)
        lay.addWidget(self.edit)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def result_text(self):
        return self.edit.toPlainText().strip()


class ProfileDialog(QDialog):
    """4. 御主档案注入"""
    def __init__(self, prof, parent=None):
        super().__init__(parent)
        self.setWindowTitle("御主档案（会注入每次对话最前端）")
        form = QFormLayout(self)
        self.e_nick = QLineEdit(prof.get("nickname", ""))
        self.e_birth = QLineEdit(prof.get("birthday", ""))
        self.e_call = QLineEdit(prof.get("how_to_call", ""))
        self.e_rel = QLineEdit(prof.get("relation", ""))
        form.addRow("昵称", self.e_nick)
        form.addRow("生日", self.e_birth)
        form.addRow("对我的称呼", self.e_call)
        form.addRow("关系设定", self.e_rel)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        form.addRow(box)


class FocusDialog(QDialog):
    """6. 专注时钟设置"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("专注时钟")
        form = QFormLayout(self)
        self.spin = QSpinBox()
        self.spin.setRange(1, 240)
        self.spin.setValue(25)
        self.spin.setSuffix(" 分钟")
        form.addRow("专注时长", self.spin)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        form.addRow(box)

    def minutes(self):
        return self.spin.value()


class BackpackDialog(QDialog):
    """7. 喂食与背包系统"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("背包（点击食物喂给麟）")
        self.setMinimumSize(460, 320)
        self.parent = parent
        lay = QVBoxLayout(self)
        self.grid = QGridLayout()
        lay.addLayout(self.grid)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)
        self.refresh()

    def refresh(self):
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        items = load_json("items.json", {}).get("items", [])
        for i, it in enumerate(items):
            btn = QPushButton("{} {}  x{}".format(it.get("icon", "🍽"), it.get("name", ""), it.get("count", 0)))
            btn.setStyleSheet("QPushButton{background:#f0e2cc;color:#3d2b1f;border-radius:8px;padding:8px;font-size:13px;}"
                              "QPushButton:hover{background:#e0c9a8;}")
            btn.clicked.connect(lambda _, n=it.get("name", ""): self.feed(n))
            self.grid.addWidget(btn, i // 2, i % 2)
        self.grid.setRowStretch((len(items) + 1) // 2, 1)

    def feed(self, name):
        win = self.parent
        # 防抖：0.8 秒内重复点击直接忽略（修复批量喂食崩溃）
        if time.time() - getattr(win, "_feed_lock", 0) < 0.8:
            win.say("慢点慢点～麟还在吃上一个呢！", split=False)
            return
        win._feed_lock = time.time()
        items = load_json("items.json", {})
        found = None
        for it in items.get("items", []):
            if it.get("name") == name:
                found = it
                break
        if not found or found.get("count", 0) <= 0:
            win.say("唔…{}已经吃光啦～".format(name), split=False)
            return
        found["count"] -= 1
        save_json("items.json", items)
        win._play_eat()
        win.add_affinity(5)
        win.add_coins(2)
        # 连续喂食 3 次后，吃完会打嗝（burp.png）
        win._feed_streak = getattr(win, "_feed_streak", 0) + 1
        if win._feed_streak >= 3:
            win._feed_streak = 0
            win._want_burp = True
        # 上一条口感评价还没回就不重复请求（降低主线程/接口压力）
        if getattr(win, "_pending_food", None) is None:
            win._pending_food = (name, found.get("reply", "好吃！"))
            msgs = [{"role": "system",
                     "content": "你是麟。请用一句（30字内）以麟的口吻评价刚吃到的食物【{}】的口感，语气可爱。".format(name)}]
            ask_llm(msgs, win.bus, kind="food", temperature=1.2, max_tokens=60)
        self.refresh()


class ScheduleDialog(QDialog):
    """9. 日程提醒助手"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("日程提醒")
        self.setMinimumSize(420, 360)
        lay = QVBoxLayout(self)
        add_row = QHBoxLayout()
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        self.event_edit = QLineEdit()
        self.event_edit.setPlaceholderText("事项内容，如：开会")
        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self.add_one)
        add_row.addWidget(self.time_edit)
        add_row.addWidget(self.event_edit, 1)
        add_row.addWidget(add_btn)
        lay.addLayout(add_row)
        self.list = QListWidget()
        lay.addWidget(self.list)
        del_btn = QPushButton("删除选中")
        del_btn.clicked.connect(self.del_one)
        lay.addWidget(del_btn)
        self.refresh()

    def refresh(self):
        self.list.clear()
        for it in load_json("schedule.json", []):
            QListWidgetItem("{}  {}".format(it.get("time", ""), it.get("event", "")), self.list)

    def add_one(self):
        t = self.time_edit.time().toString("HH:mm")
        e = self.event_edit.text().strip()
        if not e:
            return
        sched = load_json("schedule.json", [])
        sched.append({"time": t, "event": e})
        save_json("schedule.json", sched)
        self.event_edit.clear()
        self.refresh()

    def del_one(self):
        row = self.list.currentRow()
        if row < 0:
            return
        sched = load_json("schedule.json", [])
        if 0 <= row < len(sched):
            sched.pop(row)
            save_json("schedule.json", sched)
            self.refresh()


class EventDialog(QDialog):
    """10. 随机事件选项框"""
    def __init__(self, ev, parent=None):
        super().__init__(parent)
        self.chosen = None
        self.setWindowTitle(ev.get("title", "随机事件"))
        lay = QVBoxLayout(self)
        txt = QLabel(ev.get("text", ""))
        txt.setWordWrap(True)
        lay.addWidget(txt)
        bA = QPushButton("A. " + ev.get("a", ""))
        bB = QPushButton("B. " + ev.get("b", ""))
        bA.clicked.connect(lambda: self._pick("a"))
        bB.clicked.connect(lambda: self._pick("b"))
        lay.addWidget(bA)
        lay.addWidget(bB)

    def _pick(self, k):
        self.chosen = k
        self.accept()


class RPSDialog(QDialog):
    """18. 猜拳（重构：分区布局 + 结果配色 + 自适应字号）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("和麟猜拳")
        self.setMinimumSize(380, 320)
        self.parent = parent
        self.setStyleSheet("QDialog{background:#fdf6ec;}")
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.setContentsMargins(20, 20, 20, 20)

        title = QLabel("✊✌🖐 石头剪刀布")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size:18px;font-weight:bold;color:#3d2b1f;")
        lay.addWidget(title)

        self.result = QLabel("来吧！选择你的出拳～")
        self.result.setWordWrap(True)
        self.result.setAlignment(Qt.AlignCenter)
        self.result.setMinimumHeight(64)
        self.result.setStyleSheet(
            "font-size:15px;color:#3d2b1f;background:#f0e2cc;border-radius:10px;padding:12px;")
        lay.addWidget(self.result)

        hint = QLabel("【你的出拳】")
        hint.setStyleSheet("font-size:12px;color:#8a6f4d;")
        lay.addWidget(hint)

        h = QHBoxLayout()
        h.setSpacing(10)
        for label in ["✊ 石头", "✌ 剪刀", "🖐 布"]:
            b = QPushButton(label)
            b.setStyleSheet("QPushButton{background:#9c6b45;color:#fff;border:none;border-radius:12px;"
                            "padding:10px 14px;font-size:15px;}"
                            "QPushButton:hover{background:#b58155;}")
            b.clicked.connect(lambda _, l=label: self.play(l))
            h.addWidget(b, 1)
        lay.addLayout(h)

        again = QPushButton("🔁 再来一局")
        again.setStyleSheet("QPushButton{background:#e0c9a8;color:#3d2b1f;border:none;border-radius:10px;padding:8px;}"
                            "QPushButton:hover{background:#d4b892;}")
        again.clicked.connect(self.reset_view)
        lay.addWidget(again)

    def reset_view(self):
        self.result.setText("来吧！选择你的出拳～")
        self.result.setStyleSheet(
            "font-size:15px;color:#3d2b1f;background:#f0e2cc;border-radius:10px;padding:12px;")

    def play(self, mine):
        she = random.choice(["✊ 石头", "✌ 剪刀", "🖐 布"])
        m = {"✊": 0, "✌": 1, "🖐": 2}[mine[0]]
        s = {"✊": 0, "✌": 1, "🖐": 2}[she[0]]
        if m == s:
            res = "🤝 平局！\n麟和你都出了{}，再来一次？".format(mine)
            color = "#8a6f4d"
        elif (m - s) % 3 == 1:
            res = "🎉 你赢了！\n麟出{}，你出{}（金币+10，好感+3）".format(she, mine)
            color = "#3aa76d"
            self.parent.add_coins(10)
            self.parent.add_affinity(3)
        else:
            res = "😝 麟赢啦！\n麟出{}，你出{}".format(she, mine)
            color = "#c05b4f"
            self.parent.add_affinity(1)
        self.result.setText(res)
        self.result.setStyleSheet(
            "font-size:15px;color:{};background:#f0e2cc;border-radius:10px;padding:12px;".format(color))


class DiceDialog(QDialog):
    """18. 掷骰子 1-100（重构：数字大、分行显示、结果配色）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("掷骰子（1-6 比大小）")
        self.setMinimumSize(380, 320)
        self.parent = parent
        self.setStyleSheet("QDialog{background:#fdf6ec;}")
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.setContentsMargins(20, 20, 20, 20)

        title = QLabel("🎲 骰子对决")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size:18px;font-weight:bold;color:#3d2b1f;")
        lay.addWidget(title)

        self.result = QLabel("点下面的按钮开始～")
        self.result.setWordWrap(True)
        self.result.setAlignment(Qt.AlignCenter)
        self.result.setMinimumHeight(90)
        self.result.setStyleSheet(
            "font-size:17px;color:#3d2b1f;background:#f0e2cc;border-radius:10px;padding:12px;")
        lay.addWidget(self.result)

        b = QPushButton("🎲 掷骰子")
        b.setStyleSheet("QPushButton{background:#9c6b45;color:#fff;border:none;border-radius:12px;"
                        "padding:10px;font-size:15px;}"
                        "QPushButton:hover{background:#b58155;}")
        b.clicked.connect(self.roll)
        lay.addWidget(b)

        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet("QPushButton{background:#e0c9a8;color:#3d2b1f;border:none;border-radius:10px;padding:8px;}"
                                "QPushButton:hover{background:#d4b892;}")
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)

    def roll(self):
        me = random.randint(1, 6)
        she = random.randint(1, 6)
        if me > she:
            r = "你：{}\n麟：{}\n\n🎉 你赢啦！（金币+10）".format(me, she)
            color = "#3aa76d"
            self.parent.add_coins(10)
        elif me < she:
            r = "你：{}\n麟：{}\n\n😝 麟赢啦！".format(me, she)
            color = "#c05b4f"
        else:
            r = "你：{}\n麟：{}\n\n🤝 平局！".format(me, she)
            color = "#8a6f4d"
        self.result.setText(r)
        self.result.setStyleSheet(
            "font-size:17px;color:{};background:#f0e2cc;border-radius:10px;padding:12px;".format(color))


class OrganizeDialog(QDialog):
    """19. 本地文件一键整理"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("一键整理文件")
        self.parent = parent
        form = QFormLayout(self)
        self.src = QLineEdit(os.path.expanduser("~/Desktop"))
        self.dst = QLineEdit(os.path.expanduser("~/Desktop"))
        b_src = QPushButton("选择…")
        b_src.clicked.connect(lambda: self.pick(self.src))
        b_dst = QPushButton("选择…")
        b_dst.clicked.connect(lambda: self.pick(self.dst))
        h1 = QHBoxLayout()
        h1.addWidget(self.src, 1)
        h1.addWidget(b_src)
        h2 = QHBoxLayout()
        h2.addWidget(self.dst, 1)
        h2.addWidget(b_dst)
        form.addRow("杂乱目录", h1)
        form.addRow("目标目录", h2)
        go = QPushButton("开始整理")
        go.clicked.connect(self.do_it)
        form.addRow(go)
        self.out = QLabel("")
        self.out.setWordWrap(True)
        form.addRow(self.out)

    def pick(self, le):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹", le.text() or os.path.expanduser("~/Desktop"))
        if d:
            le.setText(d)

    def do_it(self):
        src = self.src.text().strip()
        dst = self.dst.text().strip()
        if not src or not os.path.isdir(src):
            self.out.setText("源目录无效")
            return
        cats = {"图片": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"],
                "文档": [".txt", ".doc", ".docx", ".pdf", ".xls", ".xlsx", ".ppt", ".pptx", ".md"],
                "压缩包": [".zip", ".rar", ".7z", ".tar", ".gz"],
                "音频": [".mp3", ".wav", ".flac", ".m4a"],
                "视频": [".mp4", ".avi", ".mkv", ".mov"],
                "安装包": [".exe", ".msi"]}
        counts = {}
        for fn in os.listdir(src):
            p = os.path.join(src, fn)
            if not os.path.isfile(p):
                continue
            ext = os.path.splitext(fn)[1].lower()
            cat = "其他"
            for c, exts in cats.items():
                if ext in exts:
                    cat = c
                    break
            folder = os.path.join(dst, cat)
            os.makedirs(folder, exist_ok=True)
            try:
                newp = os.path.join(folder, fn)
                if os.path.exists(newp):
                    continue
                os.rename(p, newp)
                counts[cat] = counts.get(cat, 0) + 1
            except Exception:
                pass
        total = sum(counts.values())
        if counts:
            msg = "整理完毕！移动 {} 个文件：".format(total) + "，".join(
                "{} {}个".format(k, v) for k, v in counts.items())
        else:
            msg = "没有可移动的文件。"
        self.out.setText(msg)
        self.parent.add_coins(5)
        self.parent.say("桌面整理完毕！麟很能干吧？(金币+5)", split=False)


class NoteDialog(QDialog):
    """24. 随手记/便签条"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("记一笔（追加到 notes.txt）")
        self.setMinimumSize(400, 240)
        self.parent = parent
        lay = QVBoxLayout(self)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("随手记点什么灵感吧…")
        lay.addWidget(self.edit)
        h = QHBoxLayout()
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self.save_note)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        h.addStretch(1)
        h.addWidget(save_btn)
        h.addWidget(cancel_btn)
        lay.addLayout(h)

    def save_note(self):
        text = self.edit.toPlainText().strip()
        if text:
            with open(os.path.join(data_dir(), "notes.txt"), "a", encoding="utf-8") as f:
                f.write("[{}] {}\n".format(time.strftime("%Y-%m-%d %H:%M"), text))
            self.parent.say("记好啦～麟帮你收进小本本里了。", split=False)
        self.accept()


class ShopDialog(QDialog):
    """20. 商城与经济系统"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("商城")
        self.setMinimumSize(520, 380)
        self.parent = parent
        lay = QVBoxLayout(self)
        self.balance = QLabel("")
        lay.addWidget(self.balance)
        self.list = QListWidget()
        lay.addWidget(self.list)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)
        self.refresh()

    def refresh(self):
        self.balance.setText("💎 金币：{}    ❤️ 好感：{}".format(self.parent.coins, self.parent.affinity))
        self.list.clear()
        for item in SHOP_ITEMS:
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(8, 4, 8, 4)
            lab = QLabel("{}  💰{}  {}".format(item["name"], item["price"], item["desc"]))
            lab.setWordWrap(True)
            btn = QPushButton("购买")
            btn.clicked.connect(lambda _, it=item: self.buy(it))
            h.addWidget(lab, 1)
            h.addWidget(btn)
            w.adjustSize()
            it = QListWidgetItem()
            it.setSizeHint(w.sizeHint())
            self.list.addItem(it)
            self.list.setItemWidget(it, w)

    def buy(self, item):
        win = self.parent
        if win.coins < item["price"]:
            win.say("金币不够哦～专注、签到、完成任务都能赚金币！", split=False)
            return
        win.add_coins(-item["price"])
        t = item["type"]
        if t == "aff":
            win.add_affinity(item["value"])
            win.say("购买成功！好感+{}！".format(item["value"]), split=False)
        elif t == "mood":
            win.update_mood(item["value"])
            win.say("购买成功！麟的心情一下子好起来啦～", split=False)
        elif t == "luck":
            win.economy["lucky_date"] = time.strftime("%Y-%m-%d")
            save_json("economy.json", win.economy)
            win.say("御守挂好啦～今天的运势会更好哦！", split=False)
        elif t == "memory":
            win.add_affinity(5)
            win.say("麟正在把今天的回忆整理进小本本…（好感+5）", split=False)
            win.archive_memory(force=True)
        self.refresh()


class MoodRecordDialog(QDialog):
    """心理陪伴：记录情绪（10 种情绪 + 5 级强度 + 触发标签 + 备注/日记）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📝 记录情绪")
        self.setMinimumSize(560, 600)
        self.parent = parent
        self.selected_mood = "neutral"
        self.intensity = 3
        self.selected_tags = set()
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 16, 16, 16)

        lay.addWidget(QLabel("现在感觉怎么样？"))
        grid = QGridLayout()
        self.mood_btns = {}
        for i, m in enumerate(MOOD_TYPES):
            b = QPushButton("{} {}".format(m["emoji"], m["name"]))
            b.setCheckable(True)
            b.setStyleSheet(
                "QPushButton{background:#f0e2cc;color:#3d2b1f;border:none;"
                "border-radius:8px;padding:7px 4px;font-size:13px;}"
                "QPushButton:checked{background:#9c6b45;color:#fff;}")
            b.clicked.connect(lambda _, k=m["key"], btn=b: self._pick_mood(k, btn))
            grid.addWidget(b, i // 5, i % 5)
            self.mood_btns[m["key"]] = b
        self.mood_btns["neutral"].setChecked(True)
        lay.addLayout(grid)

        lay.addWidget(QLabel("强度："))
        ih = QHBoxLayout()
        self.intensity_btns = {}
        for i in range(1, 6):
            b = QPushButton(INTENSITY_NAMES[i - 1])
            b.setCheckable(True)
            b.setStyleSheet(
                "QPushButton{background:#f0e2cc;color:#3d2b1f;border:none;"
                "border-radius:8px;padding:6px;font-size:12px;}"
                "QPushButton:checked{background:#b58155;color:#fff;}")
            b.clicked.connect(lambda _, v=i: self._pick_intensity(v))
            ih.addWidget(b)
            self.intensity_btns[i] = b
        self.intensity_btns[3].setChecked(True)
        lay.addLayout(ih)

        lay.addWidget(QLabel("触发原因（可多选）："))
        tg = QGridLayout()
        self.tag_checks = {}
        for i, (label, emoji) in enumerate(MOOD_TAGS):
            c = QCheckBox("{} {}".format(emoji, label))
            c.toggled.connect(lambda _, l=label: self._toggle_tag(l))
            tg.addWidget(c, i // 4, i % 4)
            self.tag_checks[label] = c
        lay.addLayout(tg)

        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("一句话备注（可选）")
        lay.addWidget(self.note_edit)
        self.diary_edit = QPlainTextEdit()
        self.diary_edit.setPlaceholderText("日记：写下更深层的感受（可选）")
        self.diary_edit.setMaximumHeight(110)
        lay.addWidget(self.diary_edit)

        h = QHBoxLayout()
        save_btn = QPushButton("保存")
        save_btn.setStyleSheet("QPushButton{background:#9c6b45;color:#fff;border:none;"
                               "border-radius:10px;padding:8px 22px;}")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        h.addStretch(1)
        h.addWidget(save_btn)
        h.addWidget(cancel_btn)
        lay.addLayout(h)

    def _pick_mood(self, key, btn):
        self.selected_mood = key
        for k, b in self.mood_btns.items():
            b.setChecked(k == key)

    def _pick_intensity(self, v):
        self.intensity = v
        for k, b in self.intensity_btns.items():
            b.setChecked(k == v)

    def _toggle_tag(self, label):
        if label in self.selected_tags:
            self.selected_tags.discard(label)
        else:
            self.selected_tags.add(label)

    def _save(self):
        rec = add_record(self.selected_mood, self.intensity,
                         sorted(self.selected_tags),
                         self.note_edit.text().strip(),
                         self.diary_edit.toPlainText().strip())
        if hasattr(self.parent, "on_mood_recorded"):
            self.parent.on_mood_recorded(rec)
        self.accept()


class MoodHistoryDialog(QDialog):
    """心理陪伴：情绪历史时间线（可删除）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📅 情绪历史")
        self.setMinimumSize(560, 420)
        self.parent = parent
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setStyleSheet("QListWidget{background:#fdf6ec;color:#3d2b1f;font-size:13px;}")
        lay.addWidget(self.list)
        h = QHBoxLayout()
        del_btn = QPushButton("删除选中")
        del_btn.clicked.connect(self.delete_row)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        h.addWidget(del_btn)
        h.addStretch(1)
        h.addWidget(close_btn)
        lay.addLayout(h)
        self.refresh()

    def refresh(self):
        self.list.clear()
        records = load_moods().get("records", [])
        for i, r in enumerate(reversed(records)):
            idx = len(records) - 1 - i
            tags = "[" + "、".join(r.get("tags", [])) + "]" if r.get("tags") else ""
            note = r.get("note", "") or ""
            src = " ·AI总结" if r.get("source") == "chat" else ""
            line = "{}  {} {}（{}/5）{}{}{}".format(
                (r.get("created_at") or "")[5:16],
                mood_emoji(r.get("mood", "neutral")),
                mood_name(r.get("mood", "neutral")),
                r.get("intensity", 3), tags,
                ("  " + note) if note else "", src)
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(8, 4, 8, 4)
            lab = QLabel(line)
            lab.setWordWrap(True)
            h.addWidget(lab, 1)
            w.adjustSize()
            it = QListWidgetItem()
            it.setSizeHint(w.sizeHint())
            self.list.addItem(it)
            self.list.setItemWidget(it, w)

    def delete_row(self):
        row = self.list.currentRow()
        if row < 0:
            return
        records = load_moods().get("records", [])
        idx = len(records) - 1 - row
        delete_record(idx)
        self.refresh()


class MoodStatsDialog(QDialog):
    """心理陪伴：情绪统计（打卡/近 7 天/波动/预警/时段规律）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📊 情绪统计")
        self.setMinimumSize(520, 460)
        lay = QVBoxLayout(self)
        tb = QTextBrowser()
        data = load_moods()
        records = data.get("records", [])
        res = analyze(records)
        lines = ["## 情绪统计", ""]
        lines.append("连续记录：**{} 天**".format(data.get("streak", 0)))
        lines.append("近 7 天记录：**{} 条**".format(res["count"]))
        if res["count"]:
            lines.append("平均强度：**{:.1f} / 5**".format(res["avg_intensity"]))
            lines.append("波动等级：**{}**（标准差 {:.2f}）".format(
                "平稳" if res["volatility"] < 0.5 else
                ("轻微波动" if res["volatility"] < 1.0 else
                 ("中度波动" if res["volatility"] < 1.5 else "剧烈波动")),
                res["volatility"]))
        lines.append("")
        if res["warnings"]:
            lines.append("## ⚠️ 提醒")
            lines += ["- " + w for w in res["warnings"]]
            lines.append("")
        if res["cycles"]:
            lines.append("## 时段情绪规律")
            lines += ["- {}：常感到 {}".format(p, m) for p, m in sorted(res["cycles"].items())]
        else:
            lines.append("记录 2 条以上情绪后，麟就能帮你看出情绪波动和规律哦～")
        lines.append("")
        lines.append("> 情绪记录只保存在本地（mood.json），不会上传。")
        tb.setMarkdown("\n".join(lines))
        lay.addWidget(tb)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)


class CrisisSupportDialog(QDialog):
    """心理陪伴：危机支持（心理援助热线）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🆘 危机支持")
        self.setMinimumSize(520, 380)
        lay = QVBoxLayout(self)
        tb = QTextBrowser()
        lines = ["# 当你需要帮助的时候", "",
                 "如果你此刻感到痛苦、无助或处于危机中，请一定联系专业人士。",
                 "求助不是软弱，而是对自己的保护。", "",
                 "## 心理援助热线", ""]
        lines += ["- " + x for x in CRISIS_LINES]
        lines += ["", "> 热线信息请以官方最新发布为准。", "",
                  "麟会一直陪着你，但专业支持更重要，请别独自硬扛。"]
        tb.setMarkdown("\n".join(lines))
        lay.addWidget(tb)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)


def _render_assessment_md(r):
    """把一条评测结果渲染成 Markdown（结果报告 / 历史详情共用）"""
    lines = ["# {} · 评测结果".format(r.get("scale_name", "心理评测")), ""]
    lines.append("**完成时间：**{}".format(r.get("created_at", "")))
    lines.append("")
    total_label = r.get("total_label", "得分")
    lines.append("**{}：{} / {}**　·　**{}**".format(
        total_label, r.get("total", 0), r.get("max", 0), r.get("level", "")))
    lines.append("")
    lines.append(r.get("advice", ""))
    dims = r.get("dimensions") or {}
    if dims:
        lines += ["", "## 维度分析", ""]
        for k, d in dims.items():
            name = d.get("name", k)
            val = ""
            if "letter" in d:
                val = "{}（{}%）".format(d.get("letter", ""),
                                        d.get("first_ratio", ""))
            elif "value" in d:
                val = str(d.get("value"))
            elif "avg" in d:
                val = "均分 {}".format(d.get("avg"))
            elif "t" in d:
                val = "T 分 {}".format(d.get("t"))
            elif "total" in d:
                val = "总分 {}".format(d.get("total"))
            lv = d.get("level", "")
            if lv:
                val = "{}{}".format(val, "（{}）".format(lv) if val else lv)
            line = "- **{}**：{}".format(name, val or "—")
            if d.get("high") or d.get("low"):
                line += "　（高分：{}／低分：{}）".format(d.get("high", ""),
                                                     d.get("low", ""))
            lines.append(line)
            desc = d.get("desc", "")
            if desc:
                lines.append("  " + desc)
        lines.append("")
    crisis = r.get("crisis")
    need_care = crisis or int(r.get("total", 0)) >= 10
    if crisis:
        items = "、".join(r.get("crisis_items", []) or ["相关题目"])
        lines += ["", "> ⚠️ **你在以下题目上勾选了非最轻选项：{}**".format(items),
                  "> 这非常重要：请一定不要独自承受，立即联系信任的人或心理援助热线。"]
    elif need_care:
        lines += ["", "> 得分已达到需要注意的水平，建议与信任的人聊聊，或咨询专业人士。"]
    if need_care:
        lines += ["", "## 🆘 心理援助热线", ""]
        lines += ["- " + x for x in CRISIS_LINES]
    answers = r.get("answers", [])
    lines += ["", "## 逐题回顾", ""]
    if len(answers) <= 30:
        for i, d in enumerate(answers, 1):
            lines.append("{}. {}（{}）".format(
                i, d.get("q", ""), d.get("text", "")))
    else:
        lines.append("本量表共 {} 题，逐题作答已保存在本地 assessment.json 中。".format(
            len(answers)))
    lines += ["", "---",
              "> 本评测仅供自评参考，不构成医学诊断。如需帮助，请咨询专业心理医生或拨打热线。",
              "> 结果保存在本地 assessment.json，绝不上传。"]
    return "\n".join(lines)


class AssessmentDialog(QDialog):
    """心理评测：量表选择 → 逐题作答 → 自动计分结果报告（本地存储）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📋 心理评测")
        self.setMinimumSize(620, 540)
        self.parent = parent
        self.scale = None
        self.q_index = 0
        self.answers = []
        self.result = None
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 16, 16, 16)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self._build_select_page()
        self._build_question_page()
        self._build_result_page()
        self.stack.setCurrentIndex(0)

    def _build_select_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(10)
        title = QLabel("📋 心理评测")
        title.setStyleSheet("font-size:18px;font-weight:bold;color:#3d2b1f;")
        v.addWidget(title)
        tip = QLabel("共 {} 套本地量表，分为 3 大类。\n"
                     "⬇ 上下滑动可查看并选择全部量表；请凭真实感受作答，没有标准答案；\n"
                     "结果仅用于自我了解，不构成医学诊断。".format(len(SCALES)))
        tip.setWordWrap(True)
        v.addWidget(tip)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        inner = QWidget()
        box = QVBoxLayout(inner)
        box.setSpacing(8)
        for cat, items in scales_by_category():
            cat_lab = QLabel("▍{}（{} 套）".format(cat, len(items)))
            cat_lab.setStyleSheet("font-size:13px;font-weight:bold;"
                                  "color:#8a6b50;margin-top:6px;")
            box.addWidget(cat_lab)
            for s in items:
                minutes = max(1, round(len(s["questions"]) / 10.0))
                b = QPushButton("{}　（{} 题，约 {} 分钟）".format(
                    s["name"], len(s["questions"]), minutes))
                b.setStyleSheet("QPushButton{background:#f0e2cc;color:#3d2b1f;"
                                "border:none;border-radius:10px;padding:10px 14px;"
                                "font-size:13px;text-align:left;}"
                                "QPushButton:hover{background:#e4d1b4;}")
                b.clicked.connect(lambda _, k=s["key"]: self._start_scale(k))
                box.addWidget(b)
        box.addStretch(1)
        scroll.setWidget(inner)
        v.addWidget(scroll, 1)
        note = QLabel("完成后结果会保存在本地 assessment.json，绝不上传。")
        note.setStyleSheet("color:#8a6b50;font-size:12px;")
        v.addWidget(note)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        v.addWidget(close_btn)
        v.addStretch(1)
        self.stack.addWidget(page)

    def _build_question_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setSpacing(8)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet(
            "QProgressBar{border:none;background:#efe4d2;border-radius:4px;"
            "height:6px;} QProgressBar::chunk{background:#9c6b45;border-radius:4px;}")
        v.addWidget(self.progress_bar)
        self.progress_lab = QLabel("")
        self.progress_lab.setStyleSheet("color:#9c6b45;font-size:12px;")
        v.addWidget(self.progress_lab)
        self.q_lab = QLabel("")
        self.q_lab.setWordWrap(True)
        self.q_lab.setStyleSheet("font-size:15px;color:#3d2b1f;"
                                 "padding:2px 0 6px 0;")
        v.addWidget(self.q_lab)
        self.opt_scroll = QScrollArea()
        self.opt_scroll.setWidgetResizable(True)
        self.opt_scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:1px solid #efe4d2;"
            "border-radius:8px;}")
        opt_widget = QWidget()
        self.opt_box = QVBoxLayout(opt_widget)
        self.opt_box.setSpacing(6)
        self.opt_box.setContentsMargins(10, 10, 10, 10)
        self.opt_scroll.setWidget(opt_widget)
        v.addWidget(self.opt_scroll, 1)
        h = QHBoxLayout()
        self.prev_btn = QPushButton("上一题")
        self.prev_btn.clicked.connect(self._prev_q)
        self.next_btn = QPushButton("下一题")
        self.next_btn.setStyleSheet("QPushButton{background:#9c6b45;color:#fff;"
                                    "border:none;border-radius:10px;padding:8px 22px;}")
        self.next_btn.clicked.connect(self._next_q)
        h.addWidget(self.prev_btn)
        h.addStretch(1)
        h.addWidget(self.next_btn)
        v.addLayout(h)
        self.stack.addWidget(page)

    def _clear_opts(self):
        while self.opt_box.count():
            item = self.opt_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _build_result_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        self.result_tb = QTextBrowser()
        self.result_tb.setStyleSheet("QTextBrowser{background:#fdf6ec;color:#3d2b1f;"
                                     "border:1px solid #e4d1b4;border-radius:8px;}")
        v.addWidget(self.result_tb, 1)
        h = QHBoxLayout()
        done_btn = QPushButton("完成")
        done_btn.setStyleSheet("QPushButton{background:#9c6b45;color:#fff;"
                               "border:none;border-radius:10px;padding:8px 24px;}")
        done_btn.clicked.connect(self._done)
        h.addStretch(1)
        h.addWidget(done_btn)
        v.addLayout(h)
        self.stack.addWidget(page)

    def _start_scale(self, key):
        self.scale = SCALES_BY_KEY[key]
        self.q_index = 0
        self.answers = [0] * len(self.scale["questions"])
        self._show_q()
        self.stack.setCurrentIndex(1)

    def _checked(self):
        for i, rb in enumerate(self.radio_btns):
            if rb.isChecked():
                return i
        return 0

    def _show_q(self):
        s = self.scale
        n = len(s["questions"])
        q = s["questions"][self.q_index]
        self.progress_bar.setRange(0, n - 1)
        self.progress_bar.setValue(self.q_index)
        self.progress_lab.setText("{} · 第 {} / {} 题".format(
            s["name"], self.q_index + 1, n))
        self.q_lab.setText(q.get("text", ""))
        self._clear_opts()
        labels = self.scale["options"]
        if isinstance(labels, dict):
            labels = labels.get(q.get("option_type") or "normal", [])
        if labels and isinstance(labels[0], dict):
            labels = [o["label"] for o in labels]
        val = self.answers[self.q_index]
        self.radio_btns = []
        for i, lab in enumerate(labels):
            rb = QRadioButton(lab)
            rb.setStyleSheet("QRadioButton{color:#3d2b1f;font-size:14px;"
                             "padding:6px 8px;}")
            rb.setChecked(i == val)
            self.radio_btns.append(rb)
            self.opt_box.addWidget(rb)
        self.opt_box.addStretch(1)
        self.prev_btn.setEnabled(self.q_index > 0)
        self.next_btn.setText("完成评测" if self.q_index == n - 1 else "下一题")

    def _prev_q(self):
        if self.q_index > 0:
            self.q_index -= 1
            self._show_q()

    def _next_q(self):
        self.answers[self.q_index] = self._checked()
        if self.q_index < len(self.scale["questions"]) - 1:
            self.q_index += 1
            self._show_q()
        else:
            self.result = add_assessment(self.scale["key"], self.answers)
            self.result_tb.setMarkdown(_render_assessment_md(self.result))
            self.stack.setCurrentIndex(2)

    def _done(self):
        if hasattr(self.parent, "on_assessment_done") and self.result:
            self.parent.on_assessment_done(self.result)
        self.accept()


class AssessmentResultDialog(QDialog):
    """评测结果详情（历史记录双击查看）"""
    def __init__(self, result, parent=None):
        super().__init__(parent)
        self.setWindowTitle("{} · 结果详情".format(
            result.get("scale_name", "评测结果")))
        self.setMinimumSize(580, 480)
        lay = QVBoxLayout(self)
        tb = QTextBrowser()
        tb.setStyleSheet("QTextBrowser{background:#fdf6ec;color:#3d2b1f;"
                         "border:1px solid #e4d1b4;border-radius:8px;}")
        tb.setMarkdown(_render_assessment_md(result))
        lay.addWidget(tb)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)


class AssessmentHistoryDialog(QDialog):
    """心理评测：历史记录（双击/按钮查看详情，可删除）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🗂 评测历史")
        self.setMinimumSize(620, 460)
        self.parent = parent
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setStyleSheet("QListWidget{background:#fdf6ec;color:#3d2b1f;"
                                "font-size:13px;}")
        self.list.itemDoubleClicked.connect(self._view_detail)
        lay.addWidget(self.list)
        h = QHBoxLayout()
        view_btn = QPushButton("查看详情")
        view_btn.clicked.connect(self._view_detail)
        del_btn = QPushButton("删除选中")
        del_btn.clicked.connect(self.delete_row)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        h.addWidget(view_btn)
        h.addWidget(del_btn)
        h.addStretch(1)
        h.addWidget(close_btn)
        lay.addLayout(h)
        self.refresh()

    def refresh(self):
        self.list.clear()
        records = load_assessments().get("records", [])
        for i, r in enumerate(reversed(records)):
            idx = len(records) - 1 - i
            line = "{}  {} {}分（满分{}）·{}{}".format(
                (r.get("created_at") or "")[5:16],
                r.get("scale_name", ""), r.get("total", 0),
                r.get("max", 0), r.get("level", ""),
                " ⚠️" if r.get("crisis") else "")
            w = QWidget()
            hh = QHBoxLayout(w)
            hh.setContentsMargins(8, 4, 8, 4)
            lab = QLabel(line)
            lab.setWordWrap(True)
            hh.addWidget(lab, 1)
            w.adjustSize()
            it = QListWidgetItem()
            it.setData(Qt.UserRole, idx)
            it.setSizeHint(w.sizeHint())
            self.list.addItem(it)
            self.list.setItemWidget(it, w)

    def _selected_index(self):
        row = self.list.currentRow()
        records = load_assessments().get("records", [])
        if row < 0 or not records:
            return None
        return len(records) - 1 - row

    def _view_detail(self):
        idx = self._selected_index()
        if idx is None:
            return
        records = load_assessments().get("records", [])
        if 0 <= idx < len(records):
            AssessmentResultDialog(records[idx], self).exec_()

    def delete_row(self):
        idx = self._selected_index()
        if idx is None:
            return
        delete_assessment(idx)
        self.refresh()


class ApiEditDialog(QDialog):
    """编辑/新增一个 API 条目（OpenAI 兼容接口）"""
    def __init__(self, api=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑 API" if api else "添加 API")
        form = QFormLayout(self)
        self.e_name = QLineEdit(api.get("name", "") if api else "")
        self.e_url = QLineEdit(api.get("base_url", "") if api else "")
        self.e_url.setPlaceholderText("https://.../v1/chat/completions")
        self.e_model = QLineEdit(api.get("model", "") if api else "")
        self.e_key = QLineEdit(api.get("key", "") if api else "")
        self.e_key.setEchoMode(QLineEdit.Password)
        self.c_enabled = QCheckBox("启用此 API")
        self.c_enabled.setChecked(api.get("enabled", True) if api else True)
        form.addRow("名称", self.e_name)
        form.addRow("接口地址", self.e_url)
        form.addRow("模型", self.e_model)
        form.addRow("API Key", self.e_key)
        form.addRow("", self.c_enabled)
        tip = QLabel("本地 Ollama 可留空 Key；接口需为 OpenAI 兼容格式 /chat/completions")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#8a6f4d;font-size:12px;")
        form.addRow(tip)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        form.addRow(box)

    def result_data(self):
        return {"name": self.e_name.text().strip(),
                "base_url": self.e_url.text().strip(),
                "model": self.e_model.text().strip(),
                "key": self.e_key.text().strip(),
                "enabled": self.c_enabled.isChecked()}


class ApiManagerDialog(QDialog):
    """API 仓库管理：多 API 增删改、启用、切换当前、连通测试"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🔌 API 仓库管理")
        self.setMinimumSize(680, 440)
        lay = QVBoxLayout(self)
        tip = QLabel("支持 OpenAI 兼容接口。主 API 请求失败时会自动尝试下一个启用的 API。")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        # AI 总开关：关掉 = 不发任何 API 请求、纯本地模式（Key 和配置原样保留）
        self.ai_check = QCheckBox("启用 AI 对话（取消勾选 = 暂不使用任何 API，即使已填 Key 也走纯本地模式）")
        cfg0 = load_api_config()
        self.ai_check.setChecked(cfg0.get("ai_enabled", True))
        self.ai_check.toggled.connect(self._toggle_ai)
        lay.addWidget(self.ai_check)
        self.current_label = QLabel("")
        self.current_label.setStyleSheet("color:#9c6b45;font-size:12px;")
        lay.addWidget(self.current_label)
        self.list = QListWidget()
        self.list.setStyleSheet("QListWidget{background:#fdf6ec;color:#3d2b1f;font-size:13px;}")
        lay.addWidget(self.list)
        btns = QHBoxLayout()
        b_use = QPushButton("✅ 设为当前")
        b_edit = QPushButton("✏️ 编辑")
        b_add = QPushButton("➕ 添加")
        b_del = QPushButton("🗑 删除")
        b_test = QPushButton("📡 测试连通")
        b_use.clicked.connect(self.use_current)
        b_edit.clicked.connect(self.edit_item)
        b_add.clicked.connect(self.add_item)
        b_del.clicked.connect(self.delete_item)
        b_test.clicked.connect(self.test_item)
        for b in (b_use, b_edit, b_add, b_del, b_test):
            btns.addWidget(b)
        lay.addLayout(btns)
        self.test_label = QLabel("")
        self.test_label.setWordWrap(True)
        lay.addWidget(self.test_label)
        h = QHBoxLayout()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        h.addStretch(1)
        h.addWidget(close_btn)
        lay.addLayout(h)
        self.refresh()

    def _toggle_ai(self, on):
        """AI 总开关：写入 api_config.json 并即时刷新状态行。"""
        cfg = load_api_config()
        cfg["ai_enabled"] = bool(on)
        save_api_config(cfg)
        win = self.parent()
        if hasattr(win, "_update_mode_label"):
            win._update_mode_label()
        if hasattr(win, "say"):
            win.say("好～麟切换到{}模式啦{}".format(
                "AI 对话" if on else "纯本地", "♪" if on else "（不联网也陪你）"), split=False)
        self.refresh()

    def refresh(self):
        self.list.clear()
        cfg = load_api_config()
        cur = cfg.get("current", "")
        cur_name = ""
        for a in cfg.get("apis", []):
            if a.get("id") == cur:
                cur_name = a.get("name", "")
                break
        ai_on = cfg.get("ai_enabled", True)
        self.ai_check.blockSignals(True)
        self.ai_check.setChecked(ai_on)
        self.ai_check.blockSignals(False)
        self.current_label.setText("当前生效：{}（聊天优先使用，失败自动切换备用）{}".format(
            cur_name or "未设置", "" if ai_on else "｜⚠️ AI 总开关已关闭，当前为纯本地模式"))
        for a in cfg.get("apis", []):
            mark = "✅ 当前　" if a.get("id") == cur else "　　　　"
            state = "启用" if a.get("enabled") else "停用"
            keytip = "已填Key" if (a.get("key") or "").strip() else "无Key"
            QListWidgetItem("{}[{}] {} · {} · {} · {}".format(
                mark, state, a.get("name", ""), a.get("model", ""),
                a.get("base_url", ""), keytip), self.list)

    def _selected(self):
        row = self.list.currentRow()
        cfg = load_api_config()
        apis = cfg.get("apis", [])
        if 0 <= row < len(apis):
            return cfg, apis[row]
        return None, None

    def use_current(self):
        cfg, api = self._selected()
        if api is None:
            return
        cfg["current"] = api["id"]
        save_api_config(cfg)
        self.parent.say("已切换到 {}！".format(api.get("name", "")), split=False)
        self.refresh()

    def edit_item(self):
        cfg, api = self._selected()
        if api is None:
            return
        d = ApiEditDialog(api, self)
        if d.exec_() == QDialog.Accepted:
            api.update(d.result_data())
            save_api_config(cfg)
            self.refresh()

    def add_item(self):
        cfg = load_api_config()
        d = ApiEditDialog(None, self)
        if d.exec_() == QDialog.Accepted:
            data = d.result_data()
            if not data["name"] or not data["base_url"] or not data["model"]:
                QMessageBox.information(self, "提示", "名称、接口地址、模型都不能为空。")
                return
            data["id"] = "api_{}".format(int(time.time()))
            cfg.setdefault("apis", []).append(data)
            save_api_config(cfg)
            self.refresh()

    def delete_item(self):
        cfg, api = self._selected()
        if api is None:
            return
        if api["id"] == cfg.get("current"):
            QMessageBox.information(self, "提示", "当前正在使用的 API 不能删除，请先切换到其他 API。")
            return
        cfg["apis"] = [a for a in cfg.get("apis", []) if a["id"] != api["id"]]
        save_api_config(cfg)
        self.refresh()

    def test_item(self):
        cfg, api = self._selected()
        if api is None:
            return
        self.test_label.setText("正在测试 {} …".format(api.get("name", "")))
        QApplication.processEvents()
        ok, msg = test_api(api)
        head = "✅" if ok else "❌"
        self.test_label.setText("{} {}：{}".format(head, api.get("name", ""), msg))


# ==================== 五、主窗口 ====================
class SettingsDialog(QDialog):
    """设置面板：宠物尺寸 / 闲聊间隔 / 随机事件周期与权重（可视化配置，无需改文件）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ 设置面板")
        self.setMinimumSize(460, 440)
        self.parent = parent
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(16, 16, 16, 16)

        size_box = QFrame()
        size_box.setStyleSheet("QFrame{background:#f0e2cc;border-radius:10px;}")
        sl = QVBoxLayout(size_box)
        sl.setContentsMargins(12, 10, 12, 10)
        self.scale_slider = QSlider(Qt.Horizontal)
        self.scale_slider.setRange(40, 150)
        self.scale_slider.setValue(int(parent.scale * 100))
        self.scale_label = QLabel("宠物尺寸：{}%（重启后完全生效）".format(int(parent.scale * 100)))
        self.scale_slider.valueChanged.connect(
            lambda v: self.scale_label.setText("宠物尺寸：{}%（重启后完全生效）".format(v)))
        sl.addWidget(self.scale_label)
        sl.addWidget(self.scale_slider)
        lay.addWidget(size_box)

        form = QFormLayout()
        self.chat_spin = QSpinBox()
        self.chat_spin.setRange(5, 180)
        self.chat_spin.setSuffix(" 分钟")
        self.chat_spin.setValue(max(5, parent.idle_timeout // 60))
        self.evt_min_spin = QSpinBox()
        self.evt_min_spin.setRange(10, 180)
        self.evt_min_spin.setSuffix(" 分钟")
        self.evt_min_spin.setValue(max(10, parent.event_min // 60))
        self.evt_max_spin = QSpinBox()
        self.evt_max_spin.setRange(15, 240)
        self.evt_max_spin.setSuffix(" 分钟")
        self.evt_max_spin.setValue(max(15, parent.event_max // 60))
        self.wt_slider = QSlider(Qt.Horizontal)
        self.wt_slider.setRange(0, 100)
        self.wt_slider.setValue(parent.event_weight)
        self.wt_label = QLabel("触发概率：{}%".format(parent.event_weight))
        self.wt_slider.valueChanged.connect(lambda v: self.wt_label.setText("触发概率：{}%".format(v)))
        form.addRow("闲聊基础间隔", self.chat_spin)
        form.addRow("随机事件触发下限", self.evt_min_spin)
        form.addRow("随机事件触发上限", self.evt_max_spin)
        form.addRow("随机事件概率权重", self.wt_label)
        form.addRow("", self.wt_slider)
        lay.addLayout(form)

        # 置顶开关：默认置顶（桌宠不被别的窗口压住），关掉就按普通窗口排；
        # 无论开不开，打开面板时桌宠都会临时让位，所以不会挡住浏览器。
        self.top_check = QCheckBox("桌宠窗口保持置顶（不被其它窗口遮住）")
        self.top_check.setChecked(bool(getattr(parent, "_always_on_top", True)))
        lay.addWidget(self.top_check)

        # 聊天情绪总结（默认关）：开启后 AI 定期从聊天里总结情绪写进情绪记录
        self.mood_check = QCheckBox(
            "🤖 聊天总结情绪：AI 定期从聊天中总结你的情绪，自动记入情绪记录"
            "（攒够 4 句且间隔 1 小时才总结一次；记录仍只存本地，可照常删除）")
        self.mood_check.setChecked(bool(parent.settings.get("chat_mood_summary", False)))
        lay.addWidget(self.mood_check)

        # 分类夹切换方式：默认「等当前这首歌放完再换队列」，勾上就立即换
        self.switch_check = QCheckBox(
            "🎵 切换分类夹时立即更换播放队列（默认：当前这首歌放完再切）")
        self.switch_check.setChecked(bool(
            parent.settings.get("playlist_switch_immediate", False)))
        lay.addWidget(self.switch_check)

        # 聊天窗口流式输出速度（网络增量先进缓冲，按此节奏上屏）
        srow = QHBoxLayout()
        srow.addWidget(QLabel("💬 聊天窗口流式速度："))
        self.stream_combo = QComboBox()
        self._stream_items = [("慢速（一笔一画）", "slow"), ("适中（打字机感）", "medium"),
                              ("快速", "fast"), ("瞬间（网络多快就多快）", "instant")]
        for label, val in self._stream_items:
            self.stream_combo.addItem(label, val)
        cur = str(parent.settings.get("stream_speed", "medium"))
        idx = max(0, [v for _l, v in self._stream_items].index(cur)
                  if cur in [v for _l, v in self._stream_items] else 2)
        self.stream_combo.setCurrentIndex(idx)
        srow.addWidget(self.stream_combo)
        srow.addStretch(1)
        lay.addLayout(srow)

        h = QHBoxLayout()
        reset_btn = QPushButton("恢复默认")
        reset_btn.setStyleSheet("QPushButton{background:#e0c9a8;color:#3d2b1f;border:none;border-radius:8px;padding:6px 12px;}")
        reset_btn.clicked.connect(self.reset_defaults)
        save_btn = QPushButton("保存设置")
        save_btn.setStyleSheet("QPushButton{background:#9c6b45;color:#fff;border:none;border-radius:8px;padding:6px 14px;}")
        save_btn.clicked.connect(self.save_settings)
        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet("QPushButton{background:#bdb3a4;color:#3d2b1f;border:none;border-radius:8px;padding:6px 12px;}")
        close_btn.clicked.connect(self.accept)
        h.addWidget(reset_btn)
        h.addStretch(1)
        h.addWidget(save_btn)
        h.addWidget(close_btn)
        lay.addLayout(h)

        tip = QLabel("提示：闲聊与随机事件参数保存后立即生效；宠物尺寸将在下次启动时生效。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#8a6f4d;font-size:12px;")
        lay.addWidget(tip)

    def reset_defaults(self):
        self.scale_slider.setValue(100)
        self.chat_spin.setValue(20)
        self.evt_min_spin.setValue(30)
        self.evt_max_spin.setValue(120)
        self.wt_slider.setValue(60)
        self.top_check.setChecked(True)
        self.mood_check.setChecked(False)
        self.switch_check.setChecked(False)
        self.stream_combo.setCurrentIndex(2)

    def save_settings(self):
        win = self.parent
        evt_min = self.evt_min_spin.value() * 60
        evt_max = max(evt_min, self.evt_max_spin.value() * 60)
        win.settings["pet_scale"] = self.scale_slider.value()
        win.settings["chat_interval"] = self.chat_spin.value() * 60
        win.settings["event_min"] = evt_min // 60
        win.settings["event_max"] = evt_max // 60
        win.settings["event_weight"] = self.wt_slider.value()
        win.settings["chat_mood_summary"] = self.mood_check.isChecked()
        win.settings["playlist_switch_immediate"] = self.switch_check.isChecked()
        win.settings["stream_speed"] = self.stream_combo.currentData()
        save_json("settings.json", win.settings)
        win.idle_timeout = self.chat_spin.value() * 60
        win.event_min = evt_min
        win.event_max = evt_max
        win.event_freq = random.randint(evt_min, evt_max)
        win.event_weight = self.wt_slider.value()
        if hasattr(win, "set_always_on_top"):
            win.set_always_on_top(self.top_check.isChecked())
        win.say("设置保存好啦～", split=False)
        QMessageBox.information(self, "设置", "已保存！\n闲聊与随机事件参数已立即生效；\n宠物尺寸将在下次启动时生效。")


class MusicPlayerDialog(QDialog):
    """音乐播放器面板：封面/进度/控制按钮/音量 + 可搜索的播放队列。

    播放器本体（MusicFolderPlayer）挂在 PetWindow 上。窗口行为（2026-10-24 定稿）：
    标题栏右上角是系统原生的 — □ × 三键：— 最小化到任务栏（音乐继续，任务栏或
    菜单「🎵 音乐播放器」召回）；□ 最大化；× 关闭 = 停止播放。「⏹ 停止」/入睡/
    进游戏/退出也停。面板是非模态窗口，开着也不挡桌宠本体。内置曲目来自 exe，
    本地曲目来自 exe 旁 music/ 文件夹，两种都会列在队列里（带来源标签）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        win = parent
        self.player = getattr(win, "music_player", lambda: None)()
        self._seeking = False
        self.setWindowTitle("🎵 麟的音乐播放器")
        # 标题栏右上角显示原生 — □ × 三键（用户指定样式）：— 最小化（音乐继续）、
        # □ 最大化、× 关闭即停歌；Qt.Window 让面板有自己的任务栏项，最小化可从任务栏召回
        self.setWindowFlags(Qt.Window
                            | Qt.WindowMinimizeButtonHint
                            | Qt.WindowMaximizeButtonHint
                            | Qt.WindowCloseButtonHint)
        self.setMinimumSize(780, 560)
        # 背景图（bg.jpg 水彩雨巷，用户指定）：paintEvent 里铺满 + 暖色半透明罩；
        # 缺图时退回纯色，绝不因素材缺失打不开面板
        self._bg = QPixmap()
        try:
            _bg_path = resource_path("bg.jpg")
            if os.path.exists(_bg_path):
                self._bg.load(_bg_path)
        except Exception:
            self._bg = QPixmap()
        self._bg_scaled = None        # 按当前窗口尺寸缓存，resize 时置空
        self.setStyleSheet("QDialog{background:transparent;}"
                           "QListWidget{background:rgba(255,250,240,215);color:#3d2b1f;}"
                           "QLineEdit{background:rgba(255,250,240,215);color:#3d2b1f;"
                           "border:1px solid rgba(216,196,160,190);border-radius:4px;padding:2px 6px;}"
                           "QPushButton{background:rgba(240,226,200,225);color:#3d2b1f;"
                           "border:1px solid rgba(216,196,160,210);border-radius:4px;padding:4px 10px;}"
                           "QPushButton:hover{background:rgba(232,213,176,240);}"
                           "QCheckBox{color:#3d2b1f;}")

        # ---- 外层：左侧「我的分类夹」导航 + 右侧原有播放界面（需求方案一） ----
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        body = QHBoxLayout()
        body.setSpacing(8)
        outer.addLayout(body, 1)
        self._build_nav()
        body.addWidget(self._nav_box)
        right_host = QWidget()
        lay = QVBoxLayout(right_host)
        lay.setContentsMargins(0, 0, 0, 0)
        body.addWidget(right_host, 1)

        # 当前选中的分类夹 + 歌单存储（每次打开面板重新读盘，别的地方改了也能看见）
        self._cur_scope = SYS_ALL      # 当前队列实际所属的分类夹
        self._want_scope = None        # 放歌中点了别的分类夹 → 等这首放完再切
        self._switching = False
        self._store = PlaylistStore()
        self._store.ensure_favorites()   # 老版本升级上来也补一个「我喜欢的音乐」
        self._trash = get_store()        # 回收站（软删除缓存，数据在 playlist/trash.json）
        self._key_map = None            # 歌单旧键 -> 当前曲库键 的解析缓存（每次刷新作废）
        self._fav_cache = None          # 收藏夹键集合缓存

        # ---- 顶部：封面 + 曲名/来源 ----
        top = QHBoxLayout()
        self.cover_lab = QLabel()
        self.cover_lab.setFixedSize(120, 120)
        self.cover_lab.setAlignment(Qt.AlignCenter)
        self.cover_lab.setStyleSheet("background:#f0e2c8;border:1px solid #d8c4a0;border-radius:6px;")
        self.cover_lab.setScaledContents(True)
        top.addWidget(self.cover_lab)
        info = QVBoxLayout()
        self.title_lab = QLabel("还没有开始播放")
        self.title_lab.setWordWrap(True)
        self.title_lab.setStyleSheet("font-size:16px;font-weight:bold;color:#5a3d2b;")
        self.sub_lab = QLabel("")
        self.sub_lab.setStyleSheet("color:#8a6f4d;font-size:12px;")
        info.addWidget(self.title_lab)
        info.addWidget(self.sub_lab)
        info.addStretch(1)
        top.addLayout(info, 1)
        lay.addLayout(top)

        # ---- 进度条 ----
        prog = QHBoxLayout()
        self.t_cur = QLabel("00:00")
        self.t_total = QLabel("00:00")
        self.progress = QSlider(Qt.Horizontal)
        self.progress.setRange(0, 0)
        prog.addWidget(self.t_cur)
        prog.addWidget(self.progress, 1)
        prog.addWidget(self.t_total)
        lay.addLayout(prog)

        # ---- 控制按钮行 ----
        ctrl = QHBoxLayout()
        # 播放模式按钮：左键点一下轮流切；右键弹菜单直接选四种模式之一
        self.mode_btn = QPushButton("➡️ 顺序播放")
        self.mode_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        self.prev_btn = QPushButton("⏮")
        self.play_btn = QPushButton("▶ 播放")
        self.next_btn = QPushButton("⏭")
        self.stop_btn = QPushButton("⏹ 停止")
        # 喜欢（收藏）按钮：和网易云一样，当前这首点心心就进「我喜欢的音乐」
        self.fav_btn = QPushButton("♡ 喜欢")
        self.fav_btn.setToolTip("把当前这首收进「{}」，再点一下取消".format(FAV_NAME))
        for b in (self.mode_btn, self.prev_btn, self.play_btn, self.next_btn,
                  self.stop_btn, self.fav_btn):
            b.setFocusPolicy(Qt.NoFocus)
        ctrl.addWidget(self.mode_btn)
        ctrl.addStretch(1)
        ctrl.addWidget(self.prev_btn)
        ctrl.addWidget(self.play_btn)
        ctrl.addWidget(self.next_btn)
        ctrl.addWidget(self.fav_btn)
        ctrl.addStretch(1)
        ctrl.addWidget(self.stop_btn)
        lay.addLayout(ctrl)

        # ---- 音量行 ----
        vol = QHBoxLayout()
        self.mute_chk = QCheckBox("静音")
        vol_lab = QLabel("🔊")
        self.vol_slider = QSlider(Qt.Horizontal)
        self.vol_slider.setRange(0, 100)
        vol.addWidget(self.mute_chk)
        vol.addWidget(vol_lab)
        vol.addWidget(self.vol_slider, 1)
        lay.addLayout(vol)

        # ---- 队列 ----
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 搜索队列…（输入关键字过滤）")
        self.search.setFocusPolicy(Qt.ClickFocus)
        lay.addWidget(self.search)
        self.queue = QListWidget()
        # 批量选中（多选后可右键「添加到分类夹」），自建分类夹里还能拖拽调序
        self.queue.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.queue.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue.customContextMenuRequested.connect(self._queue_menu)
        self.queue.model().rowsMoved.connect(self._on_queue_reordered)
        self.queue.itemDoubleClicked.connect(self._on_queue_dclick)
        self.queue.itemClicked.connect(self._on_queue_clicked)   # 点行首的小心心=收藏
        lay.addWidget(self.queue, 1)

        # ---- 底部按钮 ----
        bottom = QHBoxLayout()
        imp_btn = QPushButton("📥 导入音乐")
        imp_btn.setToolTip("可以选音频文件，也可以直接选 zip 压缩包（自动解压）；"
                           "或者把音频 / zip / 文件夹拖进面板")
        open_btn = QPushButton("📂 打开音乐文件夹")
        self.rm_btn = QPushButton("➖ 移除选中")
        self.restore_btn = QPushButton("♻️ 恢复全部")
        # 回收站专用：真的删（导入的歌连本地文件一起删），只在回收站里露出来
        self.purge_btn = QPushButton("🗑 彻底删除")
        self.purge_btn.setToolTip("真的删掉：导入的歌连本地文件一起删，删了就回不来了")
        self.purge_btn.setVisible(False)
        for b in (imp_btn, open_btn, self.rm_btn, self.restore_btn, self.purge_btn):
            b.setFocusPolicy(Qt.NoFocus)
        bottom.addWidget(imp_btn)
        bottom.addWidget(open_btn)
        bottom.addWidget(self.rm_btn)
        bottom.addWidget(self.restore_btn)
        bottom.addWidget(self.purge_btn)
        bottom.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        lay.addLayout(bottom)

        # 拖拽导入：把音频 / zip 压缩包 / 文件夹直接拖进面板（2026-11-06）
        self.setAcceptDrops(True)
        tip = QLabel("内置歌曲在 exe 旁 music\\原声 曲库包里（升级只换 exe，曲库不用重下）；"
                     "导入的音频 / zip 压缩包 / 文件夹直接拖进面板就会自动收进 exe 旁 music 文件夹，升级也不丢。"
                     "左侧分类夹存在 exe 旁 playlist 文件夹里，换版本也不丢。"
                     "移出的歌先进「♻ 回收站」，在那儿点「🗑 彻底删除」才是真删。"
                     "标题栏「—」最小化（音乐继续，任务栏召回）；「×」或「关闭」停止播放。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#8a6f4d;font-size:11px;")
        lay.addWidget(tip)

        # ---- 信号 ----
        self.mode_btn.clicked.connect(self._cycle_mode)
        self.mode_btn.customContextMenuRequested.connect(self._show_mode_menu)
        self.prev_btn.clicked.connect(lambda: self._guard(self.player.previous))
        self.play_btn.clicked.connect(lambda: self._guard(self.player.toggle))
        self.next_btn.clicked.connect(lambda: self._guard(self.player.next))
        self.stop_btn.clicked.connect(self._stop_music)
        self.fav_btn.clicked.connect(self._toggle_fav_current)
        self.progress.sliderPressed.connect(self._seek_start)
        self.progress.sliderMoved.connect(self._seek_move)
        self.progress.sliderReleased.connect(self._seek_apply)
        self.vol_slider.valueChanged.connect(self._volume_changed)
        self.vol_slider.sliderReleased.connect(self._volume_saved)
        self.mute_chk.toggled.connect(self._mute_changed)
        self.search.textChanged.connect(self._apply_filter)
        imp_btn.clicked.connect(self._import_music)
        open_btn.clicked.connect(self._open_folder)
        self.rm_btn.clicked.connect(self._remove_selected)
        self.restore_btn.clicked.connect(self._restore_all)
        self.purge_btn.clicked.connect(self._purge_selected)

        self._connect_player()
        self._load_cover()
        self._refresh_all()

    # ---- 播放器信号接入 --------------------------------------------------
    def _connect_player(self):
        if self.player is None:
            return
        self.player.trackChanged.connect(self._on_track)
        self.player.stateChanged.connect(self._on_state)
        self.player.modeChanged.connect(self._on_mode)
        self.player.positionChanged.connect(self._on_position)
        self.player.queueChanged.connect(self._refresh_all)
        self.player.scopeChanged.connect(self._on_scope_changed)

    def _disconnect_player(self):
        """关闭面板时摘掉所有信号连接（播放器还要长期用，别让旧面板吃信号）。"""
        if self.player is None:
            return
        try:
            self.player.trackChanged.disconnect(self._on_track)
            self.player.stateChanged.disconnect(self._on_state)
            self.player.modeChanged.disconnect(self._on_mode)
            self.player.positionChanged.disconnect(self._on_position)
            self.player.queueChanged.disconnect(self._refresh_all)
            self.player.scopeChanged.disconnect(self._on_scope_changed)
        except Exception:
            pass

    def changeEvent(self, event):
        """最小化到任务栏时让麟说一句（音乐继续放）。"""
        super().changeEvent(event)
        try:
            if self.windowState() & Qt.WindowMinimized:
                win = self.parent()
                if hasattr(win, "say"):
                    win.say("面板最小化啦，音乐继续放♪任务栏或菜单里都能叫回来", split=False)
        except Exception:
            pass

    def paintEvent(self, event):
        """画背景：bg.jpg 水彩雨巷按比例铺满窗口，再叠一层暖奶油半透明罩，
        保证深棕文字在各处都可读；bg.jpg 缺失时退回纯色。"""
        p = QPainter(self)
        try:
            rect = self.rect()
            if self._bg is not None and not self._bg.isNull():
                w, h = rect.width(), rect.height()
                if (self._bg_scaled is None
                        or self._bg_scaled.width() != w
                        or self._bg_scaled.height() != h):
                    self._bg_scaled = self._bg.scaled(
                        w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                p.drawPixmap((w - self._bg_scaled.width()) // 2,
                             (h - self._bg_scaled.height()) // 2,
                             self._bg_scaled)
                p.fillRect(rect, QColor(253, 246, 236, 150))
            else:
                p.fillRect(rect, QColor("#fdf6ec"))
        finally:
            p.end()

    def resizeEvent(self, event):
        self._bg_scaled = None           # 尺寸变了，背景缩放缓存作废（最大化/还原都会进来）
        super().resizeEvent(event)

    def done(self, r):
        """accept/reject/标题栏 × 都汇聚到这里：关面板 = 停止播放（最小化才继续放）。"""
        self._disconnect_player()
        try:
            win = self.parent()
            if hasattr(win, "stop_music"):
                win.stop_music()
        except Exception:
            pass
        super().done(r)

    def _guard(self, fn):
        try:
            fn()
        except Exception:
            pass

    # ---- 状态刷新 --------------------------------------------------------
    def _refresh_all(self):
        self._key_map = None
        self._fav_cache = None
        self._fill_nav()
        self._fill_queue()
        self._update_search_hint()
        self._update_scope_ui()
        if self.player is None:
            self.title_lab.setText("这台电脑放不了歌（缺 QtMultimedia）")
            self.sub_lab.setText("麟会改用八音盒陪你～")
            return
        self._on_track(*self._current_tuple())
        self._on_state(self.player.is_playing())
        self._on_mode(self.player.mode_key())
        self.vol_slider.blockSignals(True)
        self.vol_slider.setValue(self.player.volume())
        self.vol_slider.blockSignals(False)
        self.mute_chk.setChecked(self.player.is_muted())

    def _current_tuple(self):
        try:
            cur = self.player.current_track()
            if cur:
                return cur["index"], cur["title"], cur["source"]
        except Exception:
            pass
        return -1, "", ""

    def _on_track(self, idx, title, source):
        if title:
            self.title_lab.setText("《{}》".format(title))
            self.sub_lab.setText("第 {} / {} 首 · 来源：{}".format(
                idx + 1, len(self.player.tracks()), source))
        else:
            self.title_lab.setText("还没有开始播放")
            self.sub_lab.setText("")
        self._highlight_current()
        self._update_fav_btn()

    def _on_state(self, playing):
        self.play_btn.setText("⏸ 暂停" if playing else "▶ 播放")

    def _on_mode(self, key):
        name = self.player.__class__.mode_name(key) if self.player is not None else key
        icon = self.MODE_ICONS.get(key, "")
        self.mode_btn.setText((icon + " " if icon else "") + name)
        self.mode_btn.setToolTip("点一下轮流切换（顺序 → 列表循环 → 单曲循环 → 随机）\n"
                                 "右键直接选某个模式")

    def _on_position(self, pos, dur):
        if dur > 0:
            self.progress.setRange(0, dur)
        self.t_total.setText(fmt_ms(dur))
        if not self._seeking:
            self.progress.blockSignals(True)
            self.progress.setValue(pos)
            self.progress.blockSignals(False)
        self.t_cur.setText(fmt_ms(pos))

    # ---- 喜欢（收藏夹）/ 键解析 ------------------------------------------
    def _key_maps(self):
        """当前曲库的两张表：完整键 -> 键、曲名 -> 键（用于兼容老歌单）。"""
        if self._key_map is not None:
            return self._key_map
        by_key, by_title = {}, {}
        if self.player is not None:
            for _p, title, source in self.player.library_tracks():
                k = track_key(source, title)
                by_key[k] = k
                by_title.setdefault(title, k)
        self._key_map = (by_key, by_title)
        return self._key_map

    def _resolved_key(self, key):
        """把歌单里存的旧键映射成当前曲库的键。

        上一版内置曲被错标成「本地」，老歌单/收藏里存的是 "本地|曲名"；
        现在来源改对了，直接比键会全部失配（心心不亮、歌单里的歌全消失）。
        这里按曲名兜底，让老数据无损迁移。
        """
        if not key:
            return key
        by_key, by_title = self._key_maps()
        if key in by_key:
            return key
        return by_title.get(split_key(key)[1], key)

    def _fav_keys(self):
        """收藏夹里的歌（来源已按当前曲库纠正），带缓存避免每行都重算。"""
        if self._fav_cache is None:
            self._fav_cache = set(self._resolved_key(k)
                                  for k in self._store.keys(FAV_ID))
        return self._fav_cache

    def _is_fav_key(self, key):
        return bool(key) and self._resolved_key(key) in self._fav_keys()

    def _row_text(self, label, key, is_cur=False):
        """行文本 = 心心 + 当前曲标记 ♪ + 原标签；心心点一下就收藏/取消。"""
        heart = "♥" if self._is_fav_key(key) else "♡"
        return "{} {}{}".format(heart, "♪ " if is_cur else "", label)

    def _refresh_marks(self, keys=None):
        """只重画心心/当前曲标记，**不动**选中条和滚动位置。

        点♥只是给某一首做个标记，不该改变用户正在看的位置；真正需要「跟着
        当前曲走」的是切歌（_on_track → _highlight_current）。
        """
        targets = None
        if keys:
            targets = set(self._resolved_key(k) for k in keys if k)
        cur_row = self._current_row()
        for i in range(self.queue.count()):
            it = self.queue.item(i)
            label = it.data(Qt.UserRole)
            if label is None:
                continue        # 空分类的占位行
            k = it.data(Qt.UserRole + 2)
            if targets is not None and self._resolved_key(k) not in targets:
                continue
            it.setText(self._row_text(label, k, i == cur_row))

    def _stored_keys(self, pid, keys):
        """把列表里显示的键换算成歌单里实际存的键。

        上一版把内置曲错标成「本地」，老歌单里存的是 "本地|曲名"，界面按当前
        曲库解析后显示的是 "内置|曲名"。直接拿显示键去 remove_tracks 会比不中，
        表现就是「分类夹里的歌移除不掉」（提示移出 0 首、列表不变）。这里按解析
        后的键比对，新旧键都能删掉。
        """
        want = set(k for k in (keys or []) if k)
        if not want:
            return []
        resolved = set(self._resolved_key(k) for k in want)
        out = []
        for k in self._store.keys(pid):
            if k in want or self._resolved_key(k) in resolved:
                out.append(k)
        return out

    def _update_fav_btn(self):
        cur = self._current_tuple()
        on = bool(cur[1]) and self._is_fav_key(track_key(cur[2], cur[1]))
        self.fav_btn.setText("♥ 已喜欢" if on else "♡ 喜欢")

    def _current_row(self):
        """当前播放曲目在面板列表里的行号；列表不是它的队列（延迟切换中）返回 -1。"""
        idx, title, source = self._current_tuple()
        if idx < 0 or not title or self.player is None:
            return -1
        cur_key = track_key(source, title)
        for i in range(self.queue.count()):
            it = self.queue.item(i)
            if it is not None and it.data(Qt.UserRole + 2) == cur_key:
                return i
        return -1

    def _highlight_current(self):
        cur_row = self._current_row()
        for i in range(self.queue.count()):
            it = self.queue.item(i)
            label = it.data(Qt.UserRole)
            if label is None:
                continue        # 空分类的占位行没有 data，别把提示文字清空
            it.setText(self._row_text(label, it.data(Qt.UserRole + 2), i == cur_row))
            font = it.font()
            font.setBold(i == cur_row)
            it.setFont(font)
        self._follow_current(cur_row)

    def _follow_current(self, cur_idx):
        """让队列的灰色选中条和滚动位置跟着正在放的那首走。

        以前只给当前曲目加 "♪" 并加粗，选中条还停在上一次双击/点过的那首，
        而当前曲目往往在可视区外——用户看到的就是「高亮的那首」和标题对不上，
        以为还在放上一首。这里把选中项和滚动一起挪过去。
        被搜索关键字过滤隐藏时不硬跳，免得把用户正在看的过滤结果顶掉。
        """
        try:
            if cur_idx < 0 or cur_idx >= self.queue.count():
                return
            it = self.queue.item(cur_idx)
            if it is None or it.isHidden():
                return
            self.queue.blockSignals(True)
            try:
                self.queue.setCurrentItem(it, QItemSelectionModel.ClearAndSelect)
            finally:
                self.queue.blockSignals(False)
            self.queue.scrollToItem(it, QAbstractItemView.EnsureVisible)
        except Exception:
            pass

    def _view_tracks(self):
        """面板列表要显示的曲目。

        延迟切换（放歌中点了别的分类夹）时，列表先切到目标分类夹——用户点了
        分类夹就该马上看见里面的歌；实际播放队列还是旧的，等这首放完才换。
        以前这里两份都留在旧分类，用户看到的就是「点了分类夹没反应」。
        """
        if self.player is None:
            return []
        if not self._want_scope and self._cur_scope == TRASH_SCOPE:
            return self._scope_tracks(TRASH_SCOPE)   # 回收站：只读展示，不动播放队列
        sid = self._want_scope
        if sid and self.player.pending_scope() == sid:
            return self._scope_tracks(sid)
        return self.player.tracks()

    def _fill_queue(self):
        self.queue.blockSignals(True)
        try:
            self.queue.clear()
        finally:
            self.queue.blockSignals(False)
        if self.player is None:
            return
        # 只有自建分类夹里才允许拖动调序（系统分类是过滤视图，顺序无意义）
        self.queue.setDragDropMode(
            QAbstractItemView.InternalMove if self._is_user_scope()
            else QAbstractItemView.NoDragDrop)
        tracks = self._view_tracks()
        if not tracks:
            text = ("回收站是空的～想删的歌先在「全部音乐」里选中，点「➖ 移除选中」"
                    if self._cur_scope == TRASH_SCOPE
                    else "该分类暂无音乐，选中歌曲右键即可添加")
            it = QListWidgetItem(text)
            it.setFlags(Qt.NoItemFlags)
            it.setForeground(QColor("#a8845c"))
            self.queue.addItem(it)
            self._apply_filter(self.search.text())
            return
        for i, (path, title, source) in enumerate(tracks):
            label = "[{}] {}".format(source, title)
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, label)          # 用来搜索/显示
            it.setData(Qt.UserRole + 1, i)          # 队列下标
            it.setData(Qt.UserRole + 2, track_key(source, title))   # 歌单里的稳定键
            it.setToolTip(path)
            self.queue.addItem(it)
        self._highlight_current()
        self._apply_filter(self.search.text())

    def _apply_filter(self, text):
        """只在当前分类夹范围内过滤（需求 3.3：分类夹内搜索）。"""
        text = (text or "").strip().lower()
        for i in range(self.queue.count()):
            it = self.queue.item(i)
            label = it.data(Qt.UserRole) or it.text() or ""
            it.setHidden(bool(text) and text not in str(label).lower())
        self._follow_current(self._current_row())

    # ---- 交互动作 --------------------------------------------------------
    def _cycle_mode(self):
        if self.player is None:
            return
        key = self.player.cycle_mode()
        win = self.parent()
        if hasattr(win, "settings"):
            win.settings["music_playback"] = key
            save_json("settings.json", win.settings)

    def _show_mode_menu(self, pos):
        """右键模式按钮：直接选四种播放模式（当前模式打勾），不用一下一下轮着点。

        用户反馈「播放器没有随机播放功能」——其实点按钮第四下就是随机，
        但入口太隐蔽，所以补一个一眼能看到的右键菜单。
        """
        if self.player is None:
            return
        cur = self.player.mode_key()
        menu = QMenu(self)
        for key, name in MODES:
            icon = self.MODE_ICONS.get(key, "")
            act = menu.addAction((icon + " " if icon else "") + name)
            act.setCheckable(True)
            act.setChecked(key == cur)
            # 默认参数把 key 绑进 lambda，否则循环里所有菜单项都会指向最后一个模式
            act.triggered.connect(lambda checked=False, k=key: self._set_mode(k))
        menu.exec_(self.mode_btn.mapToGlobal(pos))

    def _set_mode(self, key):
        """直接切到指定模式（右键菜单用）：和点按钮一样会记住到 settings.json。"""
        if self.player is None:
            return
        self.player.set_mode(key)
        win = self.parent()
        if hasattr(win, "settings"):
            win.settings["music_playback"] = key
            save_json("settings.json", win.settings)

    def _seek_start(self):
        self._seeking = True

    def _seek_move(self, pos):
        self.t_cur.setText(fmt_ms(pos))

    def _seek_apply(self):
        self._seeking = False
        self._guard(lambda: self.player.seek_ms(self.progress.value()))

    def _volume_changed(self, v):
        if self.player is not None:
            self._guard(lambda: self.player.set_volume(v))

    def _volume_saved(self):
        win = self.parent()
        if self.player is not None and hasattr(win, "settings"):
            win.settings["music_volume"] = self.player.volume()
            save_json("settings.json", win.settings)

    def _mute_changed(self, on):
        if self.player is not None:
            self._guard(lambda: self.player.mute(on))

    def _stop_music(self):
        win = self.parent()
        if hasattr(win, "stop_music"):
            win.stop_music()
        self._on_state(False)

    def _on_queue_dclick(self, item):
        if self.player is None or item is None:
            return
        key = item.data(Qt.UserRole + 2)
        if not key:
            return                      # 空分类的占位行
        if self._cur_scope == TRASH_SCOPE and not self._want_scope:
            self._say("回收站里的歌先恢复才能听哦～右键「♻ 恢复」或点「♻ 恢复选中」")
            return
        pending = self.player.pending_scope()
        if pending and self._want_scope == pending:
            # 正在显示的是「等这首放完才切」的目标分类：双击 = 立刻切过去放
            self._select_scope(pending, immediate=True)
        want = self._resolved_key(key)
        for i, (_p, title, source) in enumerate(self.player.tracks()):
            if track_key(source, title) == want:
                self._guard(lambda i=i: self.player.play_at(i))
                return

    def _on_queue_clicked(self, item):
        """点行首的 ♥/♡ 就收藏/取消收藏（点别处照常选歌，不打扰）。"""
        if self.player is None or item is None:
            return
        key = item.data(Qt.UserRole + 2)
        if not key:
            return
        if self._cur_scope == TRASH_SCOPE and not self._want_scope:
            return                      # 回收站里别给删掉的歌点心心
        try:
            rect = self.queue.visualItemRect(item)
            x = self.queue.viewport().mapFromGlobal(QCursor.pos()).x()
        except Exception:
            return
        if rect.left() <= x <= rect.left() + 22:
            self._toggle_fav([key])

    def _toggle_fav_current(self):
        cur = self._current_tuple()
        if cur[0] < 0 or not cur[1]:
            self._say("先放一首歌，再点心心就能收藏啦～")
            return
        self._toggle_fav([track_key(cur[2], cur[1])])

    def _toggle_fav(self, keys):
        """收藏/取消收藏若干首（存进固定分类夹「我喜欢的音乐」，不动文件）。"""
        keys = [self._resolved_key(k) for k in (keys or []) if k]
        if not keys:
            return
        d = self._store.ensure_favorites()
        have = set(d["music_list"])
        missing = [k for k in keys if k not in have]
        if missing:
            n = self._store.add_tracks(FAV_ID, missing)
            msg = "收进「{}」啦（{} 首）♥".format(FAV_NAME, n)
        else:
            drop = set(keys)
            d["music_list"] = [k for k in d["music_list"]
                               if self._resolved_key(k) not in drop]
            self._store.save()
            msg = "从「{}」拿掉啦（{} 首），文件还在".format(FAV_NAME, len(keys))
        self._fav_cache = None
        self._fill_nav()
        if self._cur_scope == FAV_ID:
            # 在收藏夹里点♥ = 把这首拿出去，列表少一行，只能重建
            self._select_scope(FAV_ID)
        else:
            # 只重画这几行的心心，不动选中条和滚动位置。
            # 以前这里调 _highlight_current()，它会 setCurrentItem + scrollToItem
            # 到正在播放的那首：用户给列表中间的歌点个心，视图立刻被拽走，
            # 看起来就像「点喜欢就跳转到正在播放的音乐」。
            self._refresh_marks(keys)
            self._update_fav_btn()
        self._say(msg)

    def _import_music(self):
        """「📥 导入音乐」：音频文件、zip 压缩包都能选（拖拽见 _accept_import）。"""
        audio = " ".join("*" + e for e in AUDIO_EXTS)
        zips = " ".join("*" + e for e in ZIP_EXTS)
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要导入的音乐（音频或 zip 压缩包）", "",
            "音频或压缩包 ({} {});;音频文件 ({});;压缩包 ({});;所有文件 (*.*)".format(
                audio, zips, audio, zips))
        if not paths:
            return
        self._accept_import(paths)

    def _accept_import(self, paths):
        """导入的真正入口：音频 / zip 压缩包 / 文件夹都走这里，导入后给一句人话。

        zip 会在 music 文件夹里自动解压（面板不用管目录结构），文件夹会递归
        找里面的音频；结果摘要来自 player.last_import（files/zips/folders/skipped）。
        """
        if not paths or self.player is None:
            return
        try:
            n = self.player.add_files(paths)
        except Exception as e:                    # 坏包/没权限都别让面板崩
            print("[dialogs] 导入音乐失败:", e)
            return
        stat = getattr(self.player, "last_import", None) or {}
        zips = int(stat.get("zips") or 0)
        folders = int(stat.get("folders") or 0)
        # 导入会重扫曲库并退回全库范围，把当前分类夹重新套回去
        try:
            self._fill_nav()
            self._select_scope(self._cur_scope)
        except Exception as e:
            print("[dialogs] 导入后刷新列表失败:", e)
        win = self.parent()
        say = getattr(win, "say", None)
        if not callable(say):
            return
        if n:
            if zips:
                msg = "麟从压缩包里解出 {} 首歌，收进曲库啦～马上就能听！".format(n)
            elif folders:
                msg = "麟把文件夹里的 {} 首歌收进曲库啦～马上就能听！".format(n)
            else:
                msg = "麟把 {} 首歌收进曲库啦～马上就能听！".format(n)
        elif zips:
            msg = "这个压缩包里麟没找到能听的音乐（里面要有 mp3 / flac / wav 这些哦）"
        else:
            msg = "唔…这些文件麟收不进来（要音频文件、zip 压缩包或者文件夹哦）"
        try:
            say(msg, split=False)
        except TypeError:
            say(msg)

    @staticmethod
    def _drop_paths(ev):
        """从拖拽事件里取出本地路径列表；不是本地文件（网页图片等）一律不要。"""
        try:
            md = ev.mimeData()
            if md is None or not md.hasUrls():
                return []
        except Exception:
            return []
        out = []
        for u in md.urls():
            try:
                if not u.isLocalFile():
                    continue
                p = u.toLocalFile()
                if p and os.path.exists(p):
                    out.append(p)
            except Exception:
                continue
        return out

    def dragEnterEvent(self, ev):
        """把音频 / zip / 文件夹拖进面板就能导入（2026-11-06 加的）。"""
        if self._drop_paths(ev):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev):
        paths = self._drop_paths(ev)
        if not paths:
            ev.ignore()
            return
        ev.acceptProposedAction()
        self._accept_import(paths)

    def _open_folder(self):
        try:
            d = external_dir()
            os.makedirs(d, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(d)
        except Exception as e:
            QMessageBox.information(self, "打不开", "没能打开音乐文件夹：{}".format(e))

    def _remove_selected(self):
        """移除选中。

        - 系统分类（全部音乐/内置原声/我的导入）：丢进「♻ 回收站」，能恢复；
          真正的删除要去回收站点「🗑 彻底删除」；
        - 回收站里：等于「♻ 恢复选中」（把歌拿回曲库）；
        - 自建分类夹：只解除收纳（不删文件、不进回收站、不影响别的分类）。
        """
        if self.player is None:
            return
        rows = sorted(set(self.queue.row(i) for i in self.queue.selectedItems()))
        if not rows:
            row = self.queue.currentRow()
            rows = [row] if row >= 0 else []
        if not rows:
            QMessageBox.information(self, "提示", "先在列表里点选要移出的歌～")
            return
        keys, idxs = [], []
        for r in rows:
            it = self.queue.item(r)
            if it is None:
                continue
            k = it.data(Qt.UserRole + 2)
            i = it.data(Qt.UserRole + 1)
            if k:
                keys.append(k)
            if isinstance(i, int) and i >= 0:
                idxs.append(i)
        if self._cur_scope == TRASH_SCOPE and not self._want_scope:
            self._restore_trash(keys)
            return
        if self._is_user_scope():
            pid = self._cur_scope
            name = self._scope_label(pid)
            drop = self._stored_keys(pid, keys)
            n = self._store.remove_tracks(pid, drop) if drop else 0
            self._fill_nav()
            self._select_scope(pid)
            if n:
                self._say("从「{}」移出 {} 首～文件还在，其他分类也没动".format(name, n))
            else:
                self._say("这几首不在「{}」里（可能已经移出过了）".format(name))
            return
        # 系统分类：移出曲库 = 先收进回收站（还能反悔），不再一移除就消失
        view = self._view_tracks()
        entries = []
        for i in sorted(set(idxs)):
            if not (0 <= i < len(view)):
                continue
            path, title, source = view[i]
            k = track_key(source, title)
            entries.append({"source": source, "title": title, "path": path,
                            "from_playlists": self._playlists_with(k)})
        if not entries:
            return
        moved = self._trash.add(entries)
        self._drop_from_playlists(entries)     # 曲库都移出去了，分类夹里也别留着
        self._fill_nav()
        self._select_scope(self._cur_scope, immediate=True)
        self._say("{} 首收进「♻ 回收站」啦～想反悔就点左边回收站恢复".format(moved))

    def _restore_all(self):
        if self.player is None:
            return
        if self._cur_scope == TRASH_SCOPE and not self._want_scope:
            self._restore_all_trash()
            return
        keep = ""
        cur = self._current_tuple()
        if cur[0] >= 0:
            keep = cur[1]
        self._guard(lambda: self.player.restore_all(keep))
        # 重扫后把当前分类夹范围重新套上（自建歌单的文件可能变了）
        self._select_scope(self._cur_scope)
        self._say("麟把曲库重新翻了一遍，全部 {} 首都回来啦～".format(
            len(self.player.library_tracks())))

    # ---- 回收站（软删除缓存） ---------------------------------------------
    def _update_scope_ui(self):
        """底部按钮随当前分类变：回收站里给「恢复/彻底删除」，别处是「移除/恢复全部」。"""
        in_trash = self._cur_scope == TRASH_SCOPE and not self._want_scope
        self.rm_btn.setText("♻ 恢复选中" if in_trash else "➖ 移除选中")
        self.rm_btn.setToolTip("把选中的歌放回曲库" if in_trash
                               else "移出当前列表；系统分类里移出的歌会先进回收站")
        self.restore_btn.setText("♻ 全部恢复" if in_trash else "♻️ 恢复全部")
        self.purge_btn.setVisible(in_trash)

    def _playlists_with(self, key):
        """这首现在被哪些自建分类夹收着（进回收站时记下来，恢复时放回原处）。"""
        rk = self._resolved_key(key)
        out = []
        for d in self._store.playlists:
            for k in d["music_list"]:
                if self._resolved_key(k) == rk:
                    out.append(d["playlist_id"])
                    break
        return out

    def _drop_from_playlists(self, entries):
        """把这几首从所有分类夹里摘掉（只改收纳关系，绝不动文件）。"""
        want = set(self._resolved_key(track_key(e["source"], e["title"]))
                   for e in entries)
        for d in list(self._store.playlists):
            drop = [k for k in d["music_list"] if self._resolved_key(k) in want]
            if drop:
                self._store.remove_tracks(d["playlist_id"], drop)

    def _restore_trash(self, keys):
        """从回收站恢复：文件从头到尾没动过，只是放回曲库（分类夹也放回原处）。"""
        keys = [k for k in (keys or []) if k]
        if not keys:
            self._say("先在回收站里选中要恢复的歌～")
            return
        items = self._trash.restore(keys)
        if not items:
            return
        for d in items:
            for pid in d.get("from_playlists") or []:
                if self._store.by_id(pid):
                    self._store.add_tracks(pid, [d["key"]])
        # 启动扫描时这些歌被回收站滤掉了，补一次轻量重扫让它们马上回到曲库
        # （不打断正在放的音乐）
        self._guard(self.player.refresh_library)
        self._fill_nav()
        self._select_scope(self._cur_scope)
        self._say("{} 首回到曲库啦～".format(len(items)))

    def _restore_all_trash(self):
        self._restore_trash(self._trash.keys())

    def _purge_selected(self):
        if self.player is None:
            return
        keys = []
        for it in self.queue.selectedItems():
            k = it.data(Qt.UserRole + 2)
            if k and k not in keys:
                keys.append(k)
        if not keys:
            row = self.queue.currentRow()
            it = self.queue.item(row) if row >= 0 else None
            k = it.data(Qt.UserRole + 2) if it is not None else None
            if k:
                keys.append(k)
        if not keys:
            self._say("先在回收站里选中要彻底删掉的歌～")
            return
        self._purge_trash(keys)

    def _purge_trash(self, keys):
        """彻底删除：导入的歌连本地文件一起删；内置原声属于曲库包，不替用户删，
        只记进屏蔽表（永远不再出现；文件一个都不动，想找回来得清掉 playlist/trash.json
        里的屏蔽记录——换台电脑则是天然恢复）。回收站里点
        「彻底删除」就是最后一道确认，不再弹二次确认框（用户要求：都确认删除
        了就是真删，不用再问）。"""
        keys = [k for k in (keys or []) if k]
        if not keys:
            return
        want = set(keys)
        items = [d for d in self._trash.items if d["key"] in want]
        if not items:
            return
        try:
            ext_root = os.path.abspath(external_dir())
        except Exception:
            ext_root = ""
        killed, builtin_n, failed, blocked = 0, 0, 0, []
        for d in items:
            if d.get("source") == "内置":
                builtin_n += 1
                blocked.append(d["key"])        # 曲库包里的原声文件不动，只拉黑名单
                continue
            p = str(d.get("path") or "")
            inside = False
            if p and ext_root:
                try:
                    inside = os.path.commonpath(
                        [os.path.abspath(p), ext_root]) == ext_root
                except Exception:
                    inside = False
            if inside and os.path.isfile(p):
                try:
                    os.remove(p)                # 真删本地文件
                    killed += 1
                    continue
                except Exception:
                    pass
            failed += 1
            blocked.append(d["key"])            # 删不掉也别让它再冒出来
        self._trash.forget(keys, blocked_keys=blocked)
        self._guard(self.player.refresh_library)   # 已删的歌从内存曲库也清掉
        self._fill_nav()
        self._select_scope(self._cur_scope)
        msg = "彻底删掉 {} 首，导入的本地文件删了 {} 个".format(len(items), killed)
        if builtin_n:
            msg += ("；{} 首内置原声只从曲库屏蔽，曲库包里的文件一个都没动"
                    "（想找回来：清掉 playlist\\trash.json 里的屏蔽记录，重启麟就回到曲库了）").format(builtin_n)
        if failed:
            msg += "；{} 个文件没能删掉（可能正被占用），也从曲库屏蔽了".format(failed)
        self._say(msg)

    def _purge_all_trash(self):
        self._purge_trash(self._trash.keys())

    # ---- 分类夹（歌单）导航 -----------------------------------------------
    MODE_ICONS = {"sequential": "➡️", "loop": "🔁", "one": "🔂", "random": "🔀"}

    def _build_nav(self):
        """左侧导航面板：系统分类置顶 + 自建分类夹 + 底部「新建」按钮。"""
        box = QWidget()
        box.setFixedWidth(200)
        box.setObjectName("navBox")
        box.setStyleSheet(
            "#navBox{background:rgba(255,250,240,210);"
            "border:1px solid rgba(216,196,160,190);border-radius:8px;}"
            "#navBox QLabel{color:#5a3d2b;background:transparent;}"
            "#navBox QListWidget{background:transparent;border:none;color:#3d2b1f;}"
            "#navBox QListWidget::item{padding:5px 4px;border-radius:4px;}"
            "#navBox QListWidget::item:selected{background:rgba(232,213,176,240);color:#3d2b1f;}"
            "#navBox QListWidget::item:hover{background:rgba(245,235,214,200);}")
        v = QVBoxLayout(box)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)
        title = QLabel("我的分类夹")
        title.setStyleSheet("font-weight:bold;font-size:14px;")
        v.addWidget(title)
        self.nav = QListWidget()
        self.nav.setDragDropMode(QAbstractItemView.InternalMove)   # 自建分类可上下拖动排序
        self.nav.setDefaultDropAction(Qt.MoveAction)
        self.nav.setSelectionMode(QAbstractItemView.SingleSelection)
        self.nav.setContextMenuPolicy(Qt.CustomContextMenu)
        self.nav.customContextMenuRequested.connect(self._nav_menu)
        self.nav.itemClicked.connect(self._on_nav_click)
        self.nav.model().rowsMoved.connect(self._on_nav_reordered)
        v.addWidget(self.nav, 1)
        self.nav_hint = QLabel("")
        self.nav_hint.setWordWrap(True)
        self.nav_hint.setStyleSheet("color:#8a6f4d;font-size:11px;")
        v.addWidget(self.nav_hint)
        new_btn = QPushButton("＋ 新建分类夹")
        new_btn.setFocusPolicy(Qt.NoFocus)
        new_btn.clicked.connect(self._new_playlist)
        v.addWidget(new_btn)
        self._nav_box = box

    def _is_user_scope(self, scope_id=None):
        sid = self._cur_scope if scope_id is None else scope_id
        return bool(self._store.by_id(sid))

    def _scope_label(self, scope_id):
        if scope_id == TRASH_SCOPE:
            return TRASH_NAME
        for sid, name in SYSTEM_SCOPES:
            if sid == scope_id:
                return name
        return self._store.name_of(scope_id) or "分类夹"

    def _fill_nav(self):
        """刷新左侧导航（系统分类带数量角标，自建分类夹排在下面）。"""
        if self.player is None:
            return
        self.nav.blockSignals(True)
        try:
            self.nav.clear()
            # 计数只算还在曲库里的：回收站里的、彻底删掉的都不算
            lib = [t for t in self.player.library_tracks()
                   if not self._trash.is_hidden(t[2], t[1])]
            counts = {
                SYS_ALL: len(lib),
                SYS_BUILTIN: sum(1 for t in lib if t[2] == "内置"),
                SYS_EXTERNAL: sum(1 for t in lib if t[2] == "本地"),
            }
            for sid, name in SYSTEM_SCOPES:
                it = QListWidgetItem("{}  ({})".format(name, counts.get(sid, 0)))
                it.setData(Qt.UserRole, sid)
                it.setData(Qt.UserRole + 2, "sys")
                it.setToolTip("系统分类，不可改名/删除")
                self.nav.addItem(it)
            # 「我喜欢的音乐」：固定收藏夹（网易云同款），不可删除、不参与拖动排序
            fav = self._store.ensure_favorites()
            it = QListWidgetItem("❤ {}  ({})".format(
                fav["playlist_name"], len(fav["music_list"])))
            it.setData(Qt.UserRole, FAV_ID)
            it.setData(Qt.UserRole + 2, "fav")
            it.setToolTip("收藏夹：点歌前面的 ♥ 就收进来；右键可改名/清空（不能删除）")
            self.nav.addItem(it)
            # 回收站：移出的歌先放这里，能恢复，也能彻底删除
            it = QListWidgetItem("♻ {}  ({})".format(TRASH_NAME, self._trash.count()))
            it.setData(Qt.UserRole, TRASH_SCOPE)
            it.setData(Qt.UserRole + 2, "trash")
            it.setToolTip("移出的歌先放这儿：右键/底部按钮可恢复或彻底删除"
                          "（彻底删除会删掉导入的本地文件）")
            self.nav.addItem(it)
            others = [d for d in self._store.playlists if d["playlist_id"] != FAV_ID]
            if others:
                sep = QListWidgetItem("—— 我创建的 ——")
                sep.setFlags(Qt.NoItemFlags)
                sep.setForeground(QColor("#a8845c"))
                self.nav.addItem(sep)
            for d in others:
                it = QListWidgetItem("🎵 {}  ({})".format(
                    d["playlist_name"], len(d["music_list"])))
                it.setData(Qt.UserRole, d["playlist_id"])
                it.setData(Qt.UserRole + 2, "user")
                it.setToolTip("右键：重命名 / 清空 / 删除；上下拖动可调顺序")
                self.nav.addItem(it)
            target = self._want_scope or self._cur_scope
            for i in range(self.nav.count()):
                it = self.nav.item(i)
                if it.data(Qt.UserRole) == target:
                    self.nav.setCurrentItem(it)
                    break
        finally:
            self.nav.blockSignals(False)

    def _sync_nav_selection(self):
        self.nav.blockSignals(True)
        try:
            target = self._want_scope or self._cur_scope
            for i in range(self.nav.count()):
                it = self.nav.item(i)
                if it.data(Qt.UserRole) == target:
                    self.nav.setCurrentItem(it)
                    break
        finally:
            self.nav.blockSignals(False)

    def _scope_tracks(self, scope_id):
        """某个分类下的曲目：系统分类直接过滤曲库，自建分类按歌单里存的顺序还原。"""
        if self.player is None:
            return []
        if scope_id == TRASH_SCOPE:
            return self._trash.tracks()     # 回收站只读：看看有哪些，不参与播放
        lib = [t for t in self.player.library_tracks()
               if not self._trash.is_hidden(t[2], t[1])]
        if scope_id == SYS_ALL:
            return lib
        if scope_id == SYS_BUILTIN:
            return [t for t in lib if t[2] == "内置"]
        if scope_id == SYS_EXTERNAL:
            return [t for t in lib if t[2] == "本地"]
        by_key = {track_key(t[2], t[1]): t for t in lib}
        by_title = {}
        for t in lib:
            by_title.setdefault(t[1], t)
        out, seen = [], set()
        for k in self._store.keys(scope_id):
            t = by_key.get(k)
            if t is None:
                # 老歌单里的来源标签可能是旧版错标的（"本地|内置曲名"），按曲名找回
                t = by_title.get(split_key(k)[1])
            if t is None:
                continue            # 文件被删了就跳过这一首，不阻塞整个歌单
            if self._trash.is_hidden(t[2], t[1]):
                continue            # 进了回收站的歌先别在分类夹里露头
            rk = track_key(t[2], t[1])
            if rk in seen:
                continue            # 旧键 + 新键指向同一首时只显示一次
            seen.add(rk)
            out.append(t)
        return out

    def _select_scope(self, scope_id, autoplay=False, immediate=False):
        """切换分类夹：默认让当前这首歌放完再换队列（需求 3.2.1），
        设置里勾了「立即切换」就当场换。

        不管队列换不换，列表都立刻切到目标分类夹——用户点了分类夹，
        第一眼就该看见里面的歌（以前延迟切换时列表也留在旧分类，
        用户以为「点了创建好的分类夹没反应」）。
        """
        if self.player is None:
            self._cur_scope = scope_id
            self._want_scope = None
            return
        if scope_id == TRASH_SCOPE:
            # 回收站是只读视图：只换列表，播放队列一点不动，
            # 免得把「已经删掉」的歌又塞回播放器
            self._cur_scope = TRASH_SCOPE
            self._want_scope = None
            self.nav_hint.setText("")
            self._refresh_all()
            return
        tracks = self._scope_tracks(scope_id)
        win = self.parent()
        immediate = immediate or bool(getattr(win, "settings", {}).get(
            "playlist_switch_immediate", False))
        defer = (self.player.is_playing() or self.player.is_paused()) and not immediate
        self._switching = True
        try:
            switched = self.player.set_scope(tracks, scope_id, defer=defer)
        finally:
            self._switching = False
        if switched:
            self._cur_scope = scope_id
            self._want_scope = None
            self.nav_hint.setText("")
        else:
            # 列表先切到目标分类夹（_view_tracks 读 _want_scope），播放队列不动；
            # 等这首放完播放器自己套上新队列再发 scopeChanged
            self._cur_scope = scope_id
            self._want_scope = scope_id
            cur = self.player.current_track() or {}
            self.nav_hint.setText("《{}》放完就切到「{}」…".format(
                cur.get("title", "当前这首"), self._scope_label(scope_id)))
        self._refresh_all()
        if autoplay and self.player.tracks() and not self.player.is_playing():
            self._guard(lambda: self.player.play_at(0))

    def _on_nav_click(self, item):
        if item is None:
            return
        sid = item.data(Qt.UserRole)
        if not sid or sid == (self._want_scope or self._cur_scope):
            return
        self._select_scope(sid)

    def _on_scope_changed(self, scope_id):
        """播放器把延迟的分类夹切换真正生效了（上一首放完）。"""
        self._cur_scope = scope_id
        self._want_scope = None
        self.nav_hint.setText("")
        if not self._switching:
            self._refresh_all()

    def _on_nav_reordered(self, *args):
        """拖动改了自建分类的上下顺序：把新顺序写回 playlists.json。"""
        QTimer.singleShot(0, self._apply_nav_order)

    def _apply_nav_order(self):
        order = []
        for i in range(self.nav.count()):
            it = self.nav.item(i)
            if it.data(Qt.UserRole + 2) == "user":
                order.append(it.data(Qt.UserRole))
        if order:
            self._store.set_order(order)
        self._fill_nav()

    def _nav_menu(self, pos):
        it = self.nav.itemAt(pos)
        kind = it.data(Qt.UserRole + 2) if it is not None else None
        if kind == "trash":
            menu = QMenu(self)
            a_play = menu.addAction("▶ 切到回收站")
            a_back = menu.addAction("♻ 全部恢复（放回曲库）")
            menu.addSeparator()
            a_kill = menu.addAction("🗑 彻底删除全部（不可恢复）")
            act = menu.exec_(self.nav.mapToGlobal(pos))
            if act is a_play:
                self._select_scope(TRASH_SCOPE)
            elif act is a_back:
                self._restore_all_trash()
            elif act is a_kill:
                self._purge_all_trash()
            return
        if kind == "fav":
            pid = it.data(Qt.UserRole)
            menu = QMenu(self)
            a_play = menu.addAction("▶ 切到喜欢的音乐")
            a_rename = menu.addAction("✏️ 重命名")
            a_clear = menu.addAction("🧹 清空收藏（不删文件）")
            act = menu.exec_(self.nav.mapToGlobal(pos))
            if act is a_play:
                self._select_scope(pid)
            elif act is a_rename:
                self._rename_playlist(pid)
            elif act is a_clear:
                self._clear_playlist(pid)
            return
        if kind != "user":
            menu = QMenu(self)
            act = menu.addAction("＋ 新建分类夹")
            if menu.exec_(self.nav.mapToGlobal(pos)) is act:
                self._new_playlist()
            return
        pid = it.data(Qt.UserRole)
        menu = QMenu(self)
        a_play = menu.addAction("▶ 切到这个分类夹")
        a_rename = menu.addAction("✏️ 重命名")
        a_clear = menu.addAction("🧹 清空歌曲（不删文件）")
        a_up = menu.addAction("⬆ 上移")
        a_down = menu.addAction("⬇ 下移")
        menu.addSeparator()
        a_del = menu.addAction("🗑 删除分类夹（不删文件）")
        act = menu.exec_(self.nav.mapToGlobal(pos))
        if act is a_play:
            self._select_scope(pid)
        elif act is a_rename:
            self._rename_playlist(pid)
        elif act is a_clear:
            self._clear_playlist(pid)
        elif act is a_up:
            self._store.move(pid, -1)
            self._fill_nav()
        elif act is a_down:
            self._store.move(pid, 1)
            self._fill_nav()
        elif act is a_del:
            self._delete_playlist(pid)

    def _say(self, text):
        win = self.parent()
        if hasattr(win, "say"):
            try:
                win.say(text, split=False)
            except Exception:
                pass

    def _new_playlist(self):
        name, ok = QInputDialog.getText(
            self, "新建分类夹", "分类夹名称（最多 {} 字）：".format(NAME_MAX))
        if not ok:
            return
        name = (name or "").strip()
        if not name:
            QMessageBox.information(self, "新建分类夹", "名字不能是空的哦～")
            return
        d = self._store.create(name)
        if not d:
            return
        self._fill_nav()
        self._select_scope(d["playlist_id"])
        self._say("新分类夹「{}」建好啦～在歌曲上右键就能往里放♪".format(
            d["playlist_name"]))

    def _rename_playlist(self, pid):
        d = self._store.by_id(pid)
        if not d:
            return
        name, ok = QInputDialog.getText(
            self, "重命名分类夹", "新的名字（最多 {} 字）：".format(NAME_MAX),
            text=d["playlist_name"])
        if not ok:
            return
        if not (name or "").strip():
            QMessageBox.information(self, "重命名分类夹", "名字不能是空的哦～")
            return
        self._store.rename(pid, name)
        self._fill_nav()
        self._say("分类夹改名叫「{}」啦～".format(self._store.name_of(pid)))

    def _clear_playlist(self, pid):
        name = self._store.name_of(pid)
        r = QMessageBox.question(
            self, "清空分类夹",
            "清空「{}」里的全部歌曲？\n\n只解除收纳关系，"
            "不会删除本地音乐文件，其他分类夹也不受影响。".format(name),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        self._store.clear(pid)
        self._fill_nav()
        if self._cur_scope == pid:
            self._select_scope(pid)
        self._say("「{}」清空啦，歌都还在～".format(name))

    def _delete_playlist(self, pid):
        if self._store.is_favorites(pid):
            self._say("「{}」是麟的收藏夹，删不掉哦～想清空的话右键它就好".format(FAV_NAME))
            return
        name = self._store.name_of(pid)
        r = QMessageBox.question(
            self, "删除分类夹",
            "删除分类夹「{}」？\n\n仅删除分类，不会删除本地音乐文件。".format(name),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        self._store.delete(pid)
        if pid in (self._cur_scope, self._want_scope):
            self._cur_scope = SYS_ALL
            self._want_scope = None
        self._fill_nav()
        self._select_scope(self._cur_scope)
        self._say("分类夹「{}」删掉啦，歌一首没少".format(name))

    def _selected_keys(self):
        keys = []
        for it in self.queue.selectedItems():
            k = it.data(Qt.UserRole + 2)
            if k and k not in keys:
                keys.append(k)
        return keys

    def _toggle_playlist(self, pid, keys):
        d = self._store.by_id(pid)
        if not d or not keys:
            return
        stored = self._stored_keys(pid, keys)
        if len(stored) >= len(set(keys)):
            n = self._store.remove_tracks(pid, stored)
            msg = "从「{}」拿出 {} 首（文件还在）".format(d["playlist_name"], n)
        else:
            # 老歌单里存着旧来源标签的键，别再重复加一遍同一首
            have = set(self._resolved_key(k) for k in self._store.keys(pid))
            add = [k for k in keys if self._resolved_key(k) not in have]
            n = self._store.add_tracks(pid, add)
            msg = "往「{}」放进 {} 首♪".format(d["playlist_name"], n)
        self._fill_nav()
        if self._cur_scope == pid:
            self._select_scope(pid)
        self._say(msg)

    def _queue_menu(self, pos):
        if self.player is None:
            return
        keys = self._selected_keys()
        if self._cur_scope == TRASH_SCOPE and not self._want_scope:
            # 回收站里只给两件事：恢复、彻底删除（列表是只读的，不能播放/收藏）
            menu = QMenu(self)
            a_back = menu.addAction("♻ 恢复选中（放回曲库）")
            a_kill = menu.addAction("🗑 彻底删除选中（不可恢复）")
            a_back.setEnabled(bool(keys))
            a_kill.setEnabled(bool(keys))
            act = menu.exec_(self.queue.viewport().mapToGlobal(pos))
            if act is a_back:
                self._restore_trash(keys)
            elif act is a_kill:
                self._purge_trash(keys)
            return
        menu = QMenu(self)
        if keys:
            fav_on = all(self._is_fav_key(k) for k in keys)
            act = menu.addAction("💔 取消喜欢" if fav_on else "♥ 喜欢（收进收藏夹）")
            act.triggered.connect(
                lambda _c=False, ks=list(keys): self._toggle_fav(ks))
            menu.addSeparator()
        add_menu = menu.addMenu("➕ 添加到分类夹")
        others = [d for d in self._store.playlists if d["playlist_id"] != FAV_ID]
        if others:
            for d in others:
                pid = d["playlist_id"]
                act = QAction(d["playlist_name"], add_menu)
                act.setCheckable(True)
                act.setChecked(bool(keys) and len(self._stored_keys(pid, keys)) >= len(set(keys)))
                act.setEnabled(bool(keys))
                act.triggered.connect(
                    lambda _c=False, p=pid, ks=list(keys): self._toggle_playlist(p, ks))
                add_menu.addAction(act)
        else:
            act = add_menu.addAction("还没有分类夹，点这里新建…")
            act.triggered.connect(self._new_playlist)
        if self._is_user_scope():
            act = menu.addAction("➖ 从「{}」移除".format(
                self._scope_label(self._cur_scope)))
            act.triggered.connect(self._remove_selected)
        first = self.queue.itemAt(pos)
        if first is not None:
            idx = first.data(Qt.UserRole + 1)
            if isinstance(idx, int) and idx >= 0:
                act = menu.addAction("▶ 播放这首")
                act.triggered.connect(
                    lambda _c=False, i=idx: self._guard(lambda: self.player.play_at(i)))
        menu.exec_(self.queue.viewport().mapToGlobal(pos))

    def _on_queue_reordered(self, *args):
        """自建分类夹里拖动歌曲调顺序：落定后把新顺序写回歌单。"""
        if self.player is None or not self._is_user_scope():
            return
        QTimer.singleShot(0, self._apply_queue_order)

    def _apply_queue_order(self):
        if self.player is None or not self._is_user_scope():
            return
        keys = []
        for i in range(self.queue.count()):
            it = self.queue.item(i)
            k = it.data(Qt.UserRole + 2) if it is not None else None
            if k:
                keys.append(k)
        pid = self._cur_scope
        if keys and self._store.keys(pid) != keys:
            self._store.set_tracks(pid, keys)
            if self.player.pending_scope() is None:
                # 调序不打断播放：歌还是这一批，只是顺序变了，当场换队列即可
                self.player.set_scope(self._scope_tracks(pid), pid, defer=False)
                self._refresh_all()
            else:
                self._select_scope(pid)

    def _update_search_hint(self):
        sid = self._cur_scope
        if sid == TRASH_SCOPE:
            txt = "🔍 在回收站里搜索…"
        elif sid == SYS_ALL:
            txt = "🔍 搜索全部音乐…"
        elif sid == SYS_BUILTIN:
            txt = "🔍 搜索内置原声…"
        elif sid == SYS_EXTERNAL:
            txt = "🔍 搜索我的导入…"
        else:
            txt = "🔍 在「{}」里搜索…".format(self._scope_label(sid))
        self.search.setPlaceholderText(txt)

    # ---- 封面 -------------------------------------------------------------
    def _load_cover(self):
        """优先用 music_cover.png（按麟的四视图生成的封面），缺了就程序自绘占位。"""
        pix = QPixmap()
        try:
            path = resource_path("music_cover.png")
            if os.path.exists(path):
                pix.load(path)
        except Exception:
            pix = QPixmap()
        if not pix.isNull():
            self.cover_lab.setPixmap(pix.scaled(
                120, 120, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
            return
        # 占位封面：暖色渐变 + ♪
        pix = QPixmap(120, 120)
        p = QPainter(pix)
        try:
            g = QLinearGradient(0, 0, 120, 120)
            g.setColorAt(0.0, QColor("#f7e8d0"))
            g.setColorAt(1.0, QColor("#e0c9a0"))
            p.fillRect(0, 0, 120, 120, g)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QColor("#a8845c"))
            f = QFont("Microsoft YaHei", 40)
            f.setBold(True)
            p.setFont(f)
            p.drawText(pix.rect(), Qt.AlignCenter, "♪")
        finally:
            p.end()
        self.cover_lab.setPixmap(pix)
