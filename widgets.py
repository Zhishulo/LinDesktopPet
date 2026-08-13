# -*- coding: utf-8 -*-
"""自定义控件（原 oc.py 四区）：对话气泡、桌宠本体。"""
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QTextOption
from PyQt5.QtWidgets import QTextBrowser, QFrame, QLabel
# ==================== 四、自定义控件 ====================

class BubbleLabel(QTextBrowser):
    """对话气泡：可滚动，长文本完整显示不截断；点击跳过打字/下一句"""
    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.setFocusPolicy(Qt.NoFocus)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.document().setDocumentMargin(8)
        self.setStyleSheet("""
            QTextBrowser {
                background-color: rgba(255, 252, 242, 235);
                color: #3d2b1f;
                border: none;
                border-radius: 14px;
                padding: 6px 8px;
                font-size: 14px;
            }
            QScrollBar:vertical { background: transparent; width: 6px; }
            QScrollBar::handle:vertical { background: #c9a97e; border-radius: 3px; min-height: 20px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)

    def set_content(self, s):
        """写入文字并自动滚到底部（打字机逐字更新用）"""
        self.setPlainText(s)
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class PetLabel(QLabel):
    """桌宠本体：按住可拖动，单击互动，鼠标滑过算抚摸"""
    clicked = pyqtSignal()
    drag_started = pyqtSignal()
    drag_ended = pyqtSignal()
    petted = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press = None
        self._moved = False
        self._drag_emitted = False

    def enterEvent(self, event):
        self.petted.emit()
        super().enterEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.globalPos()
            self._moved = False
            self._drag_emitted = False
        event.accept()

    def mouseMoveEvent(self, event):
        if self._press is None:
            return
        delta = event.globalPos() - self._press
        if delta.manhattanLength() > 6:
            self._moved = True
            if not self._drag_emitted:
                self._drag_emitted = True
                self.drag_started.emit()
        win = self.window()
        win.move(win.pos() + delta)
        self._press = event.globalPos()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = None
            if self._moved:
                if not self._drag_emitted:
                    self._drag_emitted = True
                    self.drag_started.emit()
                self.drag_ended.emit()
            else:
                self.clicked.emit()
        event.accept()


# ---- 各种对话框 ----
