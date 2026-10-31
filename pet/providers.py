"""多供应商适配：DeepSeek / Google Gemini / 任意 OpenAI 兼容接口。

各家只差三件事：URL、鉴权头、请求体与增量文本的位置。
线程、SSE 解析、重试与界面全部共用，新增一家只要加一个 ProviderSpec。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

Message = Mapping[str, str]

OPENAI_STYLE = "openai"
GEMINI_STYLE = "gemini"

_STATUS_HINTS = {
    400: "请求格式有误（400）",
    401: "API Key 无效或已失效（401）",
    402: "账户余额不足（402）",
    403: "没有权限（403）",
    404: "接口地址或模型不存在（404）",
    422: "请求参数不被接受（422）",
    429: "请求太频繁，被限流了（429）",
    500: "服务端错误（500）",
    502: "网关错误（502）",
    503: "服务繁忙，稍后再试（503）",
}


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    style: str
    default_base_url: str
    default_model: str
    env_vars: tuple[str, ...]
    key_placeholder: str
    hint: str


PROVIDERS: dict[str, ProviderSpec] = {
    "deepseek": ProviderSpec(
        key="deepseek",
        label="DeepSeek",
        style=OPENAI_STYLE,
        default_base_url="https://api.deepseek.com",
        default_model="deepseek-chat",
        env_vars=("DEEPSEEK_API_KEY",),
        key_placeholder="sk-…",
        hint="OpenAI 兼容接口，地址 https://api.deepseek.com",
    ),
    "gemini": ProviderSpec(
        key="gemini",
        label="Google Gemini",
        style=GEMINI_STYLE,
        default_base_url="https://generativelanguage.googleapis.com",
        default_model="gemini-2.5-flash",
        env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        key_placeholder="AIza…",
        hint="原生 v1beta 接口，模型名可点「拉取」从账号可用列表里选",
    ),
    "openai": ProviderSpec(
        key="openai",
        label="其他 OpenAI 兼容接口",
        style=OPENAI_STYLE,
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        env_vars=("OPENAI_API_KEY",),
        key_placeholder="sk-…",
        hint="任何兼容 /chat/completions 的服务（含本地 Ollama、中转站）",
    ),
}

PROVIDER_KEYS = tuple(PROVIDERS)
DEFAULT_PROVIDER = "deepseek"


def get(name: str | None) -> ProviderSpec:
    return PROVIDERS.get((name or "").strip().lower(), PROVIDERS[DEFAULT_PROVIDER])


def label_of(name: str | None) -> str:
    return get(name).label


# ---------- 配置解析 ----------
def resolve_key(
    spec: ProviderSpec,
    keys: Mapping[str, str],
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    """返回 (key, 来源说明)。环境变量优先。"""
    environ = os.environ if environ is None else environ
    for var in spec.env_vars:
        value = (environ.get(var) or "").strip()
        if value:
            return value, f"环境变量 {var}"
    value = (keys.get(spec.key) or "").strip()
    return value, ("配置文件" if value else "未配置")


def normalise_base(spec: ProviderSpec, base_url: str | None) -> str:
    base = (base_url or spec.default_base_url).strip().rstrip("/")
    if spec.style == GEMINI_STYLE:
        for suffix in ("/v1beta", "/v1"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
    return base


def endpoint(spec: ProviderSpec, base_url: str | None, model: str | None, *, stream: bool) -> str:
    base = normalise_base(spec, base_url)
    name = (model or spec.default_model).strip()
    if spec.style == GEMINI_STYLE:
        action = "streamGenerateContent?alt=sse" if stream else "generateContent"
        return f"{base}/v1beta/models/{name}:{action}"
    return f"{base}/chat/completions"


def models_endpoint(spec: ProviderSpec, base_url: str | None) -> str:
    base = normalise_base(spec, base_url)
    return f"{base}/v1beta/models?pageSize=100" if spec.style == GEMINI_STYLE else f"{base}/models"


def headers(spec: ProviderSpec, api_key: str) -> dict[str, str]:
    common = {
        "Content-Type": "application/json",
        "User-Agent": "Mysterio-DesktopPet/1.1",
    }
    if spec.style == GEMINI_STYLE:
        common["x-goog-api-key"] = api_key
    else:
        common["Authorization"] = f"Bearer {api_key}"
    return common


# ---------- 请求体 ----------
def _gemini_contents(messages: Iterable[Message]) -> tuple[str, list[dict[str, Any]]]:
    system: list[str] = []
    contents: list[dict[str, Any]] = []
    for message in messages:
        role = (message.get("role") or "user").strip()
        text = message.get("content") or ""
        if not text:
            continue
        if role == "system":
            system.append(text)
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text}]})
        else:
            contents.append({"role": "user", "parts": [{"text": text}]})
    return "\n".join(system), contents


def payload(
    spec: ProviderSpec,
    messages: Sequence[Message],
    model: str | None,
    temperature: float,
    max_tokens: int,
    *,
    stream: bool,
) -> dict[str, Any]:
    if spec.style == GEMINI_STYLE:
        system, contents = _gemini_contents(messages)
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": float(temperature),
                "maxOutputTokens": int(max_tokens),
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        return body
    return {
        "model": (model or spec.default_model).strip(),
        "messages": [dict(m) for m in messages],
        "stream": bool(stream),
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
    }


# ---------- 响应解析 ----------
def extract_delta(spec: ProviderSpec, event: Any) -> str:
    if not isinstance(event, dict):
        return ""
    if spec.style == GEMINI_STYLE:
        pieces: list[str] = []
        for candidate in event.get("candidates") or []:
            content = candidate.get("content") or {}
            for part in content.get("parts") or []:
                text = part.get("text")
                if isinstance(text, str):
                    pieces.append(text)
        return "".join(pieces)
    choices = event.get("choices") or []
    if not choices:
        return ""
    first = choices[0] or {}
    delta = first.get("delta") or {}
    piece = delta.get("content")
    if not piece:
        piece = (first.get("message") or {}).get("content")
    return piece if isinstance(piece, str) else ""


def blocked_reason(spec: ProviderSpec, event: Any) -> str | None:
    """内容安全策略拦截时给一句人话，避免“她什么也没说”。"""
    if not isinstance(event, dict):
        return None
    if spec.style == GEMINI_STYLE:
        feedback = event.get("promptFeedback") or {}
        reason = feedback.get("blockReason")
        if reason:
            return f"这次请求被安全策略拦下了（{reason}），换个说法试试？"
        for candidate in event.get("candidates") or []:
            finish = candidate.get("finishReason")
            if finish and finish not in ("STOP", "MAX_TOKENS"):
                return f"回答被中止了（{finish}）。"
    return None


def error_message(spec: ProviderSpec, status: int, body: str) -> str:
    detail = ""
    try:
        data = json.loads(body) if body else {}
    except (ValueError, TypeError):
        data = {}
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("status") or "").strip()
        elif isinstance(error, str):
            detail = error.strip()
    if not detail:
        detail = (body or "").strip()[:200]

    hint = _STATUS_HINTS.get(status, f"请求失败（{status}）")
    lowered = detail.lower()
    if spec.style == GEMINI_STYLE and status in (400, 403) and "api key" in lowered:
        hint = "API Key 无效（400）"
    elif spec.style == GEMINI_STYLE and status == 404:
        hint = "模型名或接口地址不对（404）"
    return f"{hint}：{detail}" if detail else hint


def parse_models(spec: ProviderSpec, payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    if spec.style == GEMINI_STYLE:
        names: list[str] = []
        for item in payload.get("models") or []:
            methods = item.get("supportedGenerationMethods") or []
            if methods and "generateContent" not in methods:
                continue
            full = str(item.get("name") or "")
            if full:
                names.append(full.split("/")[-1])
        return names
    return [str(item.get("id")) for item in payload.get("data") or [] if item.get("id")]
