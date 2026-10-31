"""对话记忆：落盘保存 + 上下文预算 + 早期记忆折叠。

设计取舍（为什么这样淘汰）：

1. **磁盘记全，上下文只放一部分**。`memory.json` 里保留完整对话，重启后还在；
   送进模型的是「一段滚动摘要 + 最近若干条」，受 token 预算约束。
   这样既不丢历史，又不会每轮都把几万 token 全发一遍（贵且慢）。

2. **按"轮次"成批淘汰，而不是一条一条挤**。超预算时不会只淘汰刚好够的那一条，
   而是一次淘汰到**低水位**（预算的 60%）。原因是：一条一条挤会让每一轮都触发一次
   摘要重写，摘要质量越写越糊；留出 40% 余量相当于一次整理能撑十几轮。

3. **最近 6 条永不淘汰**（3 个来回）。当前话题永远完整，不会被摘要截断。

4. **淘汰内容折成摘要，摘要也有上限**。摘要按行累积，超过 600 字就从最旧的行开始丢，
   于是"很早以前的事"会自然淡出——这本来就是记忆的代价，硬留反而会把摘要撑爆。

5. **成对淘汰**。一次尽量淘汰「用户+助手」两个回合，避免上下文从"我：…"开头，
   有些模型对首条不是 user 的对话会不自然。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Sequence

MEMORY_VERSION = 1

# 淘汰到预算的这个比例，给下一次淘汰留出余量
LOW_WATER_RATIO = 0.6
# 最近多少条消息永不淘汰（6 = 3 个来回）
KEEP_RECENT = 6
# 摘要最多保留多少字符
SUMMARY_MAX_CHARS = 600
# 单条摘录的截断长度
USER_SNIPPET = 40
ASSISTANT_SNIPPET = 30
# 每条消息固定开销（role/分隔符等）
MESSAGE_OVERHEAD = 4

SummaryFn = Callable[[str, Sequence[dict]], str]


def estimate_tokens(text: str) -> int:
    """粗略 token 估计：中日韩字符按 1 个算，其余按 4 字符 1 个。

    宁可高估：高估只是少放几条，低估会真的把请求撑爆。
    """
    if not text:
        return 0
    cjk = 0
    for ch in text:
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF or 0x3040 <= code <= 0x30FF or 0xAC00 <= code <= 0xD7AF:
            cjk += 1
    others = len(text) - cjk
    return cjk + (others + 3) // 4


def message_tokens(message: dict) -> int:
    return estimate_tokens(str(message.get("content") or "")) + MESSAGE_OVERHEAD


@dataclass
class MemoryInfo:
    """一次上下文组装的结果，便于界面显示和测试断言。"""

    messages: int = 0
    tokens: int = 0
    evicted: int = 0
    kept: int = 0
    total_turns: int = 0
    summarized: int = 0
    compactions: int = 0

    @property
    def compacted(self) -> bool:
        return self.evicted > 0


@dataclass
class MemoryStore:
    path: Path
    turns: list[dict] = field(default_factory=list)
    summary: str = ""
    summarized: int = 0          # turns[:summarized] 已折叠进 summary
    compactions: int = 0
    persist: bool = True
    context_tokens: int = 2400
    max_turns: int = 2000
    summary_max_chars: int = SUMMARY_MAX_CHARS
    # 待模型整理的素材：本地摘录只是兜底，真正的记忆压缩在后台做
    pending_summary: str | None = None
    pending_turns: list[dict] = field(default_factory=list)

    # ---------- 读写 ----------
    @classmethod
    def load(
        cls,
        path: Path,
        *,
        persist: bool = True,
        context_tokens: int = 2400,
        max_turns: int = 2000,
    ) -> "MemoryStore":
        store = cls(
            path=Path(path),
            persist=persist,
            context_tokens=context_tokens,
            max_turns=max_turns,
        )
        if not store.path.exists():
            return store
        try:
            raw = json.loads(store.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return store
        if not isinstance(raw, dict):
            return store
        turns = raw.get("turns")
        if isinstance(turns, list):
            store.turns = [
                {"role": str(item.get("role") or ""), "content": str(item.get("content") or ""),
                 "ts": str(item.get("ts") or "")}
                for item in turns
                if isinstance(item, dict) and item.get("content")
            ]
        store.summary = str(raw.get("summary") or "")
        store.summarized = max(0, min(int(raw.get("summarized") or 0), len(store.turns)))
        store.compactions = max(0, int(raw.get("compactions") or 0))
        return store

    def save(self) -> None:
        if not self.persist:
            return
        payload = {
            "version": MEMORY_VERSION,
            "updated": datetime.now().isoformat(timespec="seconds"),
            "summary": self.summary,
            "summarized": self.summarized,
            "compactions": self.compactions,
            "turns": self.turns,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass  # 记忆写不进去不能影响聊天

    def clear(self) -> None:
        self.turns.clear()
        self.summary = ""
        self.summarized = 0
        self.compactions = 0
        self.save()

    # ---------- 写入 ----------
    def add(self, role: str, content: str) -> None:
        content = (content or "").strip()
        if not content:
            return
        self.turns.append(
            {"role": role, "content": content, "ts": datetime.now().isoformat(timespec="seconds")}
        )
        self._trim_disk()

    def drop_last(self, role: str) -> bool:
        """请求失败时撤回最后一条，避免重试时重复记账。"""
        if self.turns and self.turns[-1].get("role") == role:
            self.turns.pop()
            self.summarized = min(self.summarized, len(self.turns))
            return True
        return False

    def _trim_disk(self) -> None:
        overflow = len(self.turns) - self.max_turns
        if overflow <= 0:
            return
        del self.turns[:overflow]
        self.summarized = max(0, self.summarized - overflow)

    # ---------- 上下文 ----------
    def context(
        self,
        system_prompt: str,
        *,
        summarize: SummaryFn | None = None,
    ) -> tuple[list[dict], MemoryInfo]:
        """组装这次要发给模型的消息，必要时先把旧记忆折叠成摘要。"""
        # 摘要也占预算，所以它的上限跟着预算走：预算的一成半到六成之间
        self.summary_max_chars = min(SUMMARY_MAX_CHARS, max(60, int(self.context_tokens * 0.25)))
        recent = [dict(t) for t in self.turns[self.summarized:]]
        summary = self.summary
        info = MemoryInfo(
            total_turns=len(self.turns),
            summarized=self.summarized,
            compactions=self.compactions,
        )

        tokens = self._tokens(recent, summary)
        evicted: list[dict] = []
        if tokens > self.context_tokens:
            base_summary = summary
            recent, summary, evicted = self._compact(recent, summary, summarize)
            tokens = self._tokens(recent, summary)
            if evicted:
                self._record_pending(base_summary, evicted)
                self.summarized += len(evicted)
                self.summary = summary
                self.compactions += 1
                self.save()

        system = system_prompt
        if summary:
            system = (
                f"{system_prompt}\n\n"
                "【更早的对话，你已经记在心里，回答时可以自然提起】\n"
                f"{summary}"
            )
        messages = [{"role": "system", "content": system}]
        messages.extend({"role": m["role"], "content": m["content"]} for m in recent)

        info.messages = len(messages)
        info.tokens = tokens
        info.evicted = len(evicted)
        info.kept = len(recent)
        info.summarized = self.summarized
        info.compactions = self.compactions
        return messages, info

    # ---------- 后台整理 ----------
    def _record_pending(self, base_summary: str, evicted: Sequence[dict]) -> None:
        if self.pending_summary is None:
            self.pending_summary = base_summary
        self.pending_turns.extend(dict(t) for t in evicted)

    def has_pending(self) -> bool:
        return bool(self.pending_turns)

    def take_pending(self) -> tuple[str, list[dict]]:
        """取出待整理的素材（旧摘要 + 被淘汰的原文），交给模型压缩。"""
        base = self.pending_summary or ""
        turns = list(self.pending_turns)
        self.pending_summary = None
        self.pending_turns = []
        return base, turns

    def apply_summary(self, text: str) -> bool:
        """用模型压缩后的记忆替换本地摘录。返回是否真的替换了。"""
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return False
        self.summary = self._cap(cleaned)
        self.save()
        return True

    # ---------- 内部 ----------
    def _tokens(self, recent: Iterable[dict], summary: str) -> int:
        total = estimate_tokens(summary) + 8 if summary else 0
        for message in recent:
            total += message_tokens(message)
        return total

    def _compact(
        self,
        recent: list[dict],
        summary: str,
        summarize: SummaryFn | None,
    ) -> tuple[list[dict], str, list[dict]]:
        """从最旧的一端成批淘汰，直到降到低水位。

        最近 KEEP_RECENT 条受保护；每次尽量淘汰一个「用户+助手」回合，
        免得上下文以一个孤零零的助手消息开头。
        """
        target = int(self.context_tokens * LOW_WATER_RATIO)
        evicted: list[dict] = []
        remaining = list(recent)

        while self._tokens(remaining, summary) > target and len(remaining) > KEEP_RECENT:
            evictable = len(remaining) - KEEP_RECENT
            take = 2 if evictable >= 2 else 1
            chunk, remaining = remaining[:take], remaining[take:]
            evicted.extend(chunk)
            summary = self._fold(summary, evicted, summarize)
        return remaining, summary, evicted

    def _fold(self, previous: str, evicted: Sequence[dict], summarize: SummaryFn | None) -> str:
        if not evicted:
            return previous
        if summarize is not None:
            try:
                return self._cap(str(summarize(previous, list(evicted))).strip())
            except Exception:  # 摘要函数挂了就退回本地摘录
                pass
        lines = [line for line in previous.splitlines() if line.strip()]
        for message in evicted:
            lines.append(self._snippet(message))
        while lines and len("\n".join(lines)) > self.summary_max_chars:
            lines.pop(0)
        return "\n".join(lines)

    @staticmethod
    def _snippet(message: dict) -> str:
        text = " ".join(str(message.get("content") or "").split())
        if message.get("role") == "user":
            short = text[:USER_SNIPPET] + ("…" if len(text) > USER_SNIPPET else "")
            return f"- 你：{short}"
        short = text[:ASSISTANT_SNIPPET] + ("…" if len(text) > ASSISTANT_SNIPPET else "")
        return f"- 我：{short}"

    def _cap(self, text: str) -> str:
        limit = getattr(self, "summary_max_chars", SUMMARY_MAX_CHARS)
        if len(text) <= limit:
            return text
        return text[-limit:].lstrip()


def remember(
    store: MemoryStore,
    role: str,
    content: str,
    *,
    save: bool = True,
) -> None:
    store.add(role, content)
    if save:
        store.save()
