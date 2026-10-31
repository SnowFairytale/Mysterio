"""无边框透明宠物窗口：拖动、滚轮缩放、右键菜单。

窗口只负责“显示哪张图”和鼠标交互；什么时候该显示哪张由 behavior.PetBehavior 决定。
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QPainter, QPixmap
from PyQt6.QtWidgets import QInputDialog, QMenu, QWidget

from .config import DEFAULT_SIZE, MAX_SIZE, MIN_SIZE, POSE_KEYS, SIZE_PRESETS, Config
from .sprites import SpriteStore

DRAG_THRESHOLD = 4  # 逻辑像素，超过才算拖动而不是“戳一下”


class PetWindow(QWidget):
    chatRequested = pyqtSignal()
    settingsRequested = pyqtSignal()
    quitRequested = pyqtSignal()
    hideRequested = pyqtSignal()
    sizeChanged = pyqtSignal(int)
    poseChanged = pyqtSignal(str)
    moved = pyqtSignal()
    poked = pyqtSignal()
    interacted = pyqtSignal()

    def __init__(self, config: Config, sprites: SpriteStore) -> None:
        super().__init__()
        self.config = config
        self.sprites = sprites
        self._pose = "idle"
        self._pixmap = QPixmap()
        self._press_global: QPoint | None = None
        self._press_origin: QPoint | None = None
        self._pressed = False
        self._drag_moved = False

        # 单击后等一下再判定为“戳”，避免和双击聊天抢事件
        self._poke_timer = QTimer(self)
        self._poke_timer.setSingleShot(True)
        self._poke_timer.setInterval(240)
        self._poke_timer.timeout.connect(self._emit_poke)

        self.setWindowTitle("喵斯特莉亚")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("左键拖动 · 单击戳一下 · 双击聊天 · 滚轮缩放 · 右键菜单")

        self.setFixedSize(config.pet.size, config.pet.size)
        self._refresh_pixmap()
        self._restore_position()

    # ---------- 显示 ----------
    def current_pose(self) -> str:
        return self._pose

    def set_pose(self, pose: str) -> None:
        """只改画面，不写配置：状态由 PetBehavior 统一调度。"""
        if pose not in POSE_KEYS or pose == self._pose:
            return
        self._pose = pose
        self._refresh_pixmap()
        self.poseChanged.emit(pose)

    def apply_size(self, size: int, *, persist: bool = True) -> None:
        size = max(MIN_SIZE, min(MAX_SIZE, int(size)))
        if size == self.width():
            return
        self.setFixedSize(size, size)
        self.config.pet.size = size
        self._refresh_pixmap()
        self._clamp_to_screen()
        if persist:
            self.config.save()
        self.sizeChanged.emit(size)

    def physical_size(self) -> int:
        return round(self.width() * self.devicePixelRatioF())

    def _refresh_pixmap(self) -> None:
        dpr = max(1.0, self.devicePixelRatioF())
        self._pixmap = self.sprites.scaled(self._pose, self.width(), self.height(), dpr)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if not self._pixmap.isNull():
            painter.drawPixmap(0, 0, self._pixmap)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_pixmap()
        self.moved.emit()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self.moved.emit()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None:
            try:
                handle.screenChanged.disconnect(self._on_screen_changed)
            except TypeError:
                pass
            handle.screenChanged.connect(self._on_screen_changed)

    def _on_screen_changed(self, *_args) -> None:
        self.sprites.clear()
        self._refresh_pixmap()

    def enterEvent(self, event) -> None:  # noqa: N802
        self.interacted.emit()
        super().enterEvent(event)

    # ---------- 位置 ----------
    def _restore_position(self) -> None:
        area = self.screen().availableGeometry() if self.screen() else None
        if self.config.pet.x is None or self.config.pet.y is None:
            # 默认落在左下角：右下角常被各类常驻悬浮窗占用
            if area is not None:
                self.move(area.left() + 40, area.bottom() - self.height() - 60)
            return
        self.move(self.config.pet.x, self.config.pet.y)
        self._clamp_to_screen()

    def _clamp_to_screen(self) -> None:
        screen = self.screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = max(area.left(), min(self.x(), area.right() - self.width() + 1))
        y = max(area.top(), min(self.y(), area.bottom() - self.height() + 1))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def _save_position(self) -> None:
        self.config.pet.x = self.x()
        self.config.pet.y = self.y()
        self.config.save()

    # ---------- 鼠标 ----------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._poke_timer.stop()
            self._press_global = event.globalPosition().toPoint()
            self._press_origin = self.pos()
            self._pressed = True
            self._drag_moved = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.interacted.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._pressed and self._press_global is not None and self._press_origin is not None:
            delta = event.globalPosition().toPoint() - self._press_global
            if not self._drag_moved and delta.manhattanLength() >= DRAG_THRESHOLD:
                self._drag_moved = True
            if self._drag_moved:
                self.move(self._press_origin + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            self._press_global = None
            self._press_origin = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if self._drag_moved:
                self._clamp_to_screen()
                self._save_position()
            else:
                self._poke_timer.start()
            self.interacted.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._poke_timer.stop()
            self.chatRequested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _emit_poke(self) -> None:
        self.poked.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            return
        step = max(8, round(self.width() * 0.06))
        self.apply_size(self.width() + (step if delta > 0 else -step))
        self.interacted.emit()
        event.accept()

    # ---------- 菜单 ----------
    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.interacted.emit()
        menu = QMenu(self)
        menu.addAction("和她说话    (双击)", self.chatRequested.emit)
        menu.addSeparator()

        size_menu = menu.addMenu("尺寸")
        for preset in SIZE_PRESETS:
            action = QAction(f"{preset} × {preset}", size_menu, checkable=True)
            action.setChecked(preset == self.width())
            action.triggered.connect(lambda _checked=False, s=preset: self.apply_size(s))
            size_menu.addAction(action)
        size_menu.addSeparator()
        current = QAction(
            f"当前：逻辑 {self.width()} / 物理 {self.physical_size()} px", size_menu
        )
        current.setEnabled(False)
        size_menu.addAction(current)
        size_menu.addAction("自定义（逻辑像素）…", self._ask_logical_size)
        size_menu.addAction("按物理像素设置…", self._ask_physical_size)
        size_menu.addAction("恢复默认 256", lambda: self.apply_size(DEFAULT_SIZE))

        menu.addSeparator()
        top_action = QAction("总在最前", menu, checkable=True, checked=self.config.pet.always_on_top)
        top_action.toggled.connect(self.set_always_on_top)
        menu.addAction(top_action)
        menu.addAction("设置…", self.settingsRequested.emit)
        menu.addAction("隐藏到托盘", self.hideRequested.emit)
        menu.addAction("退出", self.quitRequested.emit)
        menu.exec(event.globalPos())

    def _ask_logical_size(self) -> None:
        value, ok = QInputDialog.getInt(
            self, "自定义尺寸", "逻辑像素（128~512 比较合适）：",
            self.width(), MIN_SIZE, MAX_SIZE, 8,
        )
        if ok:
            self.apply_size(value)

    def _ask_physical_size(self) -> None:
        dpr = max(1.0, self.devicePixelRatioF())
        value, ok = QInputDialog.getInt(
            self, "按物理像素设置",
            f"屏幕缩放 {dpr:.0f}x，物理像素（会换算成逻辑尺寸）：",
            self.physical_size(), MIN_SIZE, 1600, 16,
        )
        if ok:
            self.apply_size(round(value / dpr))

    def set_always_on_top(self, enabled: bool) -> None:
        if enabled == self.config.pet.always_on_top and bool(
            self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
        ) == enabled:
            return
        self.config.pet.always_on_top = enabled
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if self.isVisible():
            self.show()
        self.config.save()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._save_position()
        super().closeEvent(event)
