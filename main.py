"""桌宠入口。

用法：
    conda activate dip
    python main.py
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime

from PyQt6.QtCore import QObject, Qt
from PyQt6.QtGui import QAction, QGuiApplication, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from pet.behavior import HAPPY, SURPRISED, THINKING, WORKING, PetBehavior
from pet.bubble import ChatBubble
from pet.chat import ChatWorker, MemoryWorker
from pet.config import APP_ROOT, CONFIG_PATH, POSES_DIR, RESOURCE_ROOT, Config
from pet.facts import FactStore
from pet.memory import MemoryStore
from pet.settings import SettingsWindow
from pet.sprites import SpriteStore
from pet.window import PetWindow

LOG_PATH = APP_ROOT / "pet.log"


def log(message: str) -> None:
    """打包成 --windowed 后没有控制台，所有异常写到 exe 旁边的 pet.log。"""
    try:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        pass


def _excepthook(exc_type, exc, tb) -> None:
    log("未捕获异常：\n" + "".join(traceback.format_exception(exc_type, exc, tb)))
    sys.__excepthook__(exc_type, exc, tb)


class ChatController(QObject):
    """把「发送 → 流式回复 → 表情切换」串起来。"""

    def __init__(
        self,
        config: Config,
        behavior: PetBehavior,
        bubble: ChatBubble,
        memory: MemoryStore,
        facts: FactStore,
    ) -> None:
        super().__init__()
        self.config = config
        self.behavior = behavior
        self.bubble = bubble
        self.memory = memory
        self.facts = facts
        self._worker: ChatWorker | None = None
        self._memory_worker: MemoryWorker | None = None
        self._receiving = False
        bubble.submitted.connect(self.send)

    # ---------- 对外 ----------
    def send(self, text: str) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.bubble.add_system("她还在想上一句，等一等喵。", "#ffd9a0")
            return

        key = self.config.chat.key_for()
        if not key:
            spec = self.config.chat.spec()
            self.bubble.show_error(
                f"还没有 {spec.label} 的 API Key。右键桌宠 → 设置 → 连接，"
                f"选好供应商并粘贴 key 后保存；也可以设置环境变量 "
                f"{' 或 '.join(spec.env_vars)}。"
            )
            self.behavior.flash(SURPRISED, 1800)
            return

        self.memory.add("user", text)
        remembered = self.facts.offer(text) if self.config.memory.enabled else None
        if remembered:
            log(f"写入永久记忆: {remembered}")
            self.bubble.add_system(f"（记住了：{remembered}）", "#8fe0a0")

        system = self.config.chat.system_prompt
        if self.config.memory.enabled:
            block = self.facts.prompt_block()
            if block:
                system = f"{system}\n\n{block}"
        messages, info = self.memory.context(system)
        if info.compacted:
            log(f"记忆折叠: 淘汰 {info.evicted} 条, 摘要 {len(self.memory.summary)} 字, "
                f"上下文 {info.tokens} tokens")
            self.bubble.add_system(
                f"（记忆整理：最早的 {info.evicted} 条对话收进摘要了）", "#9fd0ff"
            )
        self._receiving = False
        self.behavior.set_busy(THINKING)
        self.bubble.begin_reply()

        worker = ChatWorker(
            api_key=key,
            messages=messages,
            provider=self.config.chat.provider,
            base_url=self.config.chat.base_url(),
            model=self.config.chat.model(),
            temperature=self.config.chat.temperature,
            max_tokens=self.config.chat.max_tokens,
            proxy=self.config.chat.proxy or None,
        )
        worker.chunk.connect(self._on_chunk)
        worker.succeeded.connect(self._on_success)
        worker.failed.connect(self._on_failure)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def stop(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(1500)
        self._worker = None
        if self._memory_worker is not None and self._memory_worker.isRunning():
            self._memory_worker.wait(1500)
        self._memory_worker = None
        self.behavior.set_busy(None)

    def reset_history(self) -> None:
        self.memory.clear()

    # ---------- 内部 ----------
    def _on_chunk(self, piece: str) -> None:
        if not self._receiving:
            self._receiving = True
            self.behavior.set_busy(WORKING)  # 开始出字：她在“打字”
        self.bubble.add_chunk(piece)

    def _on_success(self, text: str) -> None:
        self.bubble.end_reply(text)
        self.memory.add("assistant", text)
        self.memory.save()
        self._worker = None
        self.behavior.set_busy(None)
        self.behavior.flash(HAPPY, 1800)
        self._maybe_consolidate()

    def _maybe_consolidate(self) -> None:
        """折叠发生后，后台把「旧摘要 + 被淘汰原文」交给模型压成真正的记忆。"""
        if not self.config.memory.enabled or not self.memory.has_pending():
            return
        key = self.config.chat.key_for()
        if not key:
            return
        previous, turns = self.memory.take_pending()
        worker = MemoryWorker(
            provider=self.config.chat.provider,
            api_key=key,
            previous_summary=previous,
            turns=turns,
            limit_chars=self.memory.summary_max_chars,
            base_url=self.config.chat.base_url(),
            model=self.config.chat.model(),
            proxy=self.config.chat.proxy or None,
        )
        worker.succeeded.connect(self._on_memory_ready)
        worker.failed.connect(lambda message: log(f"记忆整理失败: {message}"))
        worker.finished.connect(worker.deleteLater)
        self._memory_worker = worker
        worker.start()

    def _on_memory_ready(self, text: str) -> None:
        if self.memory.apply_summary(text):
            log(f"记忆已整理: {len(text)} 字")
            if self.bubble.isVisible():
                self.bubble.add_system("（记忆整理好了，早期对话压缩成摘要）", "#9fd0ff")

    def _on_failure(self, message: str) -> None:
        self.bubble.show_error(message)
        self.memory.drop_last("user")
        self.memory.save()
        self._worker = None
        self.behavior.set_busy(None)
        self.behavior.flash(SURPRISED, 1800)


def toggle_bubble(window: PetWindow, bubble: ChatBubble, behavior: PetBehavior) -> None:
    behavior.touch()
    if bubble.isVisible():
        bubble.hide()
        return
    if not window.isVisible():
        window.show()
        window.raise_()
    bubble.pop_up(window.geometry())
    behavior.flash(HAPPY, 1200)  # 有人来聊天，先开心一下


def _selftest() -> int:
    """打包后自检：确认素材、配置路径、TLS 与 API 连通性（结果写 pet.log）。"""
    import ssl

    config = Config.load()
    spec = config.chat.spec()
    log(f"自检: ssl={ssl.OPENSSL_VERSION}")
    log(f"自检: 素材目录 {POSES_DIR} 存在={POSES_DIR.exists()}")
    log(f"自检: 配置 {CONFIG_PATH} 存在={CONFIG_PATH.exists()} provider={spec.key}")
    try:
        from pet.chat import test_connection

        message = test_connection(
            spec.key,
            config.chat.key_for(),
            config.chat.base_url(),
            config.chat.model(),
            proxy=config.chat.proxy or None,
        )
        log(f"自检: {message}")
        return 0
    except Exception as exc:
        log(f"自检失败: {exc}")
        return 1


def main() -> int:
    sys.excepthook = _excepthook
    log(
        f"启动 frozen={getattr(sys, 'frozen', False)} "
        f"app_root={APP_ROOT} resource={RESOURCE_ROOT} config={CONFIG_PATH}"
    )
    if "--selftest" in sys.argv:
        return _selftest()
    try:
        return _run()
    except Exception:
        log("启动失败：\n" + traceback.format_exc())
        raise


def _run() -> int:
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Mysterio 桌宠")
    app.setApplicationDisplayName("喵斯特莉亚")
    app.setQuitOnLastWindowClosed(False)

    config = Config.load()
    if not CONFIG_PATH.exists():
        config.save()  # 首次启动落盘，方便手改
    sprites = SpriteStore()
    window = PetWindow(config, sprites)
    bubble = ChatBubble()
    behavior = PetBehavior(window, rest_after_ms=config.pet.rest_after_seconds * 1000)
    memory = MemoryStore.load(
        config.memory_path,
        persist=config.memory.enabled,
        context_tokens=config.memory.context_tokens,
        max_turns=config.memory.max_turns,
    )
    facts = FactStore.load(
        config.facts_path,
        max_facts=config.memory.max_facts,
        persist=config.memory.enabled,
    )
    controller = ChatController(config, behavior, bubble, memory, facts)

    # 设置窗口按需创建：启动时不建、不显示，桌面上只有桌宠和托盘图标
    settings: SettingsWindow | None = None

    # ---------- 动作 ----------
    def apply_settings(updated: Config) -> None:
        config.pet.size = updated.pet.size
        config.pet.always_on_top = updated.pet.always_on_top
        config.pet.rest_after_seconds = updated.pet.rest_after_seconds
        config.chat = updated.chat
        config.memory = updated.memory
        config.save()
        memory.persist = config.memory.enabled
        memory.context_tokens = config.memory.context_tokens
        memory.max_turns = config.memory.max_turns
        facts.persist = config.memory.enabled
        facts.max_facts = config.memory.max_facts
        window.apply_size(config.pet.size)
        window.set_always_on_top(config.pet.always_on_top)
        behavior.set_rest_after(config.pet.rest_after_seconds)
        if bubble.isVisible():
            bubble.add_system("设置已保存", "#8fe0a0")

    def open_settings() -> None:
        nonlocal settings
        behavior.touch()
        if settings is None:
            settings = SettingsWindow(config, window.devicePixelRatioF(), memory, facts)
            settings.applied.connect(apply_settings)
        settings.show_and_focus()

    def hide_to_tray() -> None:
        bubble.hide()
        window.hide()

    def shutdown() -> None:
        controller.stop()
        behavior.shutdown()
        memory.save()
        config.save()
        if settings is not None:
            settings.shutdown()

    def quit_app() -> None:
        shutdown()
        if tray is not None:
            tray.hide()
        app.quit()

    window.chatRequested.connect(lambda: toggle_bubble(window, bubble, behavior))
    window.settingsRequested.connect(open_settings)
    window.hideRequested.connect(hide_to_tray)
    window.quitRequested.connect(quit_app)
    window.moved.connect(lambda: bubble.follow(window.geometry()))
    window.poked.connect(behavior.poke)
    window.interacted.connect(behavior.touch)
    bubble.closed.connect(behavior.touch)
    app.aboutToQuit.connect(shutdown)

    # ---------- 托盘 ----------
    tray: QSystemTrayIcon | None = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        icon = QIcon(sprites.scaled("idle", 64, 64, 1.0))
        tray = QSystemTrayIcon(icon, app)
        tray.setToolTip("喵斯特莉亚 · 双击和我说话")
        menu = QMenu()
        show_action = QAction("显示桌宠", menu)
        show_action.triggered.connect(
            lambda: (window.show(), window.raise_(), window.activateWindow(), behavior.touch())
        )
        chat_action = QAction("和她说话", menu)
        chat_action.triggered.connect(lambda: toggle_bubble(window, bubble, behavior))
        settings_action = QAction("设置…", menu)
        settings_action.triggered.connect(open_settings)
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(quit_app)
        menu.addAction(show_action)
        menu.addAction(chat_action)
        menu.addSeparator()
        menu.addAction(settings_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        tray.setContextMenu(menu)

        def on_tray_activated(reason: QSystemTrayIcon.ActivationReason) -> None:
            if reason in (
                QSystemTrayIcon.ActivationReason.Trigger,
                QSystemTrayIcon.ActivationReason.DoubleClick,
            ):
                toggle_bubble(window, bubble, behavior)

        tray.activated.connect(on_tray_activated)
        tray.show()

    window.set_always_on_top(config.pet.always_on_top)
    window.show()
    window.raise_()

    if tray is not None and not config.chat.key_for():
        spec = config.chat.spec()
        tray.showMessage(
            "喵斯特莉亚",
            f"右键我 → 设置 → 连接，选 {spec.label} 并填上 API Key 就能聊天啦。",
            QSystemTrayIcon.MessageIcon.Information,
            6000,
        )

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
