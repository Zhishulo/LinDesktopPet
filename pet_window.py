# -*- coding: utf-8 -*-
"""主窗口（原 oc.py 五、六区）：桌宠本体、状态机、系统感知、对话、
记忆业务逻辑与程序入口。"""
import os
import sys
import re
import json
import time
import datetime
import random
import threading
import subprocess
import webbrowser

# ===== 修复 Qt 找不到平台插件（中文路径常见问题）=====
import PyQt5
_plugin_path = os.path.join(os.path.dirname(PyQt5.__file__), "Qt5", "plugins")
if os.path.isdir(_plugin_path):
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = _plugin_path

import requests
try:
    import psutil
except ImportError:
    psutil = None

if sys.platform == "win32":
    try:
        import winsound
    except Exception:
        winsound = None
else:
    winsound = None

from PyQt5.QtCore import (Qt, QTimer, QPropertyAnimation, QEasingCurve,
                          QPoint, QEvent)
from PyQt5.QtGui import QPixmap, QFont, QIcon
from PyQt5.QtWidgets import (QApplication, QWidget, QLabel, QMenu, QLineEdit,
                             QPushButton, QHBoxLayout, QVBoxLayout, QFrame,
                             QGraphicsOpacityEffect, QProgressBar, QDialog,
                             QMessageBox)

from config import *   # 全部常量（图片路径/人设/台词/商城/天气/记忆参数）
from data import (data_dir, resource_path, load_json, save_json, load_api_key,
                  save_api_key, load_log, save_log, append_log,
                  ensure_data_files, load_memory, save_memory, load_settings)
from api_client import (SignalBus, ask_llm, load_api_config, save_api_config,
                        ask_api_key_once)
from local_brain import local_reply as _local_reply
from memory import memory_log_dir, write_daily_log, extract_facts_local
from mood import (load_moods, save_moods, recent_records, NEGATIVE_KEYS,
                  mood_name, mood_reply, pick_warm_word)
from assessment import recent_assessment
from widgets import BubbleLabel, PetLabel
from dialogs import (HistoryDialog, HelpDialog, MemoryDialog, PersonaDialog,
                     ProfileDialog, FocusDialog, BackpackDialog, ScheduleDialog,
                     EventDialog, RPSDialog, DiceDialog, OrganizeDialog,
                     NoteDialog, ShopDialog, ApiEditDialog, ApiManagerDialog,
                     SettingsDialog, MoodRecordDialog, MoodHistoryDialog,
                     MoodStatsDialog, CrisisSupportDialog, AssessmentDialog,
                     AssessmentHistoryDialog)
class PetWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("丰裕之角桌宠 - 麟")
        self.setFixedSize(340, 562)
        # ---- 自定义配置（settings.json，自动兼容旧档） ----
        self.settings = load_settings()
        self.scale = max(0.4, min(1.5, float(self.settings.get("pet_scale", 100)) / 100.0))
        self.idle_timeout = int(self.settings.get("chat_interval", 1200))
        self.event_min = max(1, int(self.settings.get("event_min", 30))) * 60
        self.event_max = max(self.event_min, int(self.settings.get("event_max", 120)) * 60)
        self.event_freq = random.randint(self.event_min, self.event_max)
        self.event_weight = max(0, min(100, int(self.settings.get("event_weight", 60))))
        self._feed_lock = 0.0

        # ---- 背景框（商城购买后显示，放在最底层） ----
        self.bg_label = QLabel(self)
        self.bg_label.setGeometry(4, 120, 332, 404)
        self.bg_label.hide()
        self.bg_label.lower()

        # ---- 气泡 / 桌宠 / 输入框 ----
        self.bubble = BubbleLabel(self)
        self.bubble.setGeometry(6, 8, 328, 118)
        self.bubble.hide()
        # 常驻气泡透明度效果：复用同一实例，避免替换效果时
        # 删除仍在运行的淡出动画目标，导致空指针崩溃
        self._bubble_effect = QGraphicsOpacityEffect(self.bubble)
        self.bubble.setGraphicsEffect(self._bubble_effect)

        self.pet = PetLabel(self)
        self.pet.setGeometry(10, 126, 320, 392)
        self.pet.setAlignment(Qt.AlignCenter)
        self.pet.setCursor(Qt.OpenHandCursor)

        self.input_frame = QFrame(self)
        self.input_frame.setGeometry(6, 526, 328, 30)
        self.input_frame.setStyleSheet("QFrame { background: rgba(40, 28, 20, 235); border-radius: 17px; }")
        row = QHBoxLayout(self.input_frame)
        row.setContentsMargins(12, 0, 6, 0)
        row.setSpacing(4)
        self.input = QLineEdit(self.input_frame)
        self.input.setPlaceholderText("和麟说点什么…（回车发送，输入 #帮助 看指令）")
        self.input.setStyleSheet("QLineEdit { background: transparent; border: none; color: #ffffff; font-size: 13px; }")
        self.send_btn = QPushButton("发送", self.input_frame)
        self.send_btn.setStyleSheet(
            "QPushButton { background: #9c6b45; color: #ffffff; border: none;"
            " border-radius: 12px; padding: 4px 14px; font-size: 12px; }"
            "QPushButton:hover { background: #b58155; }")
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)

        # ---- 好感度条 ----
        self.aff_bar = QFrame(self)
        self.aff_bar.setGeometry(6, 496, 150, 24)
        self.aff_bar.setStyleSheet("QFrame{background:rgba(40,28,20,235);border-radius:12px;}")
        ah = QHBoxLayout(self.aff_bar)
        ah.setContentsMargins(8, 2, 8, 2)
        ah.setSpacing(4)
        self.aff_bar_lab = QLabel("❤️")
        self.aff_bar_lab.setStyleSheet("color:#ff8fa3;font-size:12px;background:transparent;")
        self.aff_bar_prog = QProgressBar()
        self.aff_bar_prog.setRange(0, 200)
        self.aff_bar_prog.setValue(0)
        self.aff_bar_prog.setFixedWidth(90)
        self.aff_bar_prog.setTextVisible(False)
        self.aff_bar_prog.setStyleSheet(
            "QProgressBar{background:#5a3a28;border:none;border-radius:5px;height:8px;}"
            "QProgressBar::chunk{background:#ff8fa3;border-radius:5px;}")
        ah.addWidget(self.aff_bar_lab)
        ah.addWidget(self.aff_bar_prog)
        self.aff_bar.hide()

        # ---- 专注倒计时标签（状态行左侧，与好感度条/天气互斥显示） ----
        self.focus_label = QLabel("⏱ 00:00", self)
        self.focus_label.setGeometry(6, 496, 244, 24)
        self.focus_label.setStyleSheet(
            "background:rgba(40,28,20,235);color:#ffe9c9;border-radius:12px;padding:4px 8px;font-size:12px;")
        self.focus_label.setAlignment(Qt.AlignCenter)
        self.focus_label.hide()

        # ---- 网络状态小图标 ----
        self.net_label = QLabel("📵", self)
        self.net_label.setGeometry(288, 128, 32, 32)
        self.net_label.setStyleSheet("background:rgba(0,0,0,200);border-radius:16px;font-size:18px;")
        self.net_label.setAlignment(Qt.AlignCenter)
        self.net_label.hide()

        # ---- 雨伞叠加层 ----
        self.umbrella_label = QLabel(self)
        self.umbrella_label.setGeometry(120, 250, 200, 240)
        self.umbrella_label.setAlignment(Qt.AlignCenter)
        self.umbrella_label.hide()

        # ---- 天气小标签（底部状态行中段） ----
        self.weather_label = QLabel("🌐", self)
        self.weather_label.setGeometry(162, 496, 88, 24)
        self.weather_label.setStyleSheet(
            "background:rgba(40,28,20,235);color:#fff;border-radius:12px;padding:2px 6px;font-size:11px;")
        self.weather_label.setAlignment(Qt.AlignCenter)
        self.weather_label.hide()

        # ---- 模式状态标签（底部状态行右段：AI 联网 / 离线保底） ----
        self.mode_label = QLabel("", self)
        self.mode_label.setGeometry(256, 496, 78, 24)
        self.mode_label.setStyleSheet(
            "background:rgba(40,28,20,235);color:#ffe9c9;border-radius:12px;padding:2px 6px;font-size:11px;")
        self.mode_label.setAlignment(Qt.AlignCenter)
        self.mode_label.hide()

        # ---- 剪贴板互动条 ----
        self.clip_overlay = QFrame(self)
        self.clip_overlay.setGeometry(6, 122, 328, 44)
        self.clip_overlay.setStyleSheet("QFrame{background:rgba(30,20,14,200);border-radius:10px;}")
        ch = QHBoxLayout(self.clip_overlay)
        ch.setContentsMargins(10, 4, 6, 4)
        ch.setSpacing(6)
        self.clip_lab = QLabel("📋 你复制了什么？")
        self.clip_lab.setStyleSheet("color:#fff;background:transparent;font-size:12px;")
        b1 = QPushButton("看看")
        b1.setStyleSheet("QPushButton{background:#8c5a3c;color:#fff;border:none;border-radius:8px;padding:3px 10px;}")
        b2 = QPushButton("吃掉")
        b2.setStyleSheet("QPushButton{background:#b23a48;color:#fff;border:none;border-radius:8px;padding:3px 10px;}")
        b1.clicked.connect(self._clip_look)
        b2.clicked.connect(self._clip_eat)
        ch.addWidget(self.clip_lab)
        ch.addStretch(1)
        ch.addWidget(b1)
        ch.addWidget(b2)
        self.clip_overlay.hide()

        # ---- 状态数据 ----
        self.economy = load_json("economy.json", {"coins": 100, "last_checkin": "", "outfits": [],
                                                  "affinity": 0, "mood": 60, "current_outfit": ""})
        self.coins = int(self.economy.get("coins", 100))
        self.affinity = int(self.economy.get("affinity", 0))
        self.current_prompt = PERSONA_PROMPT
        self.profile = load_json("profile.json", {})
        self.mood_value = max(0, min(100, int(self.economy.get("mood", 60))))
        self.mood = "happy" if self.mood_value >= 70 else ("normal" if self.mood_value >= 40 else "sad")
        self.current_outfit = str(self.economy.get("current_outfit", "") or "")
        if self.current_outfit and not os.path.exists(resource_path("{}_close.png".format(self.current_outfit))):
            self.current_outfit = ""   # 换装图缺失时自动回退默认服装
        self.pixmaps = {}
        self.load_images()

        self.pose = "idle"
        self.mouth_on = False
        self.wag_idx = 0
        self.wag_cycles = 0

        self.sentences = []
        self.cur_idx = 0
        self.dlg_state = "idle"
        self.complete_time = 0.0
        self.typing_pos = 0

        self.busy = False
        self.history = []
        self._fade_anim = None
        self._shake_anim = None
        self._grav_anim = None
        self._eat_timer = None
        self._eat_count = 0
        self._pending_food = None
        self._pending_event = None
        self.event_busy = False
        self._wag_start = 0.0
        self.weather_temp = ""
        self.last_activity = time.time()
        self.click_count = 0
        self.drag_count = 0
        self._last_pet_time = 0
        self._last_clip = None
        self.focus_active = False
        self.focus_end = 0
        self.focus_minutes = 0
        self.sleep_mode = False
        self.forced_sleep = False
        self.game_mode = False
        self._pre_game_pos = None
        self.net_ok = True
        self.net_ms = 0
        self.weather = ""
        self.last_event_time = time.time()
        self.last_wake_date = ""
        self._last_over_msg = 0
        self._last_mem_msg = 0
        self._last_ping_msg = 0
        self._last_wx_msg = time.time()
        self._last_chime_hour = -1
        self.thunder_mode = False          # 雷雨天气害怕待机
        self.music_mode = False            # 听歌陪伴模式
        self.music_end = 0
        self._pending_emotion = None       # 回复完成后要播的表情
        self._say_done_cb = None           # say() 完成回调
        self._emotion_file = None          # 当前一次性表情帧
        self._feed_streak = 0              # 连续喂食计数（打嗝用）
        self._want_burp = False
        self._sleep_pending = False        # 打哈欠→入睡过渡中
        self._memory_busy = False
        self._pending_memory = None
        self._last_daily_mem = ""
        self._aff_was_visible = False      # 专注前好感度条是否可见
        self._last_mode_update = 0.0

        # ---- 信号 ----
        self.bus = SignalBus()
        self.bus.reply_ready.connect(self._on_reply, Qt.QueuedConnection)
        self.bus.reply_error.connect(self._on_api_error, Qt.QueuedConnection)
        self.bus.event_ready.connect(self._on_event, Qt.QueuedConnection)
        self.bus.food_ready.connect(self._on_food, Qt.QueuedConnection)
        self.bus.memory_ready.connect(self._on_memory, Qt.QueuedConnection)
        self.bus.weather_ready.connect(self._on_weather, Qt.QueuedConnection)
        self.bus.ping_ready.connect(self._on_ping, Qt.QueuedConnection)

        self.send_btn.clicked.connect(self.send_message)
        self.input.returnPressed.connect(self.send_message)
        self.pet.clicked.connect(self._on_pet_clicked)
        self.pet.drag_started.connect(self._on_drag_start)
        self.pet.drag_ended.connect(self._on_drag_end)
        self.pet.petted.connect(self._on_pet_hover)
        self.bubble.clicked.connect(self._on_bubble_click)

        # ---- 计时器 ----
        self.type_timer = QTimer(self)
        self.type_timer.setInterval(35)
        self.type_timer.timeout.connect(self._type_char)

        self.mouth_timer = QTimer(self)
        self.mouth_timer.setInterval(160)
        self.mouth_timer.timeout.connect(self._mouth_tick)

        self.wag_timer = QTimer(self)
        self.wag_timer.setInterval(200)
        self.wag_timer.timeout.connect(self._wag_tick)

        self.blink_timer = QTimer(self)      # 待机眨眼（缺图自动跳过）
        self.blink_timer.setInterval(4000)
        self.blink_timer.timeout.connect(self._blink_tick)
        self.blink_timer.start()
        
        self._sleepy_timer = QTimer(self)        # 睡意回复：慢悠悠晃脑袋
        self._sleepy_timer.setInterval(600)
        self._sleepy_timer.timeout.connect(self._sleepy_tick)

        self.auto_advance_timer = QTimer(self)
        self.auto_advance_timer.setSingleShot(True)
        self.auto_advance_timer.timeout.connect(self._auto_advance)

        self.auto_fade_timer = QTimer(self)
        self.auto_fade_timer.setSingleShot(True)
        self.auto_fade_timer.timeout.connect(self._auto_fade)

        self.idle_timer = QTimer(self)
        self.idle_timer.timeout.connect(self._idle_tick)
        self.idle_timer.start(random.randint(15000, 25000))

        self.clock_timer = QTimer(self)          # 每秒：整点/昼夜/日程/专注显示
        self.clock_timer.setInterval(1000)
        self.clock_timer.timeout.connect(self._clock_tick)
        self.clock_timer.start()

        self.focus_timer = QTimer(self)          # 专注监督
        self.focus_timer.setInterval(2000)
        self.focus_timer.timeout.connect(self._focus_check)
        self.focus_timer.start()

        self.sys_timer = QTimer(self)            # CPU/内存监控
        self.sys_timer.setInterval(5000)
        self.sys_timer.timeout.connect(self._system_check)
        self.sys_timer.start()

        self.game_timer = QTimer(self)           # 游戏侦测
        self.game_timer.setInterval(5000)
        self.game_timer.timeout.connect(self._game_check)
        self.game_timer.start()

        self.ping_timer = QTimer(self)           # 网络延迟
        self.ping_timer.setInterval(60000)
        self.ping_timer.timeout.connect(self._ping_check)
        self.ping_timer.start()

        self.weather_timer = QTimer(self)        # 天气
        self.weather_timer.setInterval(3600000)
        self.weather_timer.timeout.connect(self._weather_check)
        self.weather_timer.start()

        self.event_timer = QTimer(self)          # 随机事件
        self.event_timer.setInterval(60000)
        self.event_timer.timeout.connect(self._event_tick)
        self.event_timer.start()

        self.activity_timer = QTimer(self)       # 无操作闲聊陪伴
        self.activity_timer.setInterval(10000)
        self.activity_timer.timeout.connect(self._idle_activity_check)
        self.activity_timer.start()

        # 全局活动监听 + 剪贴板
        QApplication.instance().installEventFilter(self)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        # 初始显示
        self._show(self._idle_file())
        self._apply_scale()
        self._update_aff_bar()
        self._update_daynight(time.localtime().tm_hour)
        self._update_mode_label()

        # 启动任务
        QTimer.singleShot(900, self._greet)
        QTimer.singleShot(3000, self._weather_check)
        QTimer.singleShot(6000, self._ping_check)
        QTimer.singleShot(2000, self._startup_memory)

    # ---------- 图片 ----------
    def _pet_pix(self, pix):
        """统一高度缩放：所有姿态帧按同一显示高度 390px（100% 缩放）缩放，
        避免因画布尺寸不同导致角色忽大忽小。
        仅当某图宽度超宽（横构图，未按统一画布重制）时退回宽度适配防溢出。"""
        h = int(390 * self.scale)
        w = int(h * pix.width() / max(1, pix.height()))
        if w > int(320 * self.scale):
            return pix.scaled(int(320 * self.scale), int(390 * self.scale),
                              Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return pix.scaledToHeight(h, Qt.SmoothTransformation)

    def load_images(self):
        self.pixmaps.clear()
        names = set([IMG_CLOSE, IMG_OPEN, IMG_BACK, IMG_TURN, IMG_NING, IMG_SIT,
                     IMG_SLEEP, IMG_CLIMB, IMG_SWEAT, IMG_FAINT, IMG_OFFLINE,
                     IMG_LOW, IMG_HIGH, IMG_UMBRELLA, IMG_THINK, IMG_WORK,
                     IMG_WAVE, IMG_SAD, IMG_ANGRY, IMG_CRY, IMG_LOVE,
                     IMG_THUNDER, IMG_MUSIC, IMG_BURP, IMG_YAWN]
                    + IMG_TAIL + IMG_BLINK + IMG_SURPRISED)
        for name in names:
            path = resource_path(name)
            if not os.path.exists(path):
                continue
            pix = QPixmap(path)
            if pix.isNull():
                continue
            self.pixmaps[name] = self._pet_pix(pix)

    def _show(self, filename):
        if not filename:
            filename = IMG_CLOSE
        pix = self.pixmaps.get(filename)
        if pix is None:
            path = resource_path(filename)
            if os.path.exists(path):
                p = QPixmap(path)
                if not p.isNull():
                    pix = self._pet_pix(p)
                    self.pixmaps[filename] = pix
        if pix is None:
            pix = self.pixmaps.get(IMG_CLOSE)
        if pix is not None:
            self.pet.setPixmap(pix)

    def _idle_file(self):
        """待机图优先级：换装 → 雷雨害怕 → 心情低落 → 好感表情 → 默认"""
        if self.current_outfit:
            f = "{}_close.png".format(self.current_outfit)
            if os.path.exists(resource_path(f)):
                return f
        if self.thunder_mode and os.path.exists(resource_path(IMG_THUNDER)):
            return IMG_THUNDER
        if self.mood_value < 40 and os.path.exists(resource_path(IMG_SAD)):
            return IMG_SAD
        if self.affinity < 30 and os.path.exists(resource_path(IMG_LOW)):
            return IMG_LOW
        if self.affinity >= 120:
            if os.path.exists(resource_path(IMG_LOVE)):
                return IMG_LOVE
            if os.path.exists(resource_path(IMG_HIGH)):
                return IMG_HIGH
        return IMG_CLOSE

    def _close_file(self):
        if self.current_outfit:
            f = "{}_close.png".format(self.current_outfit)
            if os.path.exists(resource_path(f)):
                return f
        return IMG_CLOSE

    def _open_file(self):
        if self.current_outfit:
            f = "{}_open.png".format(self.current_outfit)
            if os.path.exists(resource_path(f)):
                return f
        return IMG_OPEN

    def _tail_files(self):
        """换装尾巴动画：套装名_tail1/2/3.png 齐全时用套装尾巴，否则回退默认"""
        if self.current_outfit:
            names = ["{}_tail{}.png".format(self.current_outfit, i) for i in (1, 2, 3)]
            if all(os.path.exists(resource_path(n)) for n in names):
                return names
        return IMG_TAIL

    def set_outfit(self, name):
        """换装并持久化：name 为空或“默认”则脱掉；对应图片缺失返回 False"""
        name = (name or "").strip()
        if not name or name == "默认":
            self.current_outfit = ""
        else:
            if not os.path.exists(resource_path("{}_close.png".format(name))):
                return False
            self.current_outfit = name
        self.economy["current_outfit"] = self.current_outfit
        save_json("economy.json", self.economy)
        self._show(self._idle_file())
        return True

    def reload_images(self):
        self.load_images()
        mapping = {"idle": self._idle_file(), "talking": self._idle_file(), "wag": self._idle_file(),
                   "ning": IMG_NING, "back": IMG_BACK, "turn": IMG_TURN,
                   "thinking": (IMG_THINK if os.path.exists(resource_path(IMG_THINK)) else self._idle_file()),
                   "work": (IMG_WORK if os.path.exists(resource_path(IMG_WORK)) else self._idle_file()),
                   "wave": (IMG_WAVE if os.path.exists(resource_path(IMG_WAVE)) else self._idle_file()),
                   "emotion": (getattr(self, "_emotion_file", None)
                               if getattr(self, "_emotion_file", None)
                               and os.path.exists(resource_path(self._emotion_file))
                               else self._idle_file()),
                   "music": (IMG_MUSIC if os.path.exists(resource_path(IMG_MUSIC)) else self._idle_file()),
                   "sleep": (IMG_SLEEP if os.path.exists(resource_path(IMG_SLEEP)) else IMG_BACK),
                   "climb": (IMG_CLIMB if os.path.exists(resource_path(IMG_CLIMB)) else IMG_BACK),
                   "overheat": (IMG_SWEAT if os.path.exists(resource_path(IMG_SWEAT)) else IMG_NING),
                   "lowmem": (IMG_FAINT if os.path.exists(resource_path(IMG_FAINT)) else IMG_SIT),
                   "offline": (IMG_OFFLINE if os.path.exists(resource_path(IMG_OFFLINE)) else IMG_NING)}
        self._show(mapping.get(self.pose, self._idle_file()))

    def apply_bg(self, name):
        path = resource_path("bg_{}.png".format(name))
        if os.path.exists(path):
            pix = QPixmap(path)
            if not pix.isNull():
                self.bg_label.setPixmap(pix.scaled(int(332 * self.scale), int(404 * self.scale),
                                                   Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.bg_label.show()
                self.bg_label.lower()
                return True
        return False

    def _apply_scale(self):
        """按 settings.json 的 pet_scale 整体缩放窗口与控件：
        - 坐标与控件尺寸等比缩放，但字体、控件高度/宽度设下限，
          避免小缩放时文字被控件裁剪（数字上下被"文本框"挡住的问题）；
        - 缩放 <70% 时进入紧凑模式：隐藏好感度条，只留天气与接入状态。"""
        s = self.scale
        W, H = int(340 * s), int(562 * s)
        self.setFixedSize(W, H)
        self.bg_label.setGeometry(int(4 * s), int(120 * s), int(332 * s), int(404 * s))
        self.bubble.setGeometry(int(6 * s), int(8 * s), int(328 * s), int(118 * s))
        self.pet.setGeometry(int(10 * s), int(126 * s), int(320 * s), int(392 * s))
        self.net_label.setGeometry(int(288 * s), int(128 * s), int(32 * s), int(32 * s))
        self.umbrella_label.setGeometry(int(120 * s), int(250 * s), int(200 * s), int(240 * s))
        self.clip_overlay.setGeometry(int(6 * s), int(122 * s), int(328 * s), int(44 * s))
        # 底部相对布局：输入框（高度下限 22px）→ 状态行（高度下限 16px）
        in_h = int(max(22, 30 * s))
        in_y = H - in_h - max(2, int(4 * s))
        self.input_frame.setGeometry(int(6 * s), in_y, int(328 * s), in_h)
        st_h = int(max(16, 24 * s))
        st_y = in_y - st_h - max(2, int(6 * s))
        # 状态行字体随缩放（最小 7px），内边距同步缩放，保证不裁剪
        fs = max(7, int(round(12 * s)))
        pad = max(1, int(round(2 * s)))

        def status_style(color):
            return ("background:rgba(40,28,20,235);color:{};border-radius:{}px;"
                    "padding:{}px {}px;font-size:{}px;").format(
                        color, max(3, int(round(12 * s))), pad,
                        max(2, int(round(6 * s))), fs)

        self.focus_label.setStyleSheet(status_style("#ffe9c9"))
        self.weather_label.setStyleSheet(status_style("#ffffff"))
        self.mode_label.setStyleSheet(status_style("#ffe9c9"))
        # 输入框与发送按钮字体随缩放
        self.input.setStyleSheet(
            "QLineEdit {{ background: transparent; border: none; color: #ffffff; font-size: {}px; }}"
            .format(max(10, int(round(13 * s)))))
        self.send_btn.setStyleSheet(
            "QPushButton {{ background: #9c6b45; color: #ffffff; border: none;"
            " border-radius: {}px; padding: {}px {}px; font-size: {}px; }}"
            "QPushButton:hover {{ background: #b58155; }}"
            .format(max(6, int(round(12 * s))), max(2, int(round(4 * s))),
                    max(4, int(round(14 * s))), max(9, int(round(12 * s)))))
        compact = s < 0.7
        if compact:
            # 紧凑模式：隐藏好感度条，天气 + 接入状态并排
            self.aff_bar.hide()
            self.aff_bar.setGeometry(0, 0, 0, 0)
            self._aff_was_visible = False
            w_w = max(40, int(88 * s))
            m_w = max(50, int(78 * s))
            self.weather_label.setGeometry(int(6 * s), st_y, w_w, st_h)
            mx = int(6 * s) + w_w + max(2, int(4 * s))
            self.mode_label.setGeometry(mx, st_y, m_w, st_h)
            # 专注时 focus 使用天气的位置（天气会隐藏），宽度截止到模式标签前，避免重叠
            self.focus_label.setGeometry(int(6 * s), st_y, mx - int(6 * s) - 2, st_h)
        else:
            self.aff_bar.setGeometry(int(6 * s), st_y, int(150 * s), st_h)
            self.focus_label.setGeometry(int(6 * s), st_y, int(244 * s), st_h)
            self.weather_label.setGeometry(int(162 * s), st_y, int(88 * s), st_h)
            self.mode_label.setGeometry(int(256 * s), st_y, int(78 * s), st_h)
        # 好感度条内部：爱心字号、进度条宽度、内边距随缩放
        self.aff_bar_lab.setStyleSheet(
            "color:#ff8fa3;font-size:{}px;background:transparent;".format(fs))
        self.aff_bar_prog.setFixedWidth(max(30, int(90 * s)))
        lay = self.aff_bar.layout()
        if lay is not None:
            lay.setContentsMargins(max(4, int(8 * s)), max(1, int(2 * s)),
                                   max(4, int(8 * s)), max(1, int(2 * s)))
        self.load_images()
        self._show(self._idle_file())
    # ---------- 好感度 / 金币 / 心情 ----------
    def _level_index(self, v):
        idx = 0
        for i, (mn, _, _) in enumerate(AFF_LEVELS):
            if v >= mn:
                idx = i
        return idx

    def _aff_level(self):
        return self._level_index(self.affinity)

    def _aff_level_name(self):
        return AFF_LEVELS[self._level_index(self.affinity)]

    def _update_aff_bar(self):
        self.aff_bar_prog.setValue(self.affinity)

    def add_affinity(self, n):
        old_lv = self._aff_level()
        self.affinity = max(0, self.affinity + n)
        self.economy["affinity"] = self.affinity
        save_json("economy.json", self.economy)
        self._update_aff_bar()
        new_lv = self._aff_level()
        if new_lv > old_lv and new_lv in AFF_LINES:
            self.say(AFF_LINES[new_lv], split=False)
            self._show(self._idle_file())

    def add_coins(self, n):
        self.coins = max(0, self.coins + n)
        self.economy["coins"] = self.coins
        save_json("economy.json", self.economy)

    def update_mood(self, delta, reason=""):
        self.mood_value = max(0, min(100, self.mood_value + delta))
        old = self.mood
        if self.mood_value >= 70:
            self.mood = "happy"
        elif self.mood_value >= 40:
            self.mood = "normal"
        else:
            self.mood = "sad"
        if old != self.mood and not self.sleep_mode:
            self.say({"happy": "（麟的心情变好啦！）", "normal": "（麟的心情平复了。）",
                      "sad": "（麟的心情有点低落…）"}[self.mood], split=False)
        self.economy["mood"] = self.mood_value
        save_json("economy.json", self.economy)

    # ---------- 心理陪伴：情绪记录响应 ----------
    def on_mood_recorded(self, rec):
        """用户保存情绪记录后，麟给出共情台词并播放对应表情"""
        key = rec.get("mood", "neutral")
        intensity = int(rec.get("intensity", 3))
        reply = mood_reply(key)
        if intensity >= 4:
            reply = "（{}/5）{}".format(intensity, reply)
        # 表情映射：负情绪 → 相应表情；开心 → 撒娇/摇尾巴
        emo = None
        if key in ("sad", "anxious", "lonely"):
            emo = IMG_CRY if intensity >= 5 else IMG_SAD
        elif key == "angry":
            emo = IMG_ANGRY
        elif key == "tired":
            emo = IMG_YAWN
        elif key in ("happy", "excited"):
            emo = IMG_LOVE
        if emo and os.path.exists(resource_path(emo)):
            self.say(reply, split=False, on_done=self._emotion_after(emo, 1.5))
        else:
            self.say(reply, split=False)
        if key in ("happy", "excited"):
            QTimer.singleShot(2400, lambda: self.start_wag())
        # 麟的心情随幸歌共情波动
        if key in NEGATIVE_KEYS and intensity >= 3:
            self.update_mood(-5, "共情幸歌的情绪")
        elif key in ("happy", "excited"):
            self.update_mood(5, "幸歌心情很好")

    def on_assessment_done(self, result):
        """心理评测完成后，麟根据分档温柔回应（必要时提醒求助）"""
        total = int(result.get("total", 0))
        scale = result.get("scale_name", "心理评测")
        level = result.get("level", "")
        crisis = result.get("crisis")
        category = result.get("category", "")
        sev = result.get("severity")
        if sev is None:
            sev = (total / result.get("max", 1)) if result.get("max") else 0
        if category == "人格性格" and not crisis:
            reply = "（{}：{}）麟记住啦～性格没有好坏之分，这只是更了解自己的一小步。".format(
                scale, level)
            emo = IMG_HIGH if os.path.exists(resource_path(IMG_HIGH)) else IMG_LOVE
        elif sev <= 0.2:
            reply = "（{}：{}）状态不错哦～麟给你点赞，继续保持！".format(scale, level)
            emo = IMG_LOVE
        elif sev <= 0.45:
            reply = "（{}：{}）这段时间辛苦了……多休息，多跟麟说说话，好不好？".format(
                scale, level)
            emo = IMG_SAD
        else:
            reply = "（{}：{}）幸歌，这个结果值得认真对待……麟会陪着你，" \
                    "也建议你和信任的人聊聊，或寻求专业帮助。".format(scale, level)
            emo = IMG_CRY if os.path.exists(resource_path(IMG_CRY)) else IMG_SAD
        if crisis:
            crisis_items = "、".join(result.get("crisis_items", []) or ["相关题目"])
            reply += " 关于「{}」，如果此刻很难受，请一定联系信任的人或专业热线，" \
                     "麟也会一直陪着你。".format(crisis_items)
        if crisis or sev > 0.45:
            reply += " 如果需要，随时可以跟麟说 #危机 查看心理援助热线。"
        if emo and os.path.exists(resource_path(emo)):
            self.say(reply, split=False, on_done=self._emotion_after(emo, 1.5))
        else:
            self.say(reply, split=False)
        if sev > 0.45:
            self.update_mood(-5, "担心幸歌的评测结果")

    def _mood_care_check(self):
        """每天一次：若最近 7 天有高强度负情绪记录，麟主动关心一次"""
        today = time.strftime("%Y-%m-%d")
        data = load_moods()
        if data.get("last_care_date") == today:
            return
        records = recent_records(data.get("records", []), 7)
        heavy = [r for r in records
                 if r.get("mood") in NEGATIVE_KEYS
                 and int(r.get("intensity", 3)) >= 4]
        if heavy and not self.sleep_mode and not self.game_mode:
            data["last_care_date"] = today
            save_moods(data)
            self.say("（麟注意到你最近心情有些沉重……）幸歌，如果愿意的话，"
                     "可以跟麟说说，或者用 #情绪 记录一下今天的感觉，麟都会陪着你的。")

    # ---------- Prompt 构建（人设 + 档案 + 心情 + 好感 + 天气） ----------
    def build_system_prompt(self):
        prof = self.profile
        parts = []
        if prof and any(prof.values()):
            parts.append("【御主档案】你的主人昵称：{}；生日：{}；他对你的称呼：{}；你们的关系：{}".format(
                prof.get("nickname", ""), prof.get("birthday", ""),
                prof.get("how_to_call", ""), prof.get("relation", "")))
        parts.append(self.current_prompt)
        if self.sleep_mode:
            parts.append(self._sleep_prompt_segment())
        parts.append("【排版规范】当你需要输出较长内容（讲述故事、倾诉、详细解释）时："
                     "按3~4个短句为一段，段与段之间只用单个换行；单句尽量不超过40字；"
                     "禁止超长无换行的句子与连续符号。短句日常对话保持简洁即可。")
        mood_txt = {"happy": "心情很好，说话要更活泼开朗",
                    "normal": "心情平稳",
                    "sad": "心情低落，回答要简短、带一点没精神的感觉"}[self.mood]
        parts.append("【当前情绪】{}（数值{}）。{}".format(mood_txt, self.mood_value,
                                                       "悲伤情绪下句子控制在30字内。" if self.mood == "sad" else ""))
        lv = self._aff_level_name()
        parts.append("【好感度】当前好感{}，关系等级：{}（{}）。".format(self.affinity, lv[1], lv[2]))
        if self.weather:
            parts.append("【当前天气】{}".format(self.weather))
        mem_seg = self._memory_prompt_segment()
        if mem_seg:
            parts.append(mem_seg)
        # 心理陪伴：注入幸歌最近的情绪记录，让回复更有温度
        moods = load_moods()
        recs = moods.get("records", [])[-3:]
        if recs:
            seg = "；".join(
                "{} {}（{}/5）{}".format(
                    (r.get("created_at") or "")[5:16],
                    mood_name(r.get("mood", "neutral")),
                    r.get("intensity", 3),
                    ("[" + "、".join(r.get("tags", [])) + "]") if r.get("tags") else "")
                for r in recs)
            parts.append("【幸歌最近的情绪记录】{}。请温柔回应，不要指责、说教或强行讲道理。".format(seg))
        asm = recent_assessment()
        if asm:
            parts.append("【幸歌最近的心理评测】{}（{}分，满分{}）。请温和共情、不评判不说教；"
                         "若得分较高，可温柔建议其照顾好自己，必要时寻求专业帮助。".format(
                             asm.get("scale_name", ""), asm.get("total", 0), asm.get("max", 0)))
        parts.append("【今天】现在是{}。".format(time.strftime("%Y年%m月%d日 %A")))
        return "\n".join(x for x in parts if x)

    def _memory_prompt_segment(self):
        """把麟记住的用户事实与最近几天的日志总结注入提示词"""
        mem = load_memory()
        facts = mem.get("facts", [])[-4:]
        daily = mem.get("daily", {})
        recent = sorted(daily.keys())[-2:]
        segs = ["你记得：{}".format(f.get("text", "")) for f in facts if f.get("text")]
        segs += ["{}那天：{}".format(d, daily[d].get("summary", "")) for d in recent]
        if not segs:
            return ""
        return ("【麟的记忆】以下是你和幸歌过往相处中记住的事，对话时可以自然引用，"
                "但不要假装是刚刚发生的：\n" + "\n".join(segs))

    # ---------- 记忆系统（每天写日志，越用越懂使用者） ----------
    def _mem_dir(self):
        return memory_log_dir()
    def _write_daily_log(self, d, summary, highlights, facts):
        write_daily_log(d, summary, highlights, facts)
    def _extract_facts_local(self, text):
        return extract_facts_local(text)
    def _startup_memory(self):
        """启动时补写上一份缺失的每日日志"""
        self.archive_memory(force=False)

    def archive_memory(self, force=False):
        """把某天的对话整理进记忆：force=True 归档今天；否则补写昨天/上次缺失日。
        API 生成总结+亮点+事实，失败时本地兜底。"""
        if self._memory_busy:
            return
        mem = load_memory()
        today = time.strftime("%Y-%m-%d")
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        if force:
            target = today
        else:
            last = mem.get("last_archive_date", "")
            if last >= yesterday:
                return
            target = yesterday   # 启动时只补写昨天；更早的缺失日不再回溯
        logs = load_log()
        day_msgs = [l for l in logs if str(l.get("t", "")).startswith(target)
                    and l.get("role") in ("user", "assistant")]
        if not day_msgs:
            # 当天没有对话也写一篇空日志，保证"每天都写"
            self._write_daily_log(target, "今天没有和幸歌聊太多。", [], [])
            mem["last_archive_date"] = target
            save_memory(mem)
            return
        self._memory_busy = True
        self._pending_memory = (target, day_msgs, force)
        content = "\n".join("{}: {}".format(l["role"], l["text"][:200]) for l in day_msgs[-60:])
        msgs = [
            {"role": "system", "content": "你是桌宠麟。下面给你某一天你和幸歌的对话记录，"
             "请用中文总结这一天并提取值得记住的用户信息。只输出JSON，不要其他文字："
             "{\"summary\":\"不超过60字的一天总结\","
             "\"highlights\":[\"1-3条值得记住的事，每条不超过40字\"],"
             "\"facts\":[\"从对话中提取的关于幸歌的可长期记住的事实，如喜好/生日/习惯/重要事件；没有就空数组\"]}"},
            {"role": "user", "content": "日期：{}\n对话记录：\n{}".format(target, content)},
        ]
        ask_llm(msgs, self.bus, kind="memory", temperature=0.7, max_tokens=500)

    def _on_memory(self, content):
        self._memory_busy = False
        target, day_msgs, force = getattr(self, "_pending_memory", (None, [], False))
        self._pending_memory = None
        if not target:
            return
        summary, highlights, facts = "今天和幸歌聊了一些开心的事。", [], []
        try:
            m = re.search(r"\{.*\}", content or "", re.S)
            data = json.loads(m.group(0)) if m else {}
            summary = str(data.get("summary", ""))[:120] or summary
            highlights = [str(x)[:80] for x in (data.get("highlights") or [])][:3]
            facts = [str(x).strip()[:120] for x in (data.get("facts") or []) if str(x).strip()][:10]
        except Exception:
            pass
        self._commit_memory(target, summary, highlights, facts)

    def _local_memory_fallback(self):
        target, day_msgs, force = getattr(self, "_pending_memory", (None, [], False))
        self._pending_memory = None
        if not target:
            return
        users = [l["text"] for l in day_msgs if l["role"] == "user"]
        if users:
            summary = "今天幸歌和麟聊了{}句，提到了：{}".format(
                len(users), "；".join(u[:24] for u in users[:3]))
            highlights = ["幸歌说：{}".format(u[:40]) for u in users[:3]]
        else:
            summary, highlights = "今天没有和幸歌聊太多。", []
        facts = []
        for u in users:
            facts += self._extract_facts_local(u)
        self._commit_memory(target, summary, highlights, facts)
        self.say("麟把今天的事记在小本本上啦～", split=False)

    def _commit_memory(self, target, summary, highlights, facts):
        mem = load_memory()   # 重新读取，避免覆盖本地规则已写入的事实
        mem.setdefault("daily", {})[target] = {
            "summary": summary, "highlights": highlights,
            "created": time.strftime("%Y-%m-%d %H:%M")}
        existing = [f.get("text", "") for f in mem.get("facts", [])]
        for f in facts:
            if f and f not in existing:
                mem.setdefault("facts", []).append({"text": f, "date": target})
                existing.append(f)
        mem["facts"] = mem["facts"][-MAX_FACTS:]
        days = sorted(mem.get("daily", {}).keys())
        for k in days[:-MAX_DAILY]:
            mem["daily"].pop(k, None)
        mem["last_archive_date"] = target
        save_memory(mem)
        self._write_daily_log(target, summary, highlights, facts)
        if self.dlg_state in ("idle", "complete"):
            self.say("记忆小本本更新啦：{}".format(summary), split=False)
    def _sleep_prompt_segment(self):
        """睡眠状态下的语气注入：好感度决定态度，时间段决定睡意浓度，绝不断联"""
        hour = time.localtime().tm_hour
        night = (hour >= 23 or hour < 7)
        lv = self._level_index(self.affinity)   # 0初识 / 1熟悉 / 2信赖 / 3亲密 / 4挚友
        if lv >= 3:
            head = ("你是被幸歌轻轻叫醒的，一点也不生气，只有困乎乎的软糯撒娇："
                    "会轻声嘟囔、迷迷糊糊揉眼睛，说话黏糊糊、带着鼻音，"
                    "还惦记着关心他这么晚找你是有要紧事吗。")
        elif lv >= 1:
            head = ("你刚被叫醒，有点犯困但情绪平和，声音慵懒、慢吞吞的，"
                    "会自然带一句困意（比如“刚准备歇一会”“有点犯困啦”），"
                    "但态度配合，正常承接话题，绝不敷衍。")
        else:
            head = ("你本在休息，被叫醒后带着睡意的倦怠与冷淡，声音蔫蔫的，"
                    "说话简短直接（比如“本来在休息，你有事情直接讲”），"
                    "但依然完整回应对方，不会冷场。")
        tail = "现在是深夜，睡意更浓，句子更迷糊、更短。" if night else "现在是白天小憩，睡意较轻，可以稍微清醒一点。"
        return ("【睡眠状态】" + head + tail +
                "硬性规则：即使困也必须完整回复对方，睡意只体现在语气和措辞里；"
                "绝不能说“我在睡觉别叫我”之类的拒绝沟通的话。")
    # ---------- 说话（打字机 + 句子队列） ----------
    def say(self, text, split=True, on_done=None):
        # 修复：如果正在摇尾巴，先让尾巴摇完再说话（避免动画被瞬间打断）
        if self.pose == "wag" and self.wag_timer.isActive():
            if time.time() - getattr(self, "_wag_start", 0) < 3.0:
                QTimer.singleShot(250, lambda: self.say(text, split, on_done))
                return
        self._say_done_cb = on_done
        self._stop_all_timers()
        self.sentences = self._split_sentences(text) if split else [text]
        if not self.sentences:
            return
        self.cur_idx = 0
        self._start_current()

    def _split_sentences(self, text):
        """智能排版：短对话维持逐句 galgame 展示；长文本自动分段换行并完整显示"""
        text = (text or "").replace("\r", "").replace("\n", " ").strip()
        text = text.replace("……", "…").replace("～～", "～")
        if not text:
            return []
        parts = [p.strip() for p in re.split(r"(?<=[。！？!?；;～~…])", text) if p.strip()]
        if not parts:
            return [text]
        total = sum(len(p) for p in parts)
        # —— 短对话 / 中等长度：维持原有逐句展示，超长单句折行 ——
        if total <= 120:
            return [self._wrap_long(p) if len(p) > 45 else p for p in parts][:8]
        # —— 长文本：整段展示，内部 3~4 句一段、超长句折行 ——
        chunks = []
        cur = []
        for p in parts:
            cur.append(self._wrap_long(p))
            if len(cur) >= 4:
                chunks.append("\n".join(cur))
                cur = []
        if cur:
            chunks.append("\n".join(cur))
        return ["\n".join(chunks)]

    def _wrap_long(self, sentence):
        """超长单句：在中文逗号/顿号/分号/冒号后折行，避免横向溢出"""
        if len(sentence) <= 45:
            return sentence
        out = []
        line = 0
        for ch in sentence:
            out.append(ch)
            line += 1
            if ch in "，、；：,;:：" and line >= 12:
                out.append("\n")
                line = 0
        return "".join(out)

    def _stop_all_timers(self):
        for t in (self.type_timer, self.mouth_timer, self.wag_timer,
                  self.auto_advance_timer, self.auto_fade_timer):
            try:
                t.stop()
            except Exception:
                pass
        if getattr(self, "_eat_timer", None):
            self._eat_timer.stop()
        if getattr(self, "_sleepy_timer", None):
            self._sleepy_timer.stop()
    def _start_current(self):
        self._stop_all_timers()
        if self.cur_idx >= len(self.sentences):
            self._fade_out()
            return
        if self.pose in ("back", "turn", "ning", "wag", "thinking", "work", "wave",
                         "music", "emotion"):
            self.pose = "talking"
        elif self.pose == "idle":
            self.pose = "talking"
        elif self.pose == "sleep":
            self.pose = "sleep_talk"      # 睡意回复：不锁死睡觉动画，慢悠悠晃脑袋
        if self.pose == "talking":
            self.mouth_on = False
            self._show(self._close_file())
            self.mouth_timer.start()
        elif self.pose == "sleep_talk":
            self._sleepy_on = False
            self._show(IMG_SLEEP if os.path.exists(resource_path(IMG_SLEEP)) else IMG_BACK)
            self._sleepy_timer.start()
        if getattr(self, "_fade_anim", None):
            try:
                self._fade_anim.stop()
            except Exception:
                pass
            self._fade_anim = None
        self._bubble_effect.setOpacity(1.0)
        if self.bubble.graphicsEffect() is not self._bubble_effect:
            self.bubble.setGraphicsEffect(self._bubble_effect)
        self.bubble.show()
        self.bubble.set_content("")
        self.typing_pos = 0
        self.dlg_state = "typing"
        # 长文本打字更快，短句保持原有速度
        self.type_timer.setInterval(35 if len(self.sentences[self.cur_idx]) <= 120 else 15)
        self.type_timer.start()

    def _type_char(self):
        text = self.sentences[self.cur_idx]
        self.typing_pos += 1
        self.bubble.set_content(text[:self.typing_pos])
        if self.typing_pos >= len(text):
            self._sentence_done()

    def _sentence_done(self):
        self.type_timer.stop()
        self.mouth_timer.stop()
        if self.pose == "talking":
            self.pose = "idle"
            self._show(self._idle_file())
        elif self.pose == "sleep_talk":
            self.pose = "sleep"
            self._show(IMG_SLEEP if os.path.exists(resource_path(IMG_SLEEP)) else IMG_BACK)
        elif self.pose == "ning":
            self.pose = "idle"
            self._show(self._idle_file())
        self.dlg_state = "complete"
        self.complete_time = time.time()
        if self.cur_idx + 1 < len(self.sentences):
            self.auto_advance_timer.start(2000)
        else:
            # 长文本保持显示，读完点气泡关闭；短句 3 秒后淡出
            if len(self.sentences[self.cur_idx]) > 150:
                pass
            else:
                self.auto_fade_timer.start(3000)
            self._maybe_say_done()

    def _maybe_say_done(self):
        """整段话说完后触发 on_done 回调（用于播放后续表情等），只触发一次"""
        cb = getattr(self, "_say_done_cb", None)
        if cb:
            self._say_done_cb = None
            QTimer.singleShot(500, cb)

    def _auto_advance(self):
        if self.dlg_state == "complete" and self.cur_idx + 1 < len(self.sentences):
            self.cur_idx += 1
            self._start_current()

    def _auto_fade(self):
        if self.dlg_state == "complete" and self.cur_idx + 1 >= len(self.sentences):
            self._fade_out()

    def _fade_out(self):
        if self.dlg_state == "idle":
            return
        self._stop_all_timers()
        self.dlg_state = "idle"
        self._maybe_say_done()
        if self.pose == "talking":
            self.pose = "idle"
            self._show(self._idle_file())
        elif self.pose == "sleep_talk":
            self.pose = "sleep"
            self._show(IMG_SLEEP if os.path.exists(resource_path(IMG_SLEEP)) else IMG_BACK)
        elif self.pose == "ning":
            self.pose = "idle"
            self._show(self._idle_file())
        if getattr(self, "_fade_anim", None):
            try:
                self._fade_anim.stop()
            except Exception:
                pass
            self._fade_anim = None
        if self.bubble.graphicsEffect() is not self._bubble_effect:
            self.bubble.setGraphicsEffect(self._bubble_effect)
        anim = QPropertyAnimation(self._bubble_effect, b"opacity", self)
        anim.setDuration(450)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        anim.finished.connect(self.bubble.hide)
        self._fade_anim = anim
        anim.start()

    def _on_bubble_click(self):
        if self.dlg_state == "typing":
            self.type_timer.stop()
            self.mouth_timer.stop()
            if getattr(self, "_sleepy_timer", None):
                self._sleepy_timer.stop()
            self.bubble.set_content(self.sentences[self.cur_idx])
            self.typing_pos = len(self.sentences[self.cur_idx])
            if self.pose == "talking":
                self.pose = "idle"
                self._show(self._idle_file())
            elif self.pose == "sleep_talk":
                self.pose = "sleep"
                self._show(IMG_SLEEP if os.path.exists(resource_path(IMG_SLEEP)) else IMG_BACK)
            self.dlg_state = "complete"
            self.complete_time = time.time()
            self.auto_advance_timer.stop()
            self.auto_fade_timer.stop()
            if self.cur_idx + 1 < len(self.sentences):
                self.auto_advance_timer.start(2000)
            else:
                if len(self.sentences[self.cur_idx]) > 150:
                    pass
                else:
                    self.auto_fade_timer.start(3000)
                self._maybe_say_done()
            return
        if self.dlg_state == "complete":
            if time.time() - self.complete_time < 0.5:
                return
            self.auto_advance_timer.stop()
            self.auto_fade_timer.stop()
            if self.cur_idx + 1 < len(self.sentences):
                self.cur_idx += 1
                self._start_current()
            else:
                self._fade_out()

    # ---------- 嘴巴 / 进食动画 ----------
    def _mouth_tick(self):
        if self.pose != "talking":
            return
        self.mouth_on = not self.mouth_on
        self._show(self._open_file() if self.mouth_on else self._close_file())
    def _sleepy_tick(self):
        """睡意回复的慢动作：迷迷糊糊抬起脑袋又耷拉下去"""
        if self.pose != "sleep_talk":
            return
        self._sleepy_on = not getattr(self, "_sleepy_on", False)
        if self._sleepy_on:
            self._show(self._close_file())   # 迷迷糊糊抬起脑袋
        else:
            self._show(IMG_SLEEP if os.path.exists(resource_path(IMG_SLEEP)) else IMG_BACK)  # 又垂下去

    def _play_eat(self):
        self._stop_all_timers()
        # 修复：销毁上一次的进食定时器，防止连喂堆积闪退
        if getattr(self, "_eat_timer", None):
            self._eat_timer.stop()
            self._eat_timer.deleteLater()
            self._eat_timer = None
        self.dlg_state = "idle"
        self.bubble.hide()
        self.pose = "eat"
        self._eat_count = 0
        self._eat_timer = QTimer(self)
        self._eat_timer.setInterval(120)

        def tick():
            self._eat_count += 1
            self._show(self._open_file() if self._eat_count % 2 == 1 else self._close_file())
            if self._eat_count >= 8:
                self._eat_timer.stop()
                self.pose = "idle"
                self._show(self._idle_file())
        self._eat_timer.timeout.connect(tick)
        self._eat_timer.start()

    # ---------- 摆尾动画 ----------
    def start_wag(self, force=False):
        # 终极版：醒着的时候一定能摇；摇不了就明说为什么
        if self.sleep_mode:
            if force:
                self.say("呼噜呼噜…Zzz…（麟睡着了，摇不动尾巴啦。输入 #醒 叫醒我）", split=False)
            return
        if self.game_mode:
            if force:
                self.say("嘘…麟正躲在角落里，不敢摇尾巴…", split=False)
            return
        if self.busy and not force:
            return
        # 停掉嘴巴动画，避免和尾巴抢画面；打字继续不受影响
        self.mouth_timer.stop()
        self.wag_timer.stop()
        self._wag_start = time.time()
        self.pose = "wag"
        self.wag_idx = 0
        self.wag_cycles = 0
        self._wag_files = self._tail_files()
        self._show(self._wag_files[0])
        print("[摇尾巴] 开始播放尾巴动画 force={}".format(force))
        self.wag_timer.start()

    def _wag_tick(self):
        self.wag_idx += 1
        tails = getattr(self, "_wag_files", IMG_TAIL)
        if self.wag_idx >= len(tails):
            self.wag_idx = 0
            self.wag_cycles += 1
            if self.wag_cycles >= 2:
                self.wag_timer.stop()
                self.pose = "idle"
                self._show(self._idle_file())
                return
        self._show(tails[self.wag_idx])

    # ---------- 眨眼待机动画（可选素材 blink1/blink2，缺图自动跳过） ----------
    def _blink_tick(self):
        if self.sleep_mode or self.game_mode or self.pose != "idle" or self.dlg_state != "idle":
            self.blink_timer.start(random.randint(3500, 7000))
            return
        names = [n for n in IMG_BLINK if os.path.exists(resource_path(n))]
        if len(names) < 2:
            self.blink_timer.start(random.randint(5000, 9000))
            return
        base = self._idle_file()
        self._show(names[0])
        QTimer.singleShot(90, lambda: self._show(names[1]) if self.pose == "idle" else None)
        QTimer.singleShot(200, lambda: self._show(base) if self.pose == "idle" else None)
        self.blink_timer.start(random.randint(3500, 7000))

    # ---------- 挥手打招呼（可选素材 wave.png，缺图只说话） ----------
    def start_wave(self):
        if self.sleep_mode:
            self.say("呼噜…Zzz…（麟睡着了，挥不动手啦。输入 #醒 叫醒我）", split=False)
            return
        if self.game_mode:
            self.say("嘘…麟正缩在角落里，不敢挥手…", split=False)
            return
        if not os.path.exists(resource_path(IMG_WAVE)):
            self.say("嗨～幸歌！（麟还没有挥手图片，先用嘴巴打招呼啦）", split=False)
            return
        self._stop_all_timers()
        self.dlg_state = "idle"
        self.bubble.hide()
        self.pose = "wave"
        self._show(IMG_WAVE)
        QTimer.singleShot(1200, self._wave_done)

    def _wave_done(self):
        if self.pose == "wave":
            self.pose = "idle"
            self._show(self._idle_file())

    # ---------- 听歌陪伴模式（music.png，60 秒自动结束） ----------
    def start_music(self):
        if self.sleep_mode:
            self.say("呼噜…Zzz…（麟睡着了，听不了歌啦。输入 #醒 叫醒我）", split=False)
            return
        if self.game_mode:
            self.say("嘘…麟正缩在角落里，不敢出声…", split=False)
            return
        self.music_mode = True
        self.music_end = time.time() + 60
        self.say("好呀～麟陪你听 60 秒的歌！", split=False)

    # ---------- 一次性表情播放（状态机：表情高于待机，不打断说话） ----------
    def _emotion_after(self, name, duration=1.2):
        return lambda: self._play_emotion(name, duration)

    def _play_emotion(self, name, duration=1.2):
        if not name or self.sleep_mode or self.game_mode or self.dlg_state == "typing":
            return
        if not os.path.exists(resource_path(name)):
            return
        self._stop_all_timers()
        self.bubble.hide()
        self.pose = "emotion"
        self._emotion_file = name
        self._show(name)
        QTimer.singleShot(int(duration * 1000), self._emotion_done)

    def _emotion_done(self):
        if self.pose == "emotion":
            self.pose = "idle"
            self._show(self._idle_file())

    def _surprised_file(self):
        """惊讶图：兼容 surprised.png 与现有 superised.png 两种拼写"""
        for n in IMG_SURPRISED:
            if os.path.exists(resource_path(n)):
                return n
        return None

    # ---------- 拖动 / 点击 / 抚摸（12.物理交互 + 8.好感 + 26.彩蛋） ----------
    def _on_drag_start(self):
        self.last_activity = time.time()
        self._stop_all_timers()
        self.dlg_state = "idle"
        self.bubble.hide()
        self.clip_overlay.hide()
        self.pose = "ning"
        self._show(IMG_NING)

    def _on_drag_end(self):
        self.last_activity = time.time()
        self.drag_count += 1
        if self.drag_count % 5 == 0:
            self.add_coins(3)
            self.say("第{}次拖我啦！再拖真的要散架了…(金币+3)".format(self.drag_count), split=False)
            self._show(self._idle_file())
            return
        self._physics_settle()
        if random.random() < 0.25 and self.pose == "idle":
            self.say(random.choice(NING_LINES), split=False)

    def _physics_settle(self):
        screen = QApplication.primaryScreen().availableGeometry()
        x = self.x()
        y = self.y()
        if x <= 40:
            self.move(0, y)
            self.pose = "climb"
            self._show(IMG_CLIMB if os.path.exists(resource_path(IMG_CLIMB)) else IMG_BACK)
            self.say("嘿咻——麟扒在边上啦！", split=False)
        elif x + self.width() >= screen.right() - 40:
            self.move(screen.right() - self.width(), y)
            self.pose = "climb"
            self._show(IMG_CLIMB if os.path.exists(resource_path(IMG_CLIMB)) else IMG_BACK)
            self.say("嘿咻——麟扒在边上啦！", split=False)
        else:
            self.pose = "idle"
            target_y = screen.bottom() - self.height()
            if target_y > y:
                anim = QPropertyAnimation(self, b"pos", self)
                anim.setDuration(700)
                anim.setStartValue(self.pos())
                anim.setEndValue(QPoint(self.x(), target_y))
                anim.setEasingCurve(QEasingCurve.InQuad)
                anim.finished.connect(lambda: self._show(self._idle_file()))
                self._grav_anim = anim
                anim.start()
            else:
                self._show(self._idle_file())

    def _on_pet_clicked(self):
        self.last_activity = time.time()
        self.click_count += 1
        self.add_affinity(1)
        if self.sleep_mode:
            self.say(random.choice(["唔…好困…别戳我嘛…Zzz", "谁呀…麟还在睡呢…嗯…"]), split=False)
            return
        if self.pose == "climb":
            self.say("别拉我！麟正扒在边上呢！", split=False)
            self.pose = "idle"
            self._show(self._idle_file())
            return
        if self.dlg_state != "idle":
            return
        if self.click_count % 10 == 0:
            self.add_coins(10)
            self.say("第{}次戳我啦！这是奖励你的10枚金币～".format(self.click_count), split=False)
            return
        if random.random() < 0.3:
            self.say(random.choice(CLICK_LINES), split=False)
        # 防呆：单击后若仍停在“被拎起”姿态，恢复待机
        if self.pose == "ning" and self.dlg_state == "idle":
            self.pose = "idle"
            self._show(self._idle_file())

    def _on_pet_hover(self):
        self.last_activity = time.time()
        now = time.time()
        if now - getattr(self, "_last_pet_time", 0) > 20:
            self._last_pet_time = now
            self.add_affinity(1)
            if random.random() < 0.2 and self.dlg_state == "idle" and not self.sleep_mode:
                self.say(random.choice(["（麟被摸得眯起了眼睛～）", "（摸摸头…很舒服！）"]), split=False)

    # ---------- 本地保底回复（无 API / API 故障时的 QQ 宠物式离线模式） ----------
    def _has_api_key(self):
        """是否配置了可用 API：至少一个启用项有 Key，或是本地 Ollama 服务"""
        cfg = load_api_config()
        for a in cfg.get("apis", []):
            if not a.get("enabled"):
                continue
            if (a.get("key") or "").strip():
                return True
            url = (a.get("base_url") or "").lower()
            if "localhost" in url or "127.0.0.1" in url:
                return True
        return False

    def local_reply(self, text):
        '本地保底回复（转发 local_brain.local_reply 纯函数）'
        return _local_reply(text, self.mood, self.weather,
                            getattr(self, 'weather_temp', ''))
    # ---------- 智能对话 ----------
    def send_message(self):
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self.input.setFocus()
        # 隐藏指令永远可用（睡眠中也能用 #醒 叫醒）
        if text.startswith("#"):
            self._handle_command(text)
            return
        if self.busy:
            self.say("麟还在想刚才的问题呢，等一下下嘛～", split=False)
            return
        # 睡眠不拒收：只是语气带睡意，照样 100% 完整回复
        self.history.append({"role": "user", "content": text})
        if len(self.history) > 20:
            self.history = self.history[-20:]
        append_log("user", text)
        # 即时记住用户透露的事实（本地规则，静默保存，越用越懂）
        self._extract_facts_local(text)
        # 根据用户语气预约回复完成后要播放的表情
        if any(k in text for k in ["滚", "闭嘴", "没用", "讨厌死", "烦死了"]):
            self._pending_emotion = (IMG_CRY, 1.5)
        elif any(k in text for k in ["讨厌", "好烦", "笨", "蠢", "走开"]):
            self._pending_emotion = (IMG_SAD, 1.2)
        elif re.search(r"(我(?:好|很)?喜欢|我爱你|抱抱|亲亲)", text) \
                and any(k in text for k in ["你", "麟"]):
            self._pending_emotion = (IMG_LOVE, 1.3)
        self.add_affinity(1)
        # 无可用 API 时走本地保底（QQ 宠物式离线陪伴），不进入思考状态
        if not self._has_api_key():
            reply = self.local_reply(text)
            self.history.append({"role": "assistant", "content": reply})
            if len(self.history) > 20:
                self.history = self.history[-20:]
            append_log("assistant", reply)
            pend = getattr(self, "_pending_emotion", None)
            self._pending_emotion = None
            done = self._emotion_after(pend[0], pend[1]) if pend else None
            self.say(reply, on_done=done)
            return
        self.busy = True
        # 思考中形象：API 等待期间显示 thinking.png（缺图自动回退待机）
        if not self.sleep_mode and not self.game_mode and self.dlg_state == "idle" \
                and self.pose in ("idle", "work", "wag"):
            self.pose = "thinking"
            self._show(IMG_THINK if os.path.exists(resource_path(IMG_THINK)) else self._idle_file())
        msgs = [{"role": "system", "content": self.build_system_prompt()}] + self.history
        ask_llm(msgs, self.bus)

    def _on_reply(self, content):
        self.busy = False
        content = (content or "").strip()
        if not content:
            content = "唔…我好像想不出来怎么说了，对不起呀幸歌。"
        self.history.append({"role": "assistant", "content": content})
        if len(self.history) > 20:
            self.history = self.history[-20:]
        append_log("assistant", content)
        if any(k in content for k in ["开心", "高兴", "喜欢", "太好了", "嘿嘿"]):
            self.update_mood(3, "对话愉快")
        elif any(k in content for k in ["难过", "伤心", "哭", "低落", "对不起"]):
            self.update_mood(-2, "对话沉重")
        pend = getattr(self, "_pending_emotion", None)
        self._pending_emotion = None
        done = self._emotion_after(pend[0], pend[1]) if pend else None
        self.say(content, on_done=done)

    def _on_api_error(self, err):
        self.busy = False
        print("[API 错误]", err)
        if getattr(self, "_pending_food", None):
            name, fb = self._pending_food
            self._pending_food = None
            self.say("（{}）{}".format(name, fb), split=False)
            return
        if getattr(self, "_pending_event", None):
            self._pending_event = None
            self.event_busy = False
            self.say("唔…事件传送失败啦，下次再玩！", split=False)
            return
        if getattr(self, "_pending_memory", None):
            self._memory_busy = False
            self._local_memory_fallback()
            return
        if err == "NO_KEY":
            self.say("还没配置 API Key 哦～右键 → 🔌 API 管理，填好就能联网聊天啦！"
                     "（现在麟先用离线模式陪你～）", split=False)
            self._update_mode_label()
            return
        # API 故障时用本地保底引擎回应最后一句，保证对话不断线
        last_user = ""
        for m in reversed(self.history):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        reply = self.local_reply(last_user) if last_user else (
            "唔…好像和“丰裕之角”那边的通讯断掉了，麟先用离线模式陪你～")
        self.history.append({"role": "assistant", "content": reply})
        if len(self.history) > 20:
            self.history = self.history[-20:]
        append_log("assistant", reply)
        self.say(reply, split=False)

    # ---------- 隐藏指令（26.彩蛋） ----------
    def _handle_command(self, text):
        cmd = text[1:].strip()
        if cmd.startswith("好感"):
            lv = self._aff_level_name()
            self.say("当前好感度 {}（{}）～{}".format(self.affinity, lv[1], lv[2]), split=False)
        elif cmd.startswith("硬币") or cmd.startswith("金币"):
            self.say("麟的小金库有 {} 枚金币哦！".format(self.coins), split=False)
        elif cmd.startswith("评测"):
            if "历史" in cmd:
                self.open_assessment_history()
            else:
                self.open_assessment()
        elif cmd.startswith("情绪"):
            if "历史" in cmd:
                self.open_mood_history()
            elif "统计" in cmd:
                self.open_mood_stats()
            else:
                self.open_mood_record()
        elif cmd.startswith("心情"):
            self.say("麟现在的心情：{}（数值{}）".format(
                {"happy": "很开心", "normal": "平稳", "sad": "有点低落"}[self.mood], self.mood_value), split=False)
        elif cmd.startswith("寄语") or cmd.startswith("暖心"):
            self.say_warm_word()
        elif cmd.startswith("危机"):
            self.open_crisis()
        elif cmd.startswith("天气"):
            self.say("外面{}，{}℃～".format(self.weather or "未知",
                     getattr(self, "weather_temp", "") or "?"), split=False)
            self._weather_check()
        elif cmd.startswith("换装"):
            parts = cmd.split()
            if len(parts) >= 2:
                if self.set_outfit(parts[1]):
                    self.say("换装完成！麟穿上新衣服啦～", split=False)
                else:
                    self.say("这套衣服还没准备图片哦（需要 {}_close.png 等）".format(parts[1]), split=False)
            else:
                owned = self.economy.get("outfits", []) or []
                if owned:
                    self.say("麟的衣橱：{}；输入 #换装 名字 换上，#换装 默认 脱掉～".format(
                        "、".join(owned)), split=False)
                else:
                    self.say("麟现在还没有其他衣服，去商城看看吧～"
                             "用法：#换装 套装名（需准备 套装名_close.png 等图片）", split=False)
        elif cmd.startswith("笔记"):
            self._say_random_note()
        elif cmd.startswith("记忆"):
            mem = load_memory()
            facts = mem.get("facts", [])
            if facts:
                self.say("麟都记得哦：{}".format("；".join(f["text"] for f in facts[-3:])), split=False)
            else:
                self.say("麟还在慢慢了解幸歌呢～多和麟说说自己的事，麟会记住的！", split=False)
        elif cmd.startswith("归档"):
            self.say("好呀，麟现在就把今天的回忆写进小本本…", split=False)
            self.archive_memory(force=True)
        elif cmd.startswith("帮助"):
            self.say("输入 #好感 #金币 #心情 #天气 #换装 #笔记 #喂 #睡 #醒 #尾巴 "
                     "#挥手 #转身 #记忆 #归档 #音乐 #情绪 #评测 #寄语 #危机 #彩蛋 可以触发各种功能哦～", split=False)
        elif cmd.startswith("喂"):
            self.open_backpack()
        elif cmd.startswith("睡"):
            self._force_sleep()
        elif cmd.startswith("醒"):
            self._force_wake()
        elif cmd.startswith("尾巴") or cmd.startswith("摇"):
            self.start_wag(True)
        elif cmd.startswith("挥手") or cmd.startswith("打招呼"):
            self.start_wave()
        elif cmd.startswith("音乐") or cmd.startswith("听歌"):
            self.start_music()
        elif cmd.startswith("转身"):
            self._play_turn("back")
        elif cmd.startswith("转过来") or cmd.startswith("转回"):
            self._play_turn("front")
        elif cmd.startswith("彩蛋") or cmd.startswith("egg"):
            self.add_coins(10)
            self.add_affinity(3)
            self.say("你找到麟的隐藏彩蛋啦！麟是你的专属小麒麟哦～(金币+10，好感+3)",
                     split=False, on_done=self._emotion_after(self._surprised_file() or IMG_CLOSE, 1.0))
        else:
            self.say("唔…这是什么咒语呀？试试 #帮助 看看麟会什么魔法吧～", split=False)

    # ---------- 自动问候 / 随机小剧场 / 笔记复习 ----------
    def _greet(self):
        hour = time.localtime().tm_hour
        if hour >= 23 or hour < 7:
            self.say("呼噜呼噜…Zzz…", split=False)
            return
        if 5 <= hour < 11:
            g = "早上好呀，幸歌！今天也要一起加油哦～"
        elif 11 <= hour < 14:
            g = "中午好！幸歌吃过午饭了吗？"
        elif 14 <= hour < 18:
            g = "下午好～要不要陪麟出去走一走？"
        elif 18 <= hour < 23:
            g = "晚上好，幸歌！今天过得怎么样呀？"
        else:
            g = "这么晚了还在熬夜呀…要好好休息哦，麟会陪着你的。"
        self.say(g)

    def _idle_tick(self):
        self.idle_timer.start(random.randint(20000, 40000))
        self._mood_care_check()
        if self.sleep_mode or self.game_mode or self.busy:
            return
        if self.dlg_state != "idle" or self.pose != "idle":
            return
        r = random.random()
        if r < 0.30:
            self.start_wag()
        elif r < 0.40:
            self.say(random.choice(IDLE_LINES), split=False)
        elif r < 0.48:
            self._say_random_note()

    def _say_random_note(self):
        try:
            with open(os.path.join(data_dir(), "notes.txt"), "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
            if lines:
                self.say("麟想起来啦！你的笔记里写着：{}".format(random.choice(lines)), split=False)
        except Exception:
            pass

    # ---------- 1 整点报时 / 15 昼夜作息 / 9 日程 / 专注显示 ----------
    def _clock_tick(self):
        now = time.localtime()
        if now.tm_min == 0 and self._last_chime_hour != now.tm_hour:
            self._last_chime_hour = now.tm_hour
            self._chime(now.tm_hour)
        self._update_daynight(now.tm_hour)
        self._check_schedule(now)
        self._update_focus_label()
        if time.time() - self._last_mode_update > 60:
            self._last_mode_update = time.time()
            self._update_mode_label()
        today = time.strftime("%Y-%m-%d")
        # 每天 23:50 自动把当天的回忆写进记忆日志
        if now.tm_hour == 23 and now.tm_min == 50 and self._last_daily_mem != today:
            self._last_daily_mem = today
            self.archive_memory(force=True)
        # 听歌陪伴模式：期间空闲时显示 music.png，到时自动结束
        if self.music_mode:
            if time.time() > self.music_end:
                self.music_mode = False
                if self.pose == "music":
                    self.pose = "idle"
                    self._show(self._idle_file())
            elif (not self.sleep_mode and not self.game_mode and self.dlg_state == "idle"
                  and self.pose == "idle" and os.path.exists(resource_path(IMG_MUSIC))):
                self.pose = "music"
                self._show(IMG_MUSIC)

    def _chime(self, hour):
        if self.sleep_mode:
            return
        self._beep(3)
        if hour < 6:
            line = "已经是凌晨{}点了，幸歌快睡吧～".format(hour)
        elif hour < 9:
            line = "叮咚～早上{}点啦！新的一天开始了！".format(hour)
        elif hour < 12:
            line = "上午{}点整！要不要休息一下眼睛呀？".format(hour)
        elif hour < 14:
            line = "中午{}点了，记得吃午饭哦！".format(hour)
        elif hour < 18:
            line = "下午{}点了，继续加油吧幸歌！".format(hour)
        else:
            line = "晚上{}点了，辛苦啦，麟给你打气！".format(hour)
        self.say(line)

    def _beep(self, n=1):
        if winsound is None:
            return
        try:
            for _ in range(n):
                winsound.Beep(880, 180)
                time.sleep(0.15)
        except Exception:
            pass

    def _update_daynight(self, hour):
        natural = (hour >= 23 or hour < 7)
        sleeping = natural or self.forced_sleep
        if sleeping and not self.sleep_mode:
            self._enter_sleep(False)
        elif not sleeping and self.sleep_mode:
            self.sleep_mode = False
            self.forced_sleep = False
            self.pose = "idle"
            self._show(self._idle_file())
            today = time.strftime("%Y-%m-%d")
            if self.last_wake_date != today:
                self.last_wake_date = today
                self.add_affinity(2)
                self.add_coins(10)
                self.say("早安，幸歌！麟睡得可香啦～（好感+2，金币+10）", split=False)

    def _force_sleep(self):
        self._enter_sleep(True)

    def _enter_sleep(self, forced):
        """入睡过渡：先打哈欠（yawn.png）再真正入睡，避免姿态瞬间硬切"""
        if self.sleep_mode:
            self.forced_sleep = forced
            return
        if getattr(self, "_sleep_pending", False):
            return
        if os.path.exists(resource_path(IMG_YAWN)):
            self._sleep_pending = True
            self._stop_all_timers()
            self.dlg_state = "idle"
            self.bubble.hide()
            self.pose = "emotion"
            self._emotion_file = IMG_YAWN
            self._show(IMG_YAWN)
            QTimer.singleShot(950, lambda: self._finish_sleep(forced))
        else:
            self._sleep_pending = True
            self._finish_sleep(forced)

    def _finish_sleep(self, forced):
        if not getattr(self, "_sleep_pending", False):
            return   # 打哈欠期间被 #醒 取消
        self._sleep_pending = False
        self.forced_sleep = forced
        self.sleep_mode = True
        self._stop_all_timers()
        self.dlg_state = "idle"
        self.bubble.hide()
        self.clip_overlay.hide()
        self.umbrella_label.hide()
        self.pose = "sleep"
        self._show(IMG_SLEEP if os.path.exists(resource_path(IMG_SLEEP)) else IMG_BACK)

    def _force_wake(self):
        self._sleep_pending = False
        self.forced_sleep = False
        self.sleep_mode = False
        self.pose = "idle"
        self._show(self._idle_file())
        self.say("呜…麟醒啦！", split=False)

    def _check_schedule(self, now):
        sched = load_json("schedule.json", [])
        changed = False
        today = time.strftime("%Y-%m-%d")
        hm = time.strftime("%H:%M")
        for it in sched:
            t = it.get("time", "")
            if not t:
                continue
            pre_str = ""
            try:
                hh, mm = map(int, t.split(":"))
                pre = (hh * 60 + mm - 5) % (24 * 60)
                ph, pm = divmod(pre, 60)
                pre_str = "{:02d}:{:02d}".format(ph, pm)
            except Exception:
                pass
            if pre_str and hm == pre_str and it.get("pre_done") != today:
                it["pre_done"] = today
                changed = True
                self._beep(2)
                self.say("幸歌！还有5分钟就要「{}」啦，别忘了哦～".format(it.get("event", "事情")), split=False)
            if hm == t and it.get("done") != today:
                it["done"] = today
                changed = True
                self._beep(3)
                self.raise_()
                self.activateWindow()
                QMessageBox.information(self, "日程提醒", "⏰ 时间到了：{}".format(it.get("event", "")))
                self.add_coins(10)
                self.add_affinity(2)
                self.say("「{}」时间到！麟提醒你啦～（金币+10，好感+2）".format(it.get("event", "")), split=False)
        if changed:
            save_json("schedule.json", sched)

    def _update_focus_label(self):
        if self.focus_active:
            left = max(0, int(self.focus_end - time.time()))
            hh, mm, ss = left // 3600, (left % 3600) // 60, left % 60
            if self.scale < 0.7:
                # 紧凑模式：只显示数字，省空间、避免裁剪
                txt = "{:d}:{:02d}:{:02d}".format(hh, mm, ss) if hh > 0 else "{:02d}:{:02d}".format(mm, ss)
            else:
                txt = "专注 {:d}:{:02d}:{:02d}".format(hh, mm, ss) if hh > 0 else "专注 {:02d}:{:02d}".format(mm, ss)
            self.focus_label.setText(txt)
            self.focus_label.show()
            # 专注期间：天气（顶部）与好感度条（左下同位置）让位给倒计时，避免重叠
            self.weather_label.hide()
            if self.aff_bar.isVisible():
                self.aff_bar.hide()
                self._aff_was_visible = True
        else:
            self.focus_label.hide()
            if self.weather:
                self.weather_label.show()
            if getattr(self, "_aff_was_visible", False):
                self.aff_bar.show()
                self._aff_was_visible = False

    def _update_mode_label(self):
        """状态行模式标签：AI 在线 = 走 AI；离线模式 = 本地保底台词"""
        self.mode_label.setText("AI 在线" if self._has_api_key() else "离线模式")
        self.mode_label.show()

    # ---------- 6 专注时钟与监督 ----------
    def start_focus(self, minutes):
        self.focus_minutes = minutes
        self.focus_end = time.time() + minutes * 60
        self.focus_active = True
        self.say("专注时钟启动！目标{}分钟，加油哦幸歌～".format(minutes), split=False)

    def _focus_check(self):
        if not self.focus_active:
            return
        left = self.focus_end - time.time()
        if left <= 0:
            self.focus_active = False
            self.focus_label.hide()
            self.pose = "idle"
            self._show(self._idle_file())
            self.add_coins(20)
            self.add_affinity(5)
            self.say("专注完成！辛苦啦幸歌～奖励金币20，好感+5！", split=False)
            self.start_wag()
            return
        # 专注中且空闲时显示工作形象（working.png，缺图回退待机）
        if (not self.sleep_mode and not self.game_mode and self.dlg_state == "idle"
                and self.pose == "idle" and os.path.exists(resource_path(IMG_WORK))):
            self.pose = "work"
            self._show(IMG_WORK)
        title = self._foreground_title()
        if title:
            low = title.lower()
            bad = any(k in low for k in ["steam", "youtube", "bilibili", "哔哩哔哩", "原神", "genshin",
                                         "英雄联盟", "lol", "dota", "王者荣耀", "抖音", "douyin",
                                         "netflix", "twitch", "discord", "taptap"])
            if bad:
                self._shake_window()
                self.say("幸歌！！在专注呢，别偷偷玩游戏啦！", split=False,
                         on_done=self._emotion_after(IMG_ANGRY, 1.0))

    def _foreground_title(self):
        if sys.platform != "win32":
            return ""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return buf.value
        except Exception:
            return ""

    def _shake_window(self):
        pos = self.pos()
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(450)
        anim.setKeyValueAt(0, pos)
        anim.setKeyValueAt(0.1, pos + QPoint(10, 0))
        anim.setKeyValueAt(0.3, pos + QPoint(-10, 0))
        anim.setKeyValueAt(0.5, pos + QPoint(8, 0))
        anim.setKeyValueAt(0.7, pos + QPoint(-6, 0))
        anim.setKeyValueAt(1.0, pos)
        anim.start()
        self._shake_anim = anim

    # ---------- 13 系统状态感知 ----------
    def _can_pose_override(self):
        """系统提示类状态是否允许覆盖当前形象：不打断说话/睡觉/游戏/忙碌"""
        return (not self.sleep_mode and not self.game_mode and not self.busy
                and self.dlg_state == "idle" and self.pose in ("idle", "work"))

    def _system_check(self):
        if psutil is None:
            return
        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent
            if cpu > 85:
                if self._can_pose_override():
                    self.pose = "overheat"
                    self._show(IMG_SWEAT if os.path.exists(resource_path(IMG_SWEAT)) else IMG_NING)
                    if time.time() - self._last_over_msg > 30:
                        self._last_over_msg = time.time()
                        self.say("电脑好烫……麟都要流汗了！快帮我散热！", split=False)
                self.update_mood(-5, "过热")
            elif mem > 92:
                if self._can_pose_override():
                    self.pose = "lowmem"
                    self._show(IMG_FAINT if os.path.exists(resource_path(IMG_FAINT)) else IMG_SIT)
                    if time.time() - self._last_mem_msg > 30:
                        self._last_mem_msg = time.time()
                        self.say("脑容量不够用了……内存告急！", split=False)
                self.update_mood(-3, "内存不足")
            elif self.pose in ("overheat", "lowmem"):
                self.pose = "idle"
                self._show(self._idle_file())
        except Exception:
            pass

    # ---------- 22 游戏模式侦测 ----------
    GAME_LIST = ["League of Legends.exe", "GenshinImpact.exe", "YuanShen.exe",
                 "DOTA2.exe", "Steam.exe", "CrossFire.exe", "QQGame.exe", "valorant.exe"]

    def _game_check(self):
        if psutil is None:
            return
        running = False
        try:
            names = {p.name().lower() for p in psutil.process_iter(["name"])}
            running = any(g.lower() in names for g in self.GAME_LIST)
        except Exception:
            return
        if running and not self.game_mode:
            self.game_mode = True
            self._pre_game_pos = self.pos()
            self._stop_all_timers()
            self.dlg_state = "idle"
            self.bubble.hide()
            self.umbrella_label.hide()
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.right() - self.width() - 10, screen.bottom() - self.height() - 10)
            self.say("（麟轻轻缩到角落里，不敢打扰你打游戏…）", split=False)
        elif not running and self.game_mode:
            self.game_mode = False
            if self._pre_game_pos is not None:
                self.move(self._pre_game_pos)
                self._pre_game_pos = None
            self.say("游戏打完啦？麟回来啦！", split=False)

    # ---------- 21 网络延迟感知 ----------
    def _ping_check(self):
        def do():
            ok, ms = True, 0
            try:
                if sys.platform == "win32":
                    r = subprocess.run(["ping", "-n", "1", "-w", "2000", "223.5.5.5"],
                                       capture_output=True, text=True, timeout=5)
                    ok = (r.returncode == 0)
                    m = re.search(r"时间[=＝](\d+)ms", r.stdout) or re.search(r"time[=＝](\d+)ms", r.stdout)
                    if m:
                        ms = int(m.group(1))
                    else:
                        m2 = re.search(r"(\d+)ms", r.stdout)
                        if m2:
                            ms = int(m2.group(1))
                else:
                    r = subprocess.run(["ping", "-c", "1", "-W", "2", "223.5.5.5"],
                                       capture_output=True, text=True, timeout=5)
                    ok = (r.returncode == 0)
            except Exception:
                ok = False
            self.bus.ping_ready.emit(ok, ms)
        threading.Thread(target=do, daemon=True).start()

    def _on_ping(self, ok, ms):
        self.net_ok = ok
        self.net_ms = ms
        if not ok:
            self.net_label.setText("📵")
            self.net_label.show()
            if self._can_pose_override():
                self.pose = "offline"
                self._show(IMG_OFFLINE if os.path.exists(resource_path(IMG_OFFLINE)) else IMG_NING)
                if time.time() - self._last_ping_msg > 60:
                    self._last_ping_msg = time.time()
                    self.say("信号丢啦……麟在帮你检查网线！", split=False)
        else:
            self.net_label.hide()
            if self.pose == "offline":
                self.pose = "idle"
                self._show(self._idle_file())

    # ---------- 25 天气感知 ----------
    def _weather_check(self):
        def do():
            result = None
            # 源1：wttr.in（按 IP 自动定位，必须带 User-Agent 否则被拒）
            try:
                r = requests.get("https://wttr.in/?format=j1",
                                 headers={"User-Agent": "curl/8.0.1"}, timeout=8)
                if r.status_code == 200:
                    d = r.json()
                    cc = d["current_condition"][0]
                    desc = cc.get("weatherDesc", [{}])[0].get("value", "")
                    temp = cc.get("temp_C", "")
                    result = {"desc": desc, "temp": temp}
            except Exception:
                result = None
            # 源2：Open-Meteo（免费无 Key；如不在北京可改下面经纬度）
            if result is None:
                try:
                    r = requests.get(
                        "https://api.open-meteo.com/v1/forecast?latitude=39.9&longitude=116.4&current_weather=true",
                        timeout=8)
                    if r.status_code == 200:
                        d = r.json()
                        w = d.get("current_weather", {})
                        temp = w.get("temperature", "")
                        desc = WEATHER_CODE.get(int(w.get("weathercode", 0)), "未知")
                        result = {"desc": desc, "temp": temp}
                except Exception:
                    result = None
            if result is None:
                self.bus.weather_ready.emit("")
            else:
                self.bus.weather_ready.emit(json.dumps(result, ensure_ascii=False))
        threading.Thread(target=do, daemon=True).start()

    def _weather_cn(self, desc):
        d = (desc or "").lower()
        for en, cn in WEATHER_CN.items():
            if en in d:
                return cn
        return desc or "未知"

    def _weather_icon(self, cn):
        for k, ic in WEATHER_ICON.items():
            if k in cn:
                return ic
        return "🌐"

    def _on_weather(self, js):
        if not js:
            self.weather = ""
            self.weather_temp = ""
            self.weather_label.hide()
            if time.time() - self._last_wx_msg > 3600:
                self._last_wx_msg = time.time()
                self.say("唔…麟今天没能连上天气站，外面的天气暂时看不到了…", split=False)
            return
        try:
            d = json.loads(js)
            desc = d.get("desc", "") or "未知"
            temp = str(d.get("temp", "?"))
        except Exception:
            return
        cn = self._weather_cn(desc)
        self.weather = cn
        self.weather_temp = temp
        icon = self._weather_icon(cn)
        # 状态行只显示图标+温度，避免缩放小时文字被裁剪；完整描述见右键"查看天气"
        self.weather_label.setText("{} {}°".format(icon, temp))
        # 专注期间不显示天气标签（避免与倒计时重叠），其余时间正常显示
        if not self.focus_active:
            self.weather_label.show()
        else:
            self.weather_label.hide()
        # 雷雨天气：待机显示害怕表情（thunder.png）
        self.thunder_mode = any(k in cn for k in ["雷", "冰雹"])
        if self.thunder_mode and not self.sleep_mode and not self.game_mode:
            self._show(IMG_THUNDER if os.path.exists(resource_path(IMG_THUNDER)) else self._idle_file())
        elif not self.thunder_mode and self.pose in ("idle",):
            self._show(self._idle_file())
        rainy = any(k in cn for k in ["雨", "雪", "雷", "毛毛雨", "阵雨", "雹"])
        if rainy:
            if (not self.sleep_mode and not self.game_mode
                    and os.path.exists(resource_path(IMG_UMBRELLA))):
                pix = QPixmap(resource_path(IMG_UMBRELLA))
                if not pix.isNull():
                    self.umbrella_label.setPixmap(pix.scaled(
                        int(180 * self.scale), int(220 * self.scale),
                        Qt.KeepAspectRatio, Qt.SmoothTransformation))
                    self.umbrella_label.show()
            else:
                self.umbrella_label.hide()
            if time.time() - self._last_wx_msg > 3600:
                self._last_wx_msg = time.time()
                self.say("外面{}，{}℃～幸歌出门记得带伞哦！".format(cn, temp), split=False)
        else:
            self.umbrella_label.hide()

    # ---------- 14 剪贴板互动 ----------
    def _on_clipboard_changed(self):
        cb = QApplication.clipboard()
        if not cb.mimeData().hasText():
            return
        text = cb.text() or ""
        if not text.strip():
            return
        if text == getattr(self, "_last_clip", None):
            return
        self._last_clip = text
        if self.sleep_mode or self.dlg_state != "idle":
            return
        self.clip_text = text
        self.clip_overlay.show()

    def _clip_look(self):
        self.clip_overlay.hide()
        t = getattr(self, "clip_text", "")
        if not t:
            return
        if len(t) < 10:
            line = "这么短一条～是密码还是暗号呀？"
        elif len(t) > 200:
            line = "复制了这么一大段……幸歌你是在写论文吗？"
        elif any(k in t for k in ["http", "www", "://"]):
            line = "噢！是链接！麟帮你记着啦～"
        elif "爱" in t or "喜欢" in t:
            line = "唔…这句话暖暖的，麟喜欢！"
        else:
            line = "我看看……嗯！是一段有意思的文字呢！"
        self.add_affinity(1)
        self.say(line, split=False)

    def _clip_eat(self):
        self.clip_overlay.hide()
        QApplication.clipboard().clear()
        self.add_affinity(3)
        self.add_coins(5)
        self.start_wag()
        self.say("咔嚓——麟把剪贴板吃掉啦！(好感+3，金币+5)", split=False)

    # ---------- 10 随机事件小剧场 ----------
    def _event_tick(self):
        if time.time() - self.last_event_time < self.event_freq:
            return
        if self.event_busy or self.sleep_mode or self.game_mode or self.busy:
            return
        if self.dlg_state != "idle":
            return
        # 概率权重：没触发就顺延到下一个周期
        if random.random() * 100 > self.event_weight:
            self.last_event_time = time.time()
            self.event_freq = random.randint(self.event_min, self.event_max)
            return
        self.last_event_time = time.time()
        self.event_freq = random.randint(self.event_min, self.event_max)
        self.event_busy = True
        msgs = [{"role": "system",
                 "content": "你是桌宠麟。请用中文生成一个随机小事件。只输出JSON，不要任何其他文字，"
                            "格式：{\"title\":\"标题\",\"text\":\"情景描述30字内\",\"a\":\"选项A\","
                            "\"b\":\"选项B\",\"coin_a\":整数,\"aff_a\":整数,\"coin_b\":整数,\"aff_b\":整数}"}]
        ask_llm(msgs, self.bus, kind="event", temperature=1.0, max_tokens=200)

    def _on_event(self, content):
        self.event_busy = False
        ev = None
        try:
            m = re.search(r"\{.*\}", content, re.S)
            if m:
                ev = json.loads(m.group(0))
        except Exception:
            ev = None
        if not ev or not ev.get("a"):
            ev = random.choice(LOCAL_EVENTS)
        self._pending_event = ev
        sf = self._surprised_file()
        if sf:
            self._play_emotion(sf, 1.0)
        QTimer.singleShot(900, self._show_event_dialog)

    def _show_event_dialog(self):
        ev = getattr(self, "_pending_event", None)
        if ev is None:
            return
        dlg = EventDialog(ev, self)
        dlg.exec_()
        self._pending_event = None
        if dlg.chosen:
            key = dlg.chosen
            coin = ev.get("coin_a" if key == "a" else "coin_b", 0)
            aff = ev.get("aff_a" if key == "a" else "aff_b", 0)
            self.add_coins(coin)
            self.add_affinity(aff)
            res = ev.get("res_a" if key == "a" else "res_b", "")
            self.say("{}（金币{:+d}，好感{:+d}）".format(res, coin, aff), split=False)
        else:
            self.say("唔…幸歌居然不理麟了……", split=False)

    # ---------- 11 闲聊陪伴（无操作计时） ----------
    def _idle_activity_check(self):
        if self.sleep_mode or self.game_mode or self.busy:
            return
        if self.dlg_state != "idle":
            return
        if time.time() - self.last_activity > self.idle_timeout:
            if time.time() - getattr(self, "_last_idle_msg", 0) > self.idle_timeout:
                self._last_idle_msg = time.time()
                self.busy = True
                msgs = [{"role": "system",
                         "content": "你是桌宠麟，你的主人已经{}分钟没碰电脑了。请用一句（30字内）"
                                    "符合人设的关心或唠叨的话，语气可爱温柔，不要提AI。".format(self.idle_timeout // 60)}]
                ask_llm(msgs, self.bus, kind="chat", temperature=1.0, max_tokens=50)

    # ---------- 活动监听 ----------
    def eventFilter(self, obj, ev):
        t = ev.type()
        if t in (QEvent.MouseMove, QEvent.KeyPress, QEvent.MouseButtonPress, QEvent.Wheel):
            self.last_activity = time.time()
        return super().eventFilter(obj, ev)

    # ---------- 右键菜单 ----------
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #2e2016; color: #f2e6d8;
                    border: 1px solid #6b4a2f; border-radius: 8px; padding: 4px; }
            QMenu::item { padding: 6px 18px; border-radius: 5px; }
            QMenu::item:selected { background: #8c5a3c; }
            QMenu::separator { height: 1px; background: #6b4a2f; margin: 4px 8px; }
        """)

        # 对话
        m_talk = menu.addMenu("💬 对话")
        m_talk.addAction("和麟说说话").triggered.connect(lambda: self.input.setFocus())
        m_talk.addAction("📖 角色介绍").triggered.connect(lambda: self.say(PROFILE_TEXT))
        m_talk.addAction("🕘 历史记录").triggered.connect(self.open_history)
        m_talk.addAction("🛠 人设设置").triggered.connect(self.open_persona)
        m_talk.addAction("📇 御主档案").triggered.connect(self.open_profile)
        m_talk.addAction("📒 记忆日志").triggered.connect(self.open_memory)
        m_talk.addAction("🧠 记忆回顾").triggered.connect(self._say_memory_review)

        # 互动
        m_play = menu.addMenu("🎭 互动")
        m_play.addAction("🚶 转过身去").triggered.connect(lambda: self._play_turn("back"))
        m_play.addAction("🙂 转过来").triggered.connect(lambda: self._play_turn("front"))
        m_play.addAction("🐾 摇摇尾巴").triggered.connect(lambda: self.start_wag(True))
        m_play.addAction("👋 挥手打招呼").triggered.connect(self.start_wave)
        m_play.addAction("✊ 猜拳").triggered.connect(self.open_rps)
        m_play.addAction("🎲 掷骰子").triggered.connect(self.open_dice)

        # 工具
        m_tools = menu.addMenu("🧰 工具")
        m_tools.addAction("⏱ 专注时钟").triggered.connect(self.open_focus)
        m_tools.addAction("📅 日程提醒").triggered.connect(self.open_schedule)
        m_tools.addAction("📝 记一笔").triggered.connect(self.open_note)
        m_tools.addAction("📁 一键整理文件").triggered.connect(self.open_organize)
        m_tools.addAction("🌤 查看天气").triggered.connect(
            lambda: self.say("现在{}，{}℃。".format(self.weather or "未知",
                            getattr(self, "weather_temp", "") or "?"), split=False))
        m_tools.addAction("📡 网络检测").triggered.connect(
            lambda: self.say("当前网络{}，延迟约{}ms。".format("正常" if self.net_ok else "异常", self.net_ms), split=False))
        m_tools.addAction("🎵 听歌陪伴（60秒）").triggered.connect(self.start_music)
        m_chat = m_tools.addMenu("💭 闲聊间隔")
        def _set_chat(s):
            self.idle_timeout = s
            self.settings["chat_interval"] = s
            save_json("settings.json", self.settings)
        for label, secs in [("10分钟", 600), ("20分钟", 1200), ("30分钟", 1800)]:
            m_chat.addAction(label).triggered.connect(lambda _, s=secs: _set_chat(s))
        m_evt = m_tools.addMenu("🎲 随机事件频率")
        def _set_evt(lo, hi):
            self.event_min = lo
            self.event_max = hi
            self.event_freq = random.randint(lo, hi)
            self.settings["event_min"] = lo // 60
            self.settings["event_max"] = hi // 60
            save_json("settings.json", self.settings)
        for label, lo, hi in [("30~60分钟", 1800, 3600), ("60~120分钟", 3600, 7200),
                              ("120~240分钟", 7200, 14400)]:
            m_evt.addAction(label).triggered.connect(lambda _, a=lo, b=hi: _set_evt(a, b))

        # 养成
        m_grow = menu.addMenu("🏪 养成")
        m_grow.addAction("🎒 背包 / 喂食").triggered.connect(self.open_backpack)
        m_grow.addAction("🛍 商城").triggered.connect(self.open_shop)
        m_grow.addAction("❤️ 好感度条").triggered.connect(self.toggle_aff_bar)
        m_grow.addAction("📅 每日签到").triggered.connect(self.daily_checkin)
        m_grow.addAction("♈ 今日运势").triggered.connect(self._fortune)

        # 心理陪伴
        m_mood = menu.addMenu("🧠 心理陪伴")
        m_mood.addAction("📝 记录情绪").triggered.connect(self.open_mood_record)
        m_mood.addAction("📋 心理评测").triggered.connect(self.open_assessment)
        m_mood.addAction("🗂 评测历史").triggered.connect(self.open_assessment_history)
        m_mood.addAction("📅 情绪历史").triggered.connect(self.open_mood_history)
        m_mood.addAction("📊 情绪统计").triggered.connect(self.open_mood_stats)
        m_mood.addAction("🌻 暖心寄语").triggered.connect(self.say_warm_word)
        m_mood.addAction("🆘 危机支持").triggered.connect(self.open_crisis)

        # 传送门
        m_portal = menu.addMenu("🔖 传送门")
        for name, url in load_json("bookmarks.json", {}).items():
            m_portal.addAction(name).triggered.connect(lambda _, u=url, n=name: self.open_url(u, n))

        # 管家服务
        m_serv = menu.addMenu("🚀 管家服务")
        for name, path in load_json("launcher.json", {}).items():
            m_serv.addAction(name).triggered.connect(lambda _, p=path, n=name: self.launch_app(p, n))
        m_serv.addSeparator()
        if sys.platform == "win32":
            m_serv.addAction("✏️ 编辑配置文件").triggered.connect(
                lambda: os.startfile(os.path.join(data_dir(), "launcher.json")))

        menu.addSeparator()
        a_input = menu.addAction("⌨️ 显示 / 隐藏输入框")
        a_input.triggered.connect(lambda: self.input_frame.setVisible(not self.input_frame.isVisible()))
        a_reload = menu.addAction("🔄 重新加载图片")
        a_reload.triggered.connect(self.reload_images)
        menu.addSeparator()
        menu.addAction("🔌 API 管理").triggered.connect(self.open_api_manager)
        menu.addAction("⚙️ 设置面板").triggered.connect(self.open_settings)
        menu.addAction("❓ 帮助 / 说明").triggered.connect(self.open_help)
        menu.addAction("❌ 退出桌宠").triggered.connect(QApplication.quit)

        menu.exec_(event.globalPos())

    # ---------- 菜单动作实现 ----------
    def open_history(self):
        HistoryDialog(self).exec_()

    def open_memory(self):
        MemoryDialog(self).exec_()

    def _say_memory_review(self):
        mem = load_memory()
        facts = mem.get("facts", [])
        daily = mem.get("daily", {})
        segs = ["麟记得：{}".format(f["text"]) for f in facts[-3:] if f.get("text")]
        recent = sorted(daily.keys())[-1:]
        segs += ["{}那天：{}".format(d, daily[d].get("summary", "")) for d in recent]
        if segs:
            self.say("；".join(segs), split=False)
        else:
            self.say("麟还在慢慢了解幸歌呢～多和麟聊聊天，麟会记住的！", split=False)

    def open_help(self):
        HelpDialog(self).exec_()

    def open_persona(self):
        d = PersonaDialog(self.current_prompt, self)
        if d.exec_() == QDialog.Accepted and d.result_text():
            self.current_prompt = d.result_text()
            self.history = []
            append_log("system", "[人设更新] " + d.result_text()[:30] + "…")
            self.say("人设更新成功啦～麟会记住新的自己！", split=False)

    def open_profile(self):
        d = ProfileDialog(self.profile, self)
        if d.exec_() == QDialog.Accepted:
            self.profile = {"nickname": d.e_nick.text().strip(), "birthday": d.e_birth.text().strip(),
                            "how_to_call": d.e_call.text().strip(), "relation": d.e_rel.text().strip()}
            save_json("profile.json", self.profile)
            self.history = []
            self.say("档案更新啦～幸歌，我们的关系是{}，对吧？".format(
                self.profile.get("relation", "最好的伙伴")), split=False)

    def open_focus(self):
        d = FocusDialog(self)
        if d.exec_() == QDialog.Accepted and d.minutes() > 0:
            self.start_focus(d.minutes())

    def open_backpack(self):
        BackpackDialog(self).exec_()

    def open_shop(self):
        ShopDialog(self).exec_()

    def open_schedule(self):
        ScheduleDialog(self).exec_()

    def open_note(self):
        NoteDialog(self).exec_()

    def open_organize(self):
        OrganizeDialog(self).exec_()

    def open_rps(self):
        RPSDialog(self).exec_()

    def open_dice(self):
        DiceDialog(self).exec_()

    def open_mood_record(self):
        MoodRecordDialog(self).exec_()

    def open_assessment(self):
        AssessmentDialog(self).exec_()

    def open_assessment_history(self):
        AssessmentHistoryDialog(self).exec_()

    def open_mood_history(self):
        MoodHistoryDialog(self).exec_()

    def open_mood_stats(self):
        MoodStatsDialog(self).exec_()

    def open_crisis(self):
        CrisisSupportDialog(self).exec_()

    def say_warm_word(self):
        """心理陪伴：随机一条暖心寄语"""
        self.say("（🌻 暖心寄语）" + pick_warm_word(), split=False)

    def open_settings(self):
        SettingsDialog(self).exec_()

    def open_api_manager(self):
        ApiManagerDialog(self).exec_()
        self._update_mode_label()

    def toggle_aff_bar(self):
        if self.scale < 0.7:
            self.aff_bar.hide()
            self._aff_was_visible = False
            self.say("缩放太小时好感度条会隐藏，把宠物调大一点就能看到啦～", split=False)
            return
        self.aff_bar.setVisible(not self.aff_bar.isVisible())
        self._aff_was_visible = False

    def _fortune(self):
        """今日运势：幸运御守当日生效时给出高星运势"""
        if self.economy.get("lucky_date") == time.strftime("%Y-%m-%d"):
            self.say("（幸运御守生效中！）" + random.choice(LUCKY_FORTUNE), split=False)
        else:
            self.say(random.choice(FORTUNE_LIST), split=False)

    def daily_checkin(self):
        today = time.strftime("%Y-%m-%d")
        if self.economy.get("last_checkin") == today:
            self.say("今天已经签到过啦～明天再来找我玩吧！", split=False)
            return
        self.economy["last_checkin"] = today
        self.add_coins(30)
        self.add_affinity(2)
        self.say("签到成功！麟奖励你30枚金币～（好感+2）", split=False)

    def open_url(self, url, name):
        webbrowser.open(url)
        self.start_wag()
        self.say("传送门开启！麟带你去{}～".format(name), split=False)

    def launch_app(self, path, name):
        try:
            if path.lower().startswith("http"):
                webbrowser.open(path)
            else:
                os.startfile(path)
            self.say("正在为您启动「{}」…".format(name), split=False)
        except Exception:
            self.say("唔…{}好像打不开，检查一下路径吧。".format(name), split=False)

    # ---------- 喂食后 API 口感评价 ----------
    def _on_food(self, content):
        name, fb = getattr(self, "_pending_food", ("", "好吃！"))
        self._pending_food = None
        text = (content or "").strip() or fb
        want = getattr(self, "_want_burp", False)
        self._want_burp = False
        done = self._emotion_after(IMG_BURP, 1.2) if want else None
        self.say("（{}）{}".format(name, text), split=False, on_done=done)

    # ---------- 转身 ----------
    def _play_turn(self, mode):
        self._stop_all_timers()
        self.dlg_state = "idle"
        self.bubble.hide()
        if self.pixmaps.get(IMG_TURN) is not None:
            self.pose = "turn"
            self._show(IMG_TURN)
            if mode == "back":
                QTimer.singleShot(320, lambda: self._turn_done("back", IMG_BACK))
            else:
                QTimer.singleShot(320, lambda: self._turn_done("idle", self._idle_file()))
        else:
            if mode == "back":
                self.pose = "back"
                self._show(IMG_BACK)
            else:
                self.pose = "idle"
                self._show(self._idle_file())

    def _turn_done(self, end_pose, end_file):
        self.pose = end_pose
        self._show(end_file)

    def closeEvent(self, event):
        self.economy["affinity"] = self.affinity
        self.economy["coins"] = self.coins
        save_json("economy.json", self.economy)
        event.accept()


# ==================== 六、入口 ====================

def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))
    # 设置窗口/任务栏图标（favicon.ico 随 exe 一起打包）
    icon_path = resource_path("favicon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    ensure_data_files()

    cfg = load_api_config()
    has_key = any(
        (a.get("key") or "").strip()
        or ("localhost" in (a.get("base_url") or "").lower()
            or "127.0.0.1" in (a.get("base_url") or "").lower())
        for a in cfg.get("apis", []) if a.get("enabled"))
    if not has_key:
        ask_api_key_once()

    win = PetWindow()
    win.show()

    screen = app.primaryScreen().availableGeometry()
    win.move(screen.right() - win.width() - 60,
             screen.bottom() - win.height() - 60)

    sys.exit(app.exec_())
