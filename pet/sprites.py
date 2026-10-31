"""精灵加载：512×512 原图按显示尺寸与屏幕缩放预缩放并缓存。

素材本身不压缩，只按「逻辑尺寸 × devicePixelRatio」生成设备像素副本；
200% 缩放的 3200×2000 屏上，256 逻辑像素正好等于 512 物理像素，即原图 1:1。
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QPixmap

from .config import POSE_KEYS, POSES_DIR


class SpriteStore:
    def __init__(self, directory: Path = POSES_DIR) -> None:
        self.directory = Path(directory)
        self._sources: dict[str, QPixmap] = {}
        self._cache: dict[tuple[str, int, int, float], QPixmap] = {}

    def load(self, pose: str) -> QPixmap:
        source = self._sources.get(pose)
        if source is None:
            path = self.directory / f"{pose}.png"
            pixmap = QPixmap(str(path))
            if pixmap.isNull():
                raise FileNotFoundError(f"缺少素材或无法读取：{path}")
            source = pixmap
            self._sources[pose] = source
        return source

    def preload(self) -> list[str]:
        return [pose for pose in POSE_KEYS if not self.load(pose).isNull()]

    def scaled(self, pose: str, width: int, height: int, dpr: float) -> QPixmap:
        device = QSize(max(1, round(width * dpr)), max(1, round(height * dpr)))
        key = (pose, device.width(), device.height(), dpr)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        source = self.load(pose)
        # 保持宽高比缩放，正方形素材直接铺满目标框
        pixmap = source.scaled(
            device,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pixmap.setDevicePixelRatio(dpr)
        self._cache[key] = pixmap
        return pixmap

    def clear(self) -> None:
        self._cache.clear()
