"""表情状态机：待机是常态，其余立绘由具体条件触发。

优先级（高到低）：
1. flash —— 短暂反应：回答完 `happy`、出错 `surprised`、被戳 `happy/surprised`，被打断也会先给反应
2. busy —— 正在和 DeepSeek 交互：等待回答 `thinking`，收字过程中 `working`
3. base —— 长时间无操作 `resting`，否则 `idle`

任何鼠标操作、聊天、改尺寸都会调用 touch()，把计时重置并叫醒她。
"""
from __future__ import annotations

import random

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .config import POSE_KEYS

IDLE = "idle"
THINKING = "thinking"
WORKING = "working"
RESTING = "resting"
HAPPY = "happy"
SURPRISED = "surprised"

POKE_REACTIONS = (HAPPY, SURPRISED)
DEFAULT_REST_AFTER_MS = 180_000


class PetBehavior(QObject):
    """把“什么时候显示哪张图”集中到一处，窗口只管画。"""

    poseChanged = pyqtSignal(str)

    def __init__(self, window, *, rest_after_ms: int = DEFAULT_REST_AFTER_MS) -> None:
        super().__init__(window)
        self.window = window
        self._base = IDLE
        self._busy: str | None = None
        self._flash: str | None = None
        self._rest_after_ms = max(15_000, int(rest_after_ms))

        self._rest_timer = QTimer(self)
        self._rest_timer.setSingleShot(True)
        self._rest_timer.timeout.connect(self._fall_asleep)

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)

        self._apply(IDLE)
        self._rest_timer.start(self._rest_after_ms)

    # ---------- 查询 ----------
    @property
    def pose(self) -> str:
        return self.window.current_pose()

    @property
    def busy(self) -> str | None:
        return self._busy

    def _resolve(self) -> str:
        if self._flash:
            return self._flash
        if self._busy:
            return self._busy
        return self._base

    def _apply(self, pose: str) -> None:
        if pose not in POSE_KEYS:
            return
        if self.window.current_pose() != pose:
            self.window.set_pose(pose)
            self.poseChanged.emit(pose)

    def _render(self) -> None:
        self._apply(self._resolve())

    # ---------- 交互 ----------
    def touch(self) -> None:
        """有交互：叫醒并重新开始休息计时。"""
        if self._base == RESTING:
            self._base = IDLE
            self._render()
        self._rest_timer.start(self._rest_after_ms)

    def poke(self) -> str:
        """被戳一下：随机开心或惊讶。"""
        self.touch()
        reaction = random.choice(POKE_REACTIONS)
        self.flash(reaction, 1400)
        return reaction

    # ---------- 状态驱动 ----------
    def set_busy(self, pose: str | None) -> None:
        """进入/退出对话忙碌态：thinking（等首字）或 working（正在出字）。"""
        if pose is not None and pose not in POSE_KEYS:
            return
        self.touch()
        self._busy = pose
        self._render()

    def flash(self, pose: str, ms: int = 1600) -> None:
        """短暂表情，到点回到 busy 或 base。"""
        if pose not in POSE_KEYS:
            return
        self.touch()
        self._flash = pose
        self._flash_timer.start(max(200, int(ms)))
        self._render()

    def rest_now(self) -> None:
        """立刻休息（用于测试或手动睡着）。"""
        if self._busy or self._flash:
            return
        self._base = RESTING
        self._render()

    def set_rest_after(self, seconds: int) -> None:
        """改“多久没动静就休息”，立即按新时长重新计时。"""
        self._rest_after_ms = max(15_000, int(seconds) * 1000)
        self._rest_timer.start(self._rest_after_ms)

    # ---------- 内部 ----------
    def _end_flash(self) -> None:
        self._flash = None
        self._render()

    def _fall_asleep(self) -> None:
        if self._busy or self._flash:
            self._rest_timer.start(self._rest_after_ms)
            return
        self._base = RESTING
        self._render()

    def shutdown(self) -> None:
        self._rest_timer.stop()
        self._flash_timer.stop()
