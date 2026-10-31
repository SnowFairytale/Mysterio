"""对话客户端：按供应商组装请求、解析 SSE、在 QThread 里跑。

网络请求一律放在工作线程，主线程只收信号，界面不会卡。
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

import httpx
from PyQt6.QtCore import QThread, pyqtSignal

from . import providers
from .providers import Message


class ChatError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _client(
    transport: httpx.BaseTransport | None,
    timeout: float,
    proxy: str | None = None,
) -> httpx.Client:
    return httpx.Client(
        # 注入了 transport（测试用）时以它为准，代理只作用于真实网络
        transport=transport,
        proxy=(proxy or None) if transport is None else None,
        timeout=httpx.Timeout(timeout, connect=10.0),
        follow_redirects=True,
        headers={"User-Agent": "Mysterio-DesktopPet/1.1"},
    )


def _error_body(response: httpx.Response) -> str:
    try:
        return response.read().decode("utf-8", "replace")
    except Exception:  # pragma: no cover - 读取失败就用状态码兜底
        return ""


def build_messages(
    system_prompt: str,
    history: Iterable[Message],
    limit: int,
) -> list[Message]:
    """system + 最近若干轮，保证第一条永远是 system。"""
    trimmed = list(history)[-max(0, limit * 2):] if limit > 0 else []
    return [{"role": "system", "content": system_prompt}, *trimmed]


class ChatWorker(QThread):
    """一次流式对话，供应商差异全部交给 providers。"""

    chunk = pyqtSignal(str)
    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(
        self,
        *,
        api_key: str,
        messages: Sequence[Message],
        provider: str = providers.DEFAULT_PROVIDER,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float = 1.0,
        max_tokens: int = 600,
        proxy: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 90.0,
    ) -> None:
        super().__init__()
        self.api_key = api_key
        self.messages = list(messages)
        self.provider = provider
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.proxy = proxy
        self._transport = transport
        self._timeout = timeout
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def run(self) -> None:  # noqa: C901 - 分流式解析，保持线性更好读
        spec = providers.get(self.provider)
        if not self.api_key:
            self.failed.emit(f"还没有配置 {spec.label} 的 API Key，右键桌宠 → 设置 里填一下喵。")
            return

        url = providers.endpoint(spec, self.base_url, self.model, stream=True)
        body = providers.payload(
            spec, self.messages, self.model, self.temperature, self.max_tokens, stream=True
        )
        headers = providers.headers(spec, self.api_key)
        headers["Accept"] = "text/event-stream"

        collected: list[str] = []
        try:
            with _client(self._transport, self._timeout, self.proxy) as client:
                with client.stream("POST", url, json=body, headers=headers) as response:
                    if response.status_code >= 400:
                        raise ChatError(
                            providers.error_message(spec, response.status_code, _error_body(response)),
                            response.status_code,
                        )

                    for raw in response.iter_lines():
                        if self._cancelled:
                            break
                        line = raw.strip()
                        if not line:
                            continue
                        if line.startswith("data:"):
                            data = line[5:].strip()
                        elif line.startswith("{"):  # 有些网关不带 data: 前缀
                            data = line
                        else:
                            continue
                        if data == "[DONE]":
                            break
                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        blocked = providers.blocked_reason(spec, event)
                        if blocked:
                            raise ChatError(blocked)
                        piece = providers.extract_delta(spec, event)
                        if piece:
                            collected.append(piece)
                            self.chunk.emit(piece)
        except ChatError as exc:
            self.failed.emit(str(exc))
            return
        except httpx.TimeoutException:
            self.failed.emit("等太久了，服务端没有响应，检查一下网络再试试？")
            return
        except httpx.HTTPError as exc:
            self.failed.emit(f"网络请求失败：{exc.__class__.__name__}，检查代理或网络连接喵。")
            return
        except Exception as exc:  # pragma: no cover - 兜底，别让线程静默死掉
            self.failed.emit(f"出错了：{exc}")
            return

        text = "".join(collected).strip()
        if self._cancelled:
            return
        if not text:
            self.failed.emit("她这次什么也没说出来，再问一次试试？")
            return
        self.succeeded.emit(text)


def test_connection(
    provider: str,
    api_key: str,
    base_url: str | None = None,
    model: str | None = None,
    proxy: str | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 20.0,
) -> str:
    """设置面板里的「测试连接」：发一条极短请求验证 key、地址与模型。"""
    spec = providers.get(provider)
    if not api_key:
        raise ChatError("请先填写 API Key。")
    url = providers.endpoint(spec, base_url, model, stream=False)
    body = providers.payload(
        spec, [{"role": "user", "content": "ping"}], model, 0.0, 1, stream=False
    )
    try:
        with _client(transport, timeout, proxy) as client:
            response = client.post(url, json=body, headers=providers.headers(spec, api_key))
    except httpx.TimeoutException as exc:
        raise ChatError("连接超时，检查网络或代理设置。") from exc
    except httpx.HTTPError as exc:
        raise ChatError(f"网络不可用：{exc.__class__.__name__}") from exc

    if response.status_code >= 400:
        raise ChatError(
            providers.error_message(spec, response.status_code, response.text),
            response.status_code,
        )
    name = (model or spec.default_model).strip()
    if spec.style == providers.GEMINI_STYLE:
        try:
            data: Any = response.json()
        except ValueError:
            data = {}
        if isinstance(data, dict) and not data.get("candidates") and data.get("promptFeedback"):
            return f"连通了，但请求被安全策略拦下；模型 {name} 可用。"
    return f"连接成功，{spec.label} 的 {name} 可用。"


def list_models(
    provider: str,
    api_key: str,
    base_url: str | None = None,
    proxy: str | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 20.0,
) -> list[str]:
    """拉取账号可用的模型名，避免模型改名后到处试。"""
    spec = providers.get(provider)
    if not api_key:
        raise ChatError("请先填写 API Key。")
    try:
        with _client(transport, timeout, proxy) as client:
            response = client.get(
                providers.models_endpoint(spec, base_url),
                headers=providers.headers(spec, api_key),
            )
    except httpx.TimeoutException as exc:
        raise ChatError("拉取模型列表超时。") from exc
    except httpx.HTTPError as exc:
        raise ChatError(f"网络不可用：{exc.__class__.__name__}") from exc

    if response.status_code >= 400:
        raise ChatError(
            providers.error_message(spec, response.status_code, response.text),
            response.status_code,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise ChatError("返回的不是 JSON，接口地址可能不对。") from exc
    names = providers.parse_models(spec, payload)
    if not names:
        raise ChatError("这个 key 下没有可用于对话的模型。")
    return names


MEMORY_SYSTEM = """你是桌宠的记忆整理器。给定「已有记忆」和「新增对话」，输出一段更新后的记忆摘要。

