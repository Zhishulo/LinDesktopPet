# -*- coding: utf-8 -*-
"""全部对话框（原 oc.py 四、五区）：历史/帮助/记忆/人设/档案/专注/背包/
日程/事件/猜拳/骰子/整理/便签/商城/API 管理/API 编辑/设置。"""
import os
import sys
import time
import random

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication, QDialog, QLabel, QLineEdit, QPushButton, QHBoxLayout,
    QVBoxLayout, QFrame, QPlainTextEdit, QSpinBox, QSlider, QFormLayout,
    QListWidget, QListWidgetItem, QGridLayout, QFileDialog, QMessageBox,
    QTextBrowser, QTimeEdit, QDialogButtonBox, QCheckBox, QWidget,
    QRadioButton, QStackedWidget, QScrollArea, QProgressBar)

from config import SHOP_ITEMS, MEMORY_LOG_DIR
from data import data_dir, load_json, save_json, load_log, save_log
from api_client import ask_llm, load_api_config, save_api_config, test_api
from mood import (MOOD_TYPES, MOOD_TAGS, INTENSITY_NAMES, CRISIS_LINES,
                  load_moods, add_record, delete_record, analyze,
                  mood_name, mood_emoji)
from assessment import (SCALES, SCALES_BY_KEY, load_assessments,
                        delete_assessment, add_assessment, scales_by_category)
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
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                tb.setMarkdown(f.read())
        else:
            tb.setPlainText("readme.md 不存在，首次启动会自动生成。")
        lay.addWidget(tb)
        h = QHBoxLayout()
        edit_btn = QPushButton("用系统记事本打开 readme.md")
        if sys.platform == "win32":
            edit_btn.clicked.connect(lambda: os.startfile(path))
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        h.addWidget(edit_btn)
        h.addStretch(1)
        h.addWidget(close_btn)
        lay.addLayout(h)


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
            line = "{}  {} {}（{}/5）{}{}".format(
                (r.get("created_at") or "")[5:16],
                mood_emoji(r.get("mood", "neutral")),
                mood_name(r.get("mood", "neutral")),
                r.get("intensity", 3), tags,
                ("  " + note) if note else "")
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

    def refresh(self):
        self.list.clear()
        cfg = load_api_config()
        cur = cfg.get("current", "")
        cur_name = ""
        for a in cfg.get("apis", []):
            if a.get("id") == cur:
                cur_name = a.get("name", "")
                break
        self.current_label.setText("当前生效：{}（聊天优先使用，失败自动切换备用）".format(cur_name or "未设置"))
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

    def save_settings(self):
        win = self.parent
        evt_min = self.evt_min_spin.value() * 60
        evt_max = max(evt_min, self.evt_max_spin.value() * 60)
        win.settings["pet_scale"] = self.scale_slider.value()
        win.settings["chat_interval"] = self.chat_spin.value() * 60
        win.settings["event_min"] = evt_min // 60
        win.settings["event_max"] = evt_max // 60
        win.settings["event_weight"] = self.wt_slider.value()
        save_json("settings.json", win.settings)
        win.idle_timeout = self.chat_spin.value() * 60
        win.event_min = evt_min
        win.event_max = evt_max
        win.event_freq = random.randint(evt_min, evt_max)
        win.event_weight = self.wt_slider.value()
        win.say("设置保存好啦～", split=False)
        QMessageBox.information(self, "设置", "已保存！\n闲聊与随机事件参数已立即生效；\n宠物尺寸将在下次启动时生效。")
