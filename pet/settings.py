"""设置窗口：仿参考作的粉白卡片风格，任务栏常驻，最小化缩回任务栏。

和桌宠窗口分开：桌宠是透明的常驻小窗，设置是独立窗口，有任务栏按钮，
所以最小化时不会再变成屏幕左上角那种奇怪的小方块。
"""
from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

from PyQt6.QtCore import QPoint, QStandardPaths, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import providers
from .chat import ChatError, list_models, test_connection
from .config import DEFAULT_SYSTEM_PROMPT, MAX_SIZE, MIN_SIZE, POSES_DIR, Config
from .facts import FactStore
from .memory import MemoryStore

WINDOW_W, WINDOW_H = 920, 640

CREAM = "#FDF6EF"
INK = "#5A4650"
MUTED = "#9B8790"
PINK = "#D8658A"
PINK_DARK = "#B84A70"
PINK_SOFT = "#FBE7EE"
BORDER = "#EFCAD6"

QSS = f"""
* {{
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", sans-serif;
}}
QLabel {{
    color: {INK};
    font-size: 12px;
}}
QWidget#Portrait {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #FCE9EF, stop:1 #F6D2DF);
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QLabel#PortraitName {{
    color: {PINK_DARK};
    font-size: 17px;
    font-weight: 600;
}}
QLabel#PortraitSub {{
    color: #B98C9D;
    font-size: 10px;
}}
QLabel#PortraitHint {{
    color: #A87587;
    font-size: 10px;
}}
QStackedWidget#Stack {{
    background: #FFFCF8;
    border: 1px solid {BORDER};
    border-radius: 12px;
}}
QStackedWidget#Stack > QWidget {{
    background: transparent;
}}
QPushButton#Tab {{
    background: {PINK_SOFT};
    color: #A85671;
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 5px 16px;
    font-size: 12px;
}}
QPushButton#Tab:checked {{
    background: {PINK};
    color: #FFFFFF;
    border: 1px solid {PINK};
}}
QPushButton#Tab:hover {{
    border: 1px solid {PINK};
}}
QPushButton#Primary {{
    background: {PINK};
    color: #FFFFFF;
    border: none;
    border-radius: 9px;
    padding: 7px 26px;
    font-size: 13px;
}}
QPushButton#Primary:hover {{ background: #C9567C; }}
QPushButton#Ghost {{
    background: #F7E3EA;
    color: #A85671;
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 7px 20px;
    font-size: 13px;
}}
QPushButton#Ghost:hover {{ background: #F2D8E2; }}
QPushButton#Mini {{
    background: {PINK_SOFT};
    color: #A85671;
    border: 1px solid {BORDER};
    border-radius: 7px;
    padding: 3px 12px;
    font-size: 11px;
}}
QPushButton#TitleBtn {{
    background: transparent;
    color: #A87587;
    border: none;
    border-radius: 6px;
    font-size: 14px;
    min-width: 26px;
    min-height: 22px;
}}
QPushButton#TitleBtn:hover {{
    background: {PINK_SOFT};
    color: {PINK_DARK};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: #FFFFFF;
    color: {INK};
    border: 1px solid #E7C9D4;
    border-radius: 8px;
    padding: 5px 8px;
    font-size: 12px;
    selection-background-color: {PINK};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 1px solid {PINK};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 22px;
    border: none;
    background: transparent;
}}
QTextEdit, QListWidget {{
    background: #FFFFFF;
    color: {INK};
    border: 1px solid #E7C9D4;
    border-radius: 8px;
    font-size: 12px;
    padding: 4px;
}}
QCheckBox {{
    color: {INK};
    font-size: 12px;
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid #E0B6C6;
    background: #FFFFFF;
}}
QCheckBox::indicator:checked {{
    background: {PINK};
    border: 1px solid {PINK};
}}
QLabel#FooterHint {{ color: {MUTED}; font-size: 10px; }}
QLabel#Hint {{ color: {MUTED}; font-size: 10px; }}
"""


