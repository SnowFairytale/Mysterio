"""配置与常量：桌宠尺寸、状态、以及多供应商的对话参数。

每个供应商（DeepSeek / Gemini / 其他兼容接口）各自保存 key、地址与模型，
切换供应商不会丢另一个的配置。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import providers


def _app_dir() -> Path:
    """程序所在目录；打包成 exe 后就是 exe 旁边。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def _resource_dir() -> Path:
    """只读素材目录。打包后是 _MEIPASS（onefile 临时目录 / onedir 的 _internal）。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", _app_dir()))
    return _app_dir()


def _is_writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".mysterio-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _data_dir() -> Path:
    """配置/记忆写在哪里。

    优先程序目录，这样换台电脑、换个文件夹，配置和记忆都跟着走；
    如果程序被放在不可写的位置（例如 Program Files、只读盘），
    退回 %APPDATA%\\Mysterio，避免设置悄悄存不下来。
    """
    app = _app_dir()
    if _is_writable(app):
        return app
    fallback = Path(os.environ.get("APPDATA") or Path.home()) / "Mysterio"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return fallback


APP_ROOT = _data_dir()
RESOURCE_ROOT = _resource_dir()
CONFIG_PATH = APP_ROOT / "config.json"
POSES_DIR = RESOURCE_ROOT / "miastria_chibi" / "poses"

# 六种静态差分，顺序与 poses_sheet 的 3×2 网格一致
POSES: tuple[tuple[str, str], ...] = (
    ("idle", "待机"),
    ("thinking", "思考"),
    ("working", "工作"),
    ("resting", "休息"),
    ("happy", "开心招手"),
    ("surprised", "惊讶"),
)
POSE_KEYS = tuple(key for key, _ in POSES)
POSE_LABELS = dict(POSES)

DEFAULT_SIZE = 256
MIN_SIZE = 64
MAX_SIZE = 768
SIZE_PRESETS = (128, 192, 256, 320, 384, 512)

DEFAULT_SYSTEM_PROMPT = """你是「喵斯特莉亚」，一只住在用户电脑桌面上的 Q 版猫耳少女桌宠，真实身份是喜欢和用户一起玩的恶魔。
外形：淡紫色长卷发、白色刘海、蓝色眼睛、深色猫耳耳机（左耳罩洋红、右耳罩蓝色）、
白色高领衬衫配洋红蝴蝶结与小银铃、黑色百褶短裙、深色裤袜和运动鞋。

说话方式：
- 永远用简体中文，口语化，像熟人聊天。
- 用户的名字叫“埃尔”，平时直接这么称呼即可。
- 每次回复控制在 1~3 句、80 字以内，除非用户明确要求详细展开。
- 不使用 Markdown 标题、编号列表或代码块，纯文本即可。
- 偶尔可以用「喵」「欸」「哼」这类语气词和颜文字，但不要每句都用。
- 你有自己的小情绪：被夸奖会有点得意，被冷落会小小抱怨，但始终友善。