要求：
- 优先保留：用户的称呼、偏好、约定、重要事实、正在进行的话题
- 丢弃：寒暄、重复、语气词、一次性的闲聊
- 用第三人称短句，每条一行，以「- 」开头
- 只输出摘要本身，不要解释、不要标题、不要 Markdown 代码块
- 总长度不超过 {limit} 字，宁可少写几条也要留住关键事实"""


def compress_memory(
    *,
    provider: str,
    api_key: str,
    previous_summary: str,
    turns: Sequence[Message],
    limit_chars: int = 600,
    base_url: str | None = None,
    model: str | None = None,
    proxy: str | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 45.0,
) -> str:
    """把「旧摘要 + 被淘汰的原文」压成一段新的记忆摘要。"""
    spec = providers.get(provider)
    if not api_key:
        raise ChatError("请先填写 API Key。")
    if not turns:
        return previous_summary

    transcript = "\n".join(
        ("- 你：" if (t.get("role") or "") == "user" else "- 我：") + str(t.get("content") or "")
        for t in turns
    )
    prompt = (
        f"【已有记忆】\n{previous_summary or '（暂无）'}\n\n"
        f"【新增对话】\n{transcript}\n\n"
        f"请输出合并压缩后的记忆摘要（不超过 {limit_chars} 字）。"
    )
    messages: list[Message] = [
        {"role": "system", "content": MEMORY_SYSTEM.format(limit=limit_chars)},
        {"role": "user", "content": prompt},
    ]
    max_tokens = max(200, min(1500, int(limit_chars * 2)))
    url = providers.endpoint(spec, base_url, model, stream=False)
    body = providers.payload(spec, messages, model, 0.2, max_tokens, stream=False)
    try:
        with _client(transport, timeout, proxy) as client:
            response = client.post(url, json=body, headers=providers.headers(spec, api_key))
    except httpx.TimeoutException as exc:
        raise ChatError("记忆整理超时。") from exc
    except httpx.HTTPError as exc:
        raise ChatError(f"记忆整理网络失败：{exc.__class__.__name__}") from exc

    if response.status_code >= 400:
        raise ChatError(
            providers.error_message(spec, response.status_code, response.text),
            response.status_code,
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise ChatError("记忆整理返回的不是 JSON。") from exc
    text = providers.extract_delta(spec, data).strip()
    if not text:
        raise ChatError("记忆整理没有返回内容。")
    return text


class MemoryWorker(QThread):
    """后台整理记忆，不阻塞对话。失败就沿用本地摘录。"""

    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, **kwargs) -> None:
        super().__init__()
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            self.succeeded.emit(compress_memory(**self._kwargs))
        except ChatError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # pragma: no cover - 兜底
            self.failed.emit(f"记忆整理失败：{exc}")