def _arrow_icon_path() -> str:
    """QSS 画不出三角箭头，生成一张小图给下拉框用（打包后同样有效）。"""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)
    target = Path(base or ".") / "mysterio-arrow.png"
    try:
        if not target.exists():
            pixmap = QPixmap(18, 18)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#A85671"))
            triangle = QPainterPath()
            triangle.moveTo(4.5, 7.0)
            triangle.lineTo(13.5, 7.0)
            triangle.lineTo(9.0, 12.0)
            triangle.closeSubpath()
            painter.drawPath(triangle)
            painter.end()
            pixmap.save(str(target), "PNG")
    except OSError:
        return ""
    return target.as_posix()


def _qss(arrow: str) -> str:
    extra = ""
    if arrow:
        extra = (
            f"QComboBox::down-arrow {{ image: url({arrow}); width: 18px; height: 18px; }}\n"
        )
    return QSS + extra


class _NetWorker(QThread):
    """把一次网络调用丢到子线程，避免设置窗口卡住。"""

    done = pyqtSignal(object)
    fail = pyqtSignal(str)

    def __init__(self, task) -> None:  # task: Callable[[], object]
        super().__init__()
        self._task = task

    def run(self) -> None:
        try:
            self.done.emit(self._task())
        except ChatError as exc:
            self.fail.emit(str(exc))
        except Exception as exc:  # pragma: no cover - 网络栈意外异常
            self.fail.emit(f"失败了：{exc}")