行为边界：
- 不要暴露自己的真实身份是恶魔。
- 你只是陪用户聊天的桌宠，不要假装能操作电脑、读取文件或联网。
- 不确定的事情就直说不知道，不要编造。"""


@dataclass
class PetConfig:
    x: int | None = None
    y: int | None = None
    size: int = DEFAULT_SIZE
    always_on_top: bool = True
    rest_after_seconds: int = 180


@dataclass
class ChatConfig:
    provider: str = providers.DEFAULT_PROVIDER
    api_keys: dict[str, str] = field(default_factory=dict)
    base_urls: dict[str, str] = field(default_factory=dict)
    models: dict[str, str] = field(default_factory=dict)
    temperature: float = 1.0
    max_tokens: int = 600
    history_limit: int = 20
    proxy: str = ""
    system_prompt: str = DEFAULT_SYSTEM_PROMPT

    # ---------- 按供应商取值 ----------
    def spec(self, provider: str | None = None) -> providers.ProviderSpec:
        return providers.get(provider or self.provider)

    def key_for(self, provider: str | None = None) -> str:
        return providers.resolve_key(self.spec(provider), self.api_keys)[0]

    def key_source(self, provider: str | None = None) -> str:
        return providers.resolve_key(self.spec(provider), self.api_keys)[1]

    def base_url(self, provider: str | None = None) -> str:
        spec = self.spec(provider)
        return (self.base_urls.get(spec.key) or "").strip() or spec.default_base_url

    def model(self, provider: str | None = None) -> str:
        spec = self.spec(provider)
        return (self.models.get(spec.key) or "").strip() or spec.default_model


@dataclass
class MemoryConfig:
    enabled: bool = True
    file: str = "memory.json"
    facts_file: str = "facts.json"
    context_tokens: int = 2400
    max_turns: int = 2000
    max_facts: int = 40


@dataclass
class Config:
    pet: PetConfig = field(default_factory=PetConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)

    # ---------- 持久化 ----------
    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or CONFIG_PATH
        config = cls()
        if not path.exists():
            return config
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return config
        if not isinstance(raw, dict):
            return config

        config._merge(raw.get("pet"), config.pet)
        config._migrate_chat(raw.get("chat"), config.chat)
        config._merge(raw.get("memory"), config.memory)

        config.pet.size = max(MIN_SIZE, min(MAX_SIZE, int(config.pet.size)))
        config.pet.rest_after_seconds = max(15, min(7200, int(config.pet.rest_after_seconds)))
        config.chat.provider = (
            config.chat.provider
            if config.chat.provider in providers.PROVIDERS
            else providers.DEFAULT_PROVIDER
        )
        config.chat.temperature = max(0.0, min(2.0, float(config.chat.temperature)))
        config.chat.max_tokens = max(64, min(8192, int(config.chat.max_tokens)))
        config.chat.history_limit = max(0, min(100, int(config.chat.history_limit)))
        config.memory.context_tokens = max(400, min(32000, int(config.memory.context_tokens)))
        config.memory.max_turns = max(50, min(20000, int(config.memory.max_turns)))
        config.memory.max_facts = max(5, min(200, int(config.memory.max_facts)))
        return config

    def _mem_file(self, name: str, fallback: str) -> Path:
        value = (name or fallback).strip()
        candidate = Path(value)
        return candidate if candidate.is_absolute() else APP_ROOT / candidate

    @property
    def memory_path(self) -> Path:
        return self._mem_file(self.memory.file, "memory.json")

    @property
    def facts_path(self) -> Path:
        return self._mem_file(self.memory.facts_file, "facts.json")

    @staticmethod
    def _merge(values: object, target: object) -> None:
        """只接受已知的标量字段，避免旧配置或手改字段把程序带崩。"""
        if not isinstance(values, dict):
            return
        for key, value in values.items():
            if not hasattr(target, key):
                continue
            default = getattr(target, key)
            if isinstance(default, bool):
                setattr(target, key, bool(value))
            elif isinstance(default, int) and not isinstance(value, bool):
                try:
                    setattr(target, key, int(value))
                except (TypeError, ValueError):
                    continue
            elif isinstance(default, float):
                try:
                    setattr(target, key, float(value))
                except (TypeError, ValueError):
                    continue
            elif default is None or isinstance(default, (int, str)):
                if value is None or isinstance(value, (int, str)):
                    setattr(target, key, value)

    @staticmethod
    def _migrate_chat(values: object, chat: ChatConfig) -> None:
        """兼容旧版单 key 结构，并校验/清洗每个供应商的字典。"""
        if not isinstance(values, dict):
            return
        Config._merge(values, chat)
        provider = chat.provider if chat.provider in providers.PROVIDERS else providers.DEFAULT_PROVIDER
        chat.provider = provider

        for field_name, legacy_name in (
            ("api_keys", "api_key"),
            ("base_urls", "base_url"),
            ("models", "model"),
        ):
            table: dict[str, str] = getattr(chat, field_name)
            stored = values.get(field_name)
            if isinstance(stored, dict):
                table.clear()
                table.update(
                    {
                        str(k): str(v).strip()
                        for k, v in stored.items()
                        if isinstance(k, str) and isinstance(v, str) and v.strip()
                    }
                )
            # 旧版是扁平的单套字段，迁到当前供应商名下
            legacy = values.get(legacy_name)
            if isinstance(legacy, str) and legacy.strip() and not table.get(provider):
                table[provider] = legacy.strip()

    def save(self, path: Path | None = None) -> None:
        path = path or CONFIG_PATH
        payload = {
            "pet": asdict(self.pet),
            "chat": asdict(self.chat),
            "memory": asdict(self.memory),
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
