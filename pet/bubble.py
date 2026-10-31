"""对话气泡：无边框半透明面板，内含聊天记录、流式回复与输入框。

流式片段直接插入聊天记录的末尾，窗口尺寸固定，避免逐字刷新时抖动。
"""
from __future__ import annotations

from html import escape

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QGuiApplication, QPainter, QPainterPath, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

PANEL_QSS = """
QWidget#Panel {
    background: rgba(28, 24, 36, 238);
    border: 1px solid rgba(255, 255, 255, 26);
    border-radius: 16px;
}
QTextBrowser {
    background: transparent;
    border: none;
    font-size: 13px;
}
QLabel#Status {
    color: #b9aec8;
    font-size: 12px;
    padding-left: 2px;
}
QLineEdit {
    background: rgba(255, 255, 255, 16);
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 10px;
    padding: 7px 10px;
    color: #f6f1fb;
    font-size: 13px;
    selection-background-color: #b4519a;
}
QLineEdit:focus {
    border: 1px solid rgba(214, 138, 198, 160);
}
QPushButton {
    background: rgba(180, 81, 154, 190);
    border: none;
    border-radius: 10px;
    padding: 7px 14px;
    color: #ffffff;
    font-size: 13px;
}
QPushButton:hover { background: rgba(206, 102, 178, 220); }
QPushButton:disabled { background: rgba(255, 255, 255, 24); color: #9b93a8; }
"""

USER_COLOR = "#9fd0ff"
PET_COLOR = "#ffc4e4"
SYSTEM_COLOR = "#ff9d9d"
PANEL_W = 392
PANEL_H = 320


class ChatBubble(QWidget):
    submitted = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._streaming = False
        self._cursor: QTextCursor | None = None
        self._last_pet_rect = QRect()

        self.setWindowTitle("和喵斯特莉亚说话")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFixedSize(PANEL_W, PANEL_H)

        panel = QWidget(self)
        panel.setObjectName("Panel")
        panel.setStyleSheet(PANEL_QSS)

        self._history = QTextBrowser(panel)
        self._history.setOpenExternalLinks(False)
        self._history.setFrameShape(QTextBrowser.Shape.NoFrame)
        self._history.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._history.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._history.customContextMenuRequested.connect(self._history_menu)

        self._status = QLabel("", panel)
        self._status.setObjectName("Status")
        self._status.hide()

        self._input = QLineEdit(panel)
        self._input.setPlaceholderText("和喵斯特莉亚说点什么…")
        self._input.returnPressed.connect(self._send)
        self._send_button = QPushButton("发送", panel)
        self._send_button.clicked.connect(self._send)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._input, 1)
        row.addWidget(self._send_button, 0)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(7)
        layout.addWidget(self._history, 1)
        layout.addWidget(self._status, 0)
        layout.addLayout(row)

    # ---------- 绘制圆角外壳 ----------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, self.width() - 1, self.height() - 1, 16, 16)
        painter.fillPath(path, QColor(28, 24, 36, 238))
        painter.setPen(QColor(255, 255, 255, 26))
        painter.drawPath(path)

    # ---------- 定位 ----------
    def follow(self, pet_rect: QRect) -> None:
        """贴到宠物上方（放不下就翻到下方），并夹在屏幕内。

        这里不能因为「还没显示」就跳过：pop_up() 必须先把位置算好再 show()，
        否则首次打开会出现在系统给的默认位置（屏幕中间）。
        """
        self._last_pet_rect = QRect(pet_rect)
        screen = QGuiApplication.screenAt(pet_rect.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1600, 1000)
        gap = 8
        x = pet_rect.center().x() - self.width() // 2
        y = pet_rect.top() - self.height() - gap
        if y < area.top():
            y = pet_rect.bottom() + gap
        x = max(area.left() + 4, min(x, area.right() - self.width() - 4))
        y = max(area.top() + 4, min(y, area.bottom() - self.height() - 4))
        self.move(QPoint(x, y))

    def pop_up(self, pet_rect: QRect) -> None:
        # 先定位再显示，避免第一帧闪在默认位置
        self.follow(pet_rect)
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()

    # ---------- 消息 ----------
    def _scroll_to_end(self) -> None:
        bar = self._history.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _append(self, html: str) -> None:
        self._history.append(html)
        self._scroll_to_end()

    def add_user(self, text: str) -> None:
        self._append(
            f'<div style="margin:10px 0 1px 0;color:{USER_COLOR};font-weight:600;">你</div>'
            f'<div style="margin:0 0 9px 0;color:#efe9f7;">'
            f'{escape(text).replace(chr(10), "<br>")}</div>'
        )

    def begin_reply(self) -> None:
        self._streaming = True
        self._status.setText("正在思考…")
        self._status.show()
        self._append(
            f'<div style="margin:6px 0 1px 0;color:{PET_COLOR};font-weight:600;">'
            f'喵斯特莉亚</div>'
            f'<div style="margin:0 0 0 0;color:#efe9f7;"></div>'
        )
        self._cursor = QTextCursor(self._history.document())
        self._cursor.movePosition(QTextCursor.MoveOperation.End)
        self.set_busy(True)

    def add_chunk(self, text: str) -> None:
        if not self._streaming or self._cursor is None:
            return
        if self._status.text() != "正在回复…":
            self._status.setText("正在回复…")
        last = self._cursor
        last.insertText(text)
        self._scroll_to_end()

    def end_reply(self, full_text: str | None = None) -> None:
        self._streaming = False
        self._cursor = None
        self._status.hide()
        self._append('<div style="margin:0 0 8px 0;"></div>')
        self.set_busy(False)
        self._input.setFocus()

    def show_error(self, message: str) -> None:
        self._streaming = False
        self._cursor = None
        self._status.hide()
        self._append(f'<div style="margin:8px 0;color:{SYSTEM_COLOR};">⚠ {escape(message)}</div>')
        self.set_busy(False)
        self._input.setFocus()

    def add_system(self, message: str, color: str = SYSTEM_COLOR) -> None:
        self._append(f'<div style="margin:8px 0;color:{color};">{escape(message)}</div>')

    def clear_history(self) -> None:
        self._history.clear()
        self._status.hide()
        self._streaming = False
        self._cursor = None

    def set_busy(self, busy: bool) -> None:
        self._send_button.setEnabled(not busy)
        self._input.setEnabled(not busy)

    # ---------- 交互 ----------
    def _send(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self.add_user(text)
        self.submitted.emit(text)

    def _history_menu(self, position) -> None:
        menu = QMenu(self)
        action = QAction("清空对话", menu)
        action.triggered.connect(self.clear_history)
        menu.addAction(action)
        menu.exec(self._history.mapToGlobal(position))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self.closed.emit()