class TitleBar(QWidget):
    """自绘标题栏：拖动窗口、最小化、关闭（关闭=缩进任务栏）。"""

    minimize_requested = pyqtSignal()
    close_requested = pyqtSignal()

    def __init__(self, title: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._drag_offset: QPoint | None = None
        self.setFixedHeight(42)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 6, 14, 4)
        label = QLabel(title)
        label.setStyleSheet(
            f"color:{PINK_DARK};font-size:13px;font-weight:600;letter-spacing:1px;"
        )
        layout.addWidget(label)
        layout.addStretch(1)

        for text, tip, signal in (
            ("—", "最小化到任务栏", self.minimize_requested),
            ("✕", "缩到任务栏（右键桌宠可退出）", self.close_requested),
        ):
            button = QPushButton(text)
            button.setObjectName("TitleBtn")
            button.setToolTip(tip)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(signal.emit)
            layout.addWidget(button)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setPen(QPen(QColor("#F1D7DF"), 1))
        painter.drawLine(18, self.height() - 1, self.width() - 18, self.height() - 1)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class SettingsWindow(QWidget):
    applied = pyqtSignal(object)  # Config

    def __init__(
        self,
        config: Config,
        dpr: float = 1.0,
        memory: MemoryStore | None = None,
        facts: FactStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("喵斯特莉亚 · 设置")
        icon_path = POSES_DIR / "idle.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setFixedSize(WINDOW_W, WINDOW_H)

        self._saved = deepcopy(config)
        self._config = deepcopy(config)
        self._dpr = max(1.0, dpr)
        self._memory = memory
        self._facts = facts
        self._worker: _NetWorker | None = None
        self._model_cache: dict[str, list[str]] = {}
        self._allow_close = False
        self._placed = False
        self._keys = dict(self._config.chat.api_keys)
        self._bases = dict(self._config.chat.base_urls)
        self._models = dict(self._config.chat.models)
        self._current = providers.get(self._config.chat.provider).key
        self._arrow_path = _arrow_icon_path()

        self._build()
        for spin in (self._temperature, self._max_tokens, self._size,
                     self._rest, self._memory_tokens):
            self._plain_spin(spin)
        self._load_provider(self._current)
        self._load_rest()
        self._load_memory()

    # ---------- 外壳 ----------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        title = TitleBar("喵斯特莉亚 · 设置", self)
        title.minimize_requested.connect(self.showMinimized)  # — ：保留任务栏图标
        title.close_requested.connect(self.close)             # ✕ ：收走任务栏图标
        root.addWidget(title)

        body = QHBoxLayout()
        body.setContentsMargins(18, 6, 18, 4)
        body.setSpacing(14)
        body.addWidget(self._build_portrait(), 0)
        body.addWidget(self._build_content(), 1)
        root.addLayout(body, 1)
        root.addWidget(self._build_footer())
        self.setStyleSheet(_qss(self._arrow_path))

    def _build_portrait(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("Portrait")
        panel.setFixedWidth(232)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(2)

        name = QLabel("喵斯特莉亚")
        name.setObjectName("PortraitName")
        name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        sub = QLabel("Mysterio · Desktop Pet")
        sub.setObjectName("PortraitSub")
        sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        picture = QLabel()
        picture.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        pixmap = QPixmap(str(POSES_DIR / "idle.png"))
        if not pixmap.isNull():
            picture.setPixmap(
                pixmap.scaled(
                    196, 300,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

        hint = QLabel("右键桌宠 → 设置 也能叫出这个窗口\n关掉会缩到任务栏，双击桌宠可以聊天")
        hint.setObjectName("PortraitHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        hint.setWordWrap(True)

        layout.addWidget(name)
        layout.addWidget(sub)
        layout.addStretch(1)
        layout.addWidget(picture)
        layout.addStretch(1)
        layout.addWidget(hint)
        return panel

    def _build_content(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        for index, name in enumerate(("连接", "外观", "人设", "记忆")):
            button = QPushButton(name)
            button.setObjectName("Tab")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, i=index: self._switch_tab(i))
            self._tab_group.addButton(button, index)
            tabs.addWidget(button)
        tabs.addStretch(1)
        layout.addLayout(tabs)

        self._stack = QStackedWidget()
        self._stack.setObjectName("Stack")
        self._stack.addWidget(self._build_connection())
        self._stack.addWidget(self._build_appearance())
        self._stack.addWidget(self._build_persona())
        self._stack.addWidget(self._build_memory())
        layout.addWidget(self._stack, 1)
        self._switch_tab(0)
        return wrap

    def _build_footer(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(22, 2, 22, 14)
        layout.setSpacing(10)
        hint = QLabel("关闭或最小化都会缩到任务栏；要彻底退出请用托盘菜单或右键桌宠 → 退出")
        hint.setObjectName("FooterHint")
        layout.addWidget(hint, 1)

        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.clicked.connect(self._cancel)
        save = QPushButton("保存")
        save.setObjectName("Primary")
        save.setCursor(Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self._save)
        layout.addWidget(cancel, 0)
        layout.addWidget(save, 0)
        return bar

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(1, 1, self.width() - 2, self.height() - 2, 16, 16)
        painter.fillPath(path, QColor(CREAM))
        painter.setPen(QPen(QColor("#E7A6B8"), 2))
        painter.drawPath(path)

    # ---------- 页面公共 ----------
    @staticmethod
    def _page() -> tuple[QWidget, QFormLayout]:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(16, 14, 16, 14)
        form = QFormLayout()
        form.setSpacing(9)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(form)
        outer.addStretch(1)
        return page, form

    @staticmethod
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Hint")
        label.setWordWrap(True)
        return label


    @staticmethod
    def _plain_spin(spin: QSpinBox | QDoubleSpinBox) -> None:
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

    # ---------- 连接页 ----------
    def _build_connection(self) -> QWidget:
        page, form = self._page()

        self._provider = QComboBox()
        for key in providers.PROVIDER_KEYS:
            self._provider.addItem(providers.PROVIDERS[key].label, key)
        self._provider.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow("供应商", self._provider)

        self._provider_hint = self._hint("")
        form.addRow("", self._provider_hint)

        self._key = QLineEdit()
        self._key.setEchoMode(QLineEdit.EchoMode.Password)
        show_key = QCheckBox("显示")
        show_key.toggled.connect(
            lambda on: self._key.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        key_row = QHBoxLayout()
        key_row.addWidget(self._key, 1)
        key_row.addWidget(show_key, 0)
        key_holder = QWidget()
        key_holder.setLayout(key_row)
        form.addRow("API Key", key_holder)

        self._key_hint = self._hint("")
        form.addRow("", self._key_hint)

        self._base_url = QLineEdit()
        form.addRow("接口地址", self._base_url)

        self._model = QComboBox()
        self._model.setEditable(True)
        self._model.setMinimumWidth(220)
        self._fetch = QPushButton("拉取")
        self._fetch.setObjectName("Mini")
        self._fetch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._fetch.setToolTip("用当前 Key 拉取账号可用的模型名")
        self._fetch.clicked.connect(self._fetch_models)
        model_row = QHBoxLayout()
        model_row.addWidget(self._model, 1)
        model_row.addWidget(self._fetch, 0)
        model_holder = QWidget()
        model_holder.setLayout(model_row)
        form.addRow("模型", model_holder)

        self._temperature = QDoubleSpinBox()
        self._temperature.setRange(0.0, 2.0)
        self._temperature.setSingleStep(0.1)
        self._temperature.setDecimals(1)
        form.addRow("温度", self._temperature)

        self._max_tokens = QSpinBox()
        self._max_tokens.setRange(64, 8192)
        self._max_tokens.setSingleStep(64)
        form.addRow("回复长度上限", self._max_tokens)

        self._proxy = QLineEdit()
        self._proxy.setPlaceholderText("http://127.0.0.1:7890（留空则用 HTTPS_PROXY 环境变量）")
        form.addRow("代理（可选）", self._proxy)

        self._test_button = QPushButton("测试连接")
        self._test_button.setObjectName("Ghost")
        self._test_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._test_button.clicked.connect(self._test)
        self._test_result = QLabel("")
        self._test_result.setWordWrap(True)
        test_row = QHBoxLayout()
        test_row.addWidget(self._test_button, 0)
        test_row.addWidget(self._test_result, 1)
        test_holder = QWidget()
        test_holder.setLayout(test_row)
        form.addRow("", test_holder)
        return page

    # ---------- 外观页 ----------
    def _build_appearance(self) -> QWidget:
        page, form = self._page()

        self._size = QSpinBox()
        self._size.setRange(MIN_SIZE, MAX_SIZE)
        self._size.setSingleStep(8)
        self._size.setSuffix(" px（逻辑）")
        self._size.valueChanged.connect(self._update_size_hint)
        form.addRow("桌宠尺寸", self._size)

        self._size_hint = self._hint("")
        form.addRow("", self._size_hint)

        self._on_top = QCheckBox("始终显示在其他窗口前面")
        form.addRow("", self._on_top)

        self._rest = QSpinBox()
        self._rest.setRange(15, 7200)
        self._rest.setSingleStep(15)
        self._rest.setSuffix(" 秒")
        form.addRow("多久后休息", self._rest)
        form.addRow("", self._hint("超过这个时间没有任何交互，她会闭眼打盹；动一下鼠标就醒。"))

        form.addRow("", self._hint("素材是 512×512 原图，程序只按显示尺寸生成设备像素副本，不压缩原文件。"))
        return page

    # ---------- 人设页 ----------
    def _build_persona(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        layout.addWidget(QLabel("系统提示词（决定她的性格和说话方式）："))
        self._system_prompt = QTextEdit()
        layout.addWidget(self._system_prompt, 1)

        reset = QPushButton("恢复默认人设")
        reset.setObjectName("Ghost")
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.clicked.connect(lambda: self._system_prompt.setPlainText(DEFAULT_SYSTEM_PROMPT))
        row = QHBoxLayout()
        row.addWidget(reset, 0)
        row.addStretch(1)
        layout.addLayout(row)
        return page

    # ---------- 记忆页 ----------
    def _build_memory(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(9)

        grid = QFormLayout()
        grid.setSpacing(9)
        self._memory_enabled = QCheckBox("在本地记住对话，重启后还能接着聊")
        grid.addRow("启用记忆", self._memory_enabled)

        self._memory_tokens = QSpinBox()
        self._memory_tokens.setRange(400, 32000)
        self._memory_tokens.setSingleStep(200)
        self._memory_tokens.setSuffix(" tokens")
        grid.addRow("上下文预算", self._memory_tokens)
        grid.addRow(
            "",
            self._hint("送进模型的是「摘要 + 最近对话」。超出预算时淘汰到 60% 留余量，最近 6 条永不淘汰。"),
        )
        self._memory_stats = QLabel("")
        self._memory_stats.setWordWrap(True)
        grid.addRow("当前记忆", self._memory_stats)

        self._memory_path_label = QLabel()
        self._memory_path_label.setObjectName("Hint")
        self._memory_path_label.setWordWrap(True)
        grid.addRow("记忆文件", self._memory_path_label)
        grid.addRow(
            "",
            self._hint(
                "默认放在程序所在目录，换台电脑、换个文件夹都会跟着变；"
                "如果那个位置不可写（例如装进 Program Files），会自动改用 %APPDATA%\\Mysterio，"
                "这里显示的始终是实际位置。"
            ),
        )

        self._forget_button = QPushButton("清空记忆")
        self._forget_button.setObjectName("Ghost")
        self._forget_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._forget_button.clicked.connect(self._clear_memory)
        forget_row = QHBoxLayout()
        forget_row.addWidget(self._forget_button, 0)
        forget_row.addStretch(1)
        forget_holder = QWidget()
        forget_holder.setLayout(forget_row)
        grid.addRow("", forget_holder)

        grid.addRow(
            "永久记忆",
            self._hint("只有你对她说「一定要记住…」「别忘了…」时才会写入，不参与摘要淘汰。"),
        )
        self._facts_list = QListWidget()
        self._facts_list.setMaximumHeight(110)
        grid.addRow("", self._facts_list)

        self._delete_fact_button = QPushButton("删除选中")
        self._delete_fact_button.setObjectName("Mini")
        self._delete_fact_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete_fact_button.clicked.connect(self._delete_fact)
        self._clear_facts_button = QPushButton("清空永久记忆")
        self._clear_facts_button.setObjectName("Mini")
        self._clear_facts_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_facts_button.clicked.connect(self._clear_facts)
        fact_row = QHBoxLayout()
        fact_row.addWidget(self._delete_fact_button, 0)
        fact_row.addWidget(self._clear_facts_button, 0)
        fact_row.addStretch(1)
        fact_holder = QWidget()
        fact_holder.setLayout(fact_row)
        grid.addRow("", fact_holder)

        outer.addLayout(grid)
        outer.addStretch(1)
        return page

    # ---------- 页签 ----------
    def _switch_tab(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        button = self._tab_group.button(index)
        if button is not None:
            button.setChecked(True)

    # ---------- 供应商切换 ----------
    def _stash(self) -> None:
        self._keys[self._current] = self._key.text().strip()
        self._bases[self._current] = self._base_url.text().strip()
        self._models[self._current] = self._model.currentText().strip()

    def _load_provider(self, provider: str) -> None:
        spec = providers.get(provider)
        self._current = spec.key
        index = self._provider.findData(spec.key)
        if index >= 0 and self._provider.currentIndex() != index:
            self._provider.blockSignals(True)
            self._provider.setCurrentIndex(index)
            self._provider.blockSignals(False)

        self._key.setText(self._keys.get(spec.key, ""))
        self._key.setPlaceholderText(spec.key_placeholder)
        self._base_url.setText(self._bases.get(spec.key, ""))
        self._base_url.setPlaceholderText(spec.default_base_url)
        self._provider_hint.setText(spec.hint)

        current = self._models.get(spec.key) or spec.default_model
        names: list[str] = []
        for name in [current, spec.default_model, *self._model_cache.get(spec.key, [])]:
            if name and name not in names:
                names.append(name)
        self._model.blockSignals(True)
        self._model.clear()
        self._model.addItems(names)
        self._model.setCurrentText(current)
        self._model.blockSignals(False)

        env_used = [var for var in spec.env_vars if os.environ.get(var)]
        if env_used:
            self._key_hint.setText(f"检测到环境变量 {' / '.join(env_used)}，它会优先于这里填的 key。")
        else:
            self._key_hint.setText(
                f"key 只存在本机 config.json 的 api_keys.{spec.key}；"
                "也可以设置 " + " / ".join(spec.env_vars) + " 环境变量。"
            )

    def _on_provider_changed(self, index: int) -> None:
        new_provider = self._provider.itemData(index)
        if not new_provider or new_provider == self._current:
            return
        self._stash()
        self._load_provider(new_provider)
        self._test_result.setText("")

    # ---------- 载入 ----------
    def _load_rest(self) -> None:
        chat = self._config.chat
        self._temperature.setValue(float(chat.temperature))
        self._max_tokens.setValue(int(chat.max_tokens))
        self._system_prompt.setPlainText(chat.system_prompt)
        self._size.setValue(int(self._config.pet.size))
        self._on_top.setChecked(bool(self._config.pet.always_on_top))
        self._rest.setValue(int(self._config.pet.rest_after_seconds))
        self._proxy.setText(self._config.chat.proxy)
        self._update_size_hint()

    def _load_memory(self) -> None:
        memory = self._config.memory
        self._memory_enabled.setChecked(bool(memory.enabled))
        self._memory_tokens.setValue(int(memory.context_tokens))
        self._refresh_memory_stats()
        self._refresh_facts()

    def _update_size_hint(self, *_args) -> None:
        physical = round(self._size.value() * self._dpr)
        self._size_hint.setText(
            f"当前屏幕缩放 {self._dpr:.0f}x，实际占用 {physical}×{physical} 物理像素"
            f"（512 素材在此尺寸下{'正好 1:1' if physical == 512 else '会被平滑缩放'}）。"
        )

    def _refresh_memory_stats(self) -> None:
        if hasattr(self, "_memory_path_label"):
            self._memory_path_label.setText(str(self._config.memory_path))
        store = self._memory
        if store is None:
            self._memory_stats.setText("未连接记忆（仅本次运行）")
            return
        self._memory_stats.setText(
            f"{len(store.turns)} 条对话 · 已折叠 {store.summarized} 条 · "
            f"整理 {store.compactions} 次 · 摘要 {len(store.summary)} 字"
        )

    def _refresh_facts(self) -> None:
        self._facts_list.clear()
        if self._facts is None:
            self._facts_list.addItem("未连接永久记忆")
            self._facts_list.setEnabled(False)
            return
        self._facts_list.setEnabled(True)
        for fact in self._facts.lines():
            self._facts_list.addItem(fact)
        if not self._facts.facts:
            self._facts_list.addItem("（还没有永久记忆，对她说「一定要记住…」即可）")

    # ---------- 动作 ----------
    def _clear_memory(self) -> None:
        if self._memory is None:
            return
        answer = QMessageBox.question(
            self, "清空记忆",
            "确定让她忘掉之前所有对话吗？磁盘上的 memory.json 会被清空，这一步不能撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._memory.clear()
            self._refresh_memory_stats()

    def _delete_fact(self) -> None:
        if self._facts is None:
            return
        row = self._facts_list.currentRow()
        if row >= 0 and self._facts.remove(row):
            self._refresh_facts()

    def _clear_facts(self) -> None:
        if self._facts is None or not self._facts.facts:
            return
        answer = QMessageBox.question(
            self, "清空永久记忆",
            "确定让她忘掉所有「一定要记住」的事吗？这一步不能撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._facts.clear()
            self._refresh_facts()

    def _collect(self) -> Config:
        self._stash()
        config = self._config
        config.chat.provider = self._current
        config.chat.api_keys = {k: v for k, v in self._keys.items() if v}
        config.chat.base_urls = {k: v for k, v in self._bases.items() if v}
        config.chat.models = {k: v for k, v in self._models.items() if v}
        config.chat.temperature = round(self._temperature.value(), 2)
        config.chat.max_tokens = int(self._max_tokens.value())
        config.chat.proxy = self._proxy.text().strip()
        config.chat.system_prompt = (
            self._system_prompt.toPlainText().strip() or DEFAULT_SYSTEM_PROMPT
        )
        config.pet.size = int(self._size.value())
        config.pet.always_on_top = self._on_top.isChecked()
        config.pet.rest_after_seconds = int(self._rest.value())
        config.memory.enabled = self._memory_enabled.isChecked()
        config.memory.context_tokens = int(self._memory_tokens.value())
        return config

    def result_config(self) -> Config:
        return self._collect()

    def _save(self) -> None:
        config = self._collect()
        self.applied.emit(config)
        self._saved = deepcopy(self._config)
        # 保存后收起窗口，任务栏图标一并收走，回到只有桌宠的状态
        self.hide()

    def _cancel(self) -> None:
        self._config = deepcopy(self._saved)
        self._keys = dict(self._config.chat.api_keys)
        self._bases = dict(self._config.chat.base_urls)
        self._models = dict(self._config.chat.models)
        self._load_provider(self._current)
        self._load_rest()
        self._load_memory()
        self.hide()

    # ---------- 网络动作 ----------
    def _busy(self, busy: bool) -> None:
        self._test_button.setEnabled(not busy)
        self._fetch.setEnabled(not busy)

    def _start(self, task, on_done) -> bool:
        """启动一个后台网络任务；已有任务在跑时返回 False。"""
        if self._worker is not None:
            try:
                if self._worker.isRunning():
                    return False
            except RuntimeError:
                # C++ 对象已被 deleteLater 回收，丢掉这个悬空引用
                self._worker = None
        worker = _NetWorker(task)
        worker.done.connect(on_done)
        worker.fail.connect(self._on_net_fail)
        worker.finished.connect(self._on_worker_finished)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        self._busy(True)
        worker.start()
        return True

    def _on_worker_finished(self) -> None:
        """线程结束：清掉引用并保证按钮一定恢复可用，避免界面像卡死。"""
        self._worker = None
        try:
            self._busy(False)
        except RuntimeError:
            pass

    def _on_net_fail(self, message: str) -> None:
        try:
            self._busy(False)
            self._test_result.setStyleSheet("color:#C0392B;font-size:11px;")
            self._test_result.setText(message)
        except RuntimeError:
            pass

    def _show_test(self, message: str) -> None:
        try:
            self._busy(False)
            self._test_result.setStyleSheet("color:#2E8B57;font-size:11px;")
            self._test_result.setText(message)
        except RuntimeError:
            pass

    def _test(self) -> None:
        spec = providers.get(self._current)
        key = self._key.text().strip() or providers.resolve_key(spec, self._keys)[0]
        if not key:
            self._test_result.setStyleSheet("color:#C0392B;font-size:11px;")
            self._test_result.setText("请先填写 API Key。")
            return
        base = self._base_url.text().strip()
        model = self._model.currentText().strip()
        self._test_result.setStyleSheet(f"color:{MUTED};font-size:11px;")
        self._test_result.setText("正在连接…")
        started = self._start(
            lambda: test_connection(
                spec.key, key, base, model,
                proxy=self._proxy.text().strip() or None,
                timeout=15.0,
            ),
            self._show_test,
        )
        if not started:
            self._test_result.setText("上一次请求还没结束，稍等一下再试…")

    def _fetch_models(self) -> None:
        spec = providers.get(self._current)
        key = self._key.text().strip() or providers.resolve_key(spec, self._keys)[0]
        if not key:
            self._test_result.setStyleSheet("color:#C0392B;font-size:11px;")
            self._test_result.setText("请先填写 API Key 再拉取模型。")
            return
        base = self._base_url.text().strip()
        self._test_result.setStyleSheet(f"color:{MUTED};font-size:11px;")
        self._test_result.setText("正在拉取模型列表…")
        started = self._start(
            lambda: list_models(
                spec.key, key, base,
                proxy=self._proxy.text().strip() or None,
                timeout=15.0,
            ),
            lambda names: self._on_models(spec, names),
        )
        if not started:
            self._test_result.setText("上一次请求还没结束，稍等一下再试…")

    def _on_models(self, spec: providers.ProviderSpec, names: object) -> None:
        try:
            self._busy(False)
            if not isinstance(names, list) or not names:
                self._on_net_fail("没有拿到模型列表。")
                return
            self._model_cache[spec.key] = [str(n) for n in names]
            current = self._model.currentText().strip() or spec.default_model
            items = [current] + [n for n in self._model_cache[spec.key] if n != current]
            self._model.clear()
            self._model.addItems(items)
            self._model.setCurrentText(current)
            self._test_result.setStyleSheet("color:#2E8B57;font-size:11px;")
            self._test_result.setText(f"拉到 {len(names)} 个可用模型，可从下拉里选。")
        except RuntimeError:
            pass

    # ---------- 窗口行为 ----------
    def show_and_focus(self) -> None:
        if self.isMinimized():
            self.showNormal()
        if not self._placed or not self._on_screen():
            self._centre_on_screen()
            self._placed = True
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_screen(self) -> bool:
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return False
        return screen.availableGeometry().intersects(self.frameGeometry())

    def _centre_on_screen(self) -> None:
        """首次打开居中，避免出现在屏幕左上角 (0,0)。"""
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(
            area.left() + (area.width() - self.width()) // 2,
            area.top() + (area.height() - self.height()) // 2,
        )

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh_memory_stats()
        self._refresh_facts()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        """✕ 与 Esc 都是收起窗口：任务栏图标随之消失，桌宠和托盘继续运行。"""
        if self._allow_close:
            super().closeEvent(event)
            return
        event.ignore()
        self.hide()

    def shutdown(self) -> None:
        """退出程序时真正关闭，不留后台窗口。"""
        self._allow_close = True
        worker = self._worker
        self._worker = None
        if worker is not None:
            try:
                if worker.isRunning():
                    worker.wait(1200)
            except RuntimeError:
                pass
        self.close()
