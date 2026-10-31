"""永久记忆：只有用户明确要求"记住"时才会写入，永不参与淘汰。

和 memory.py 的滚动摘要不同，这里是**用户显式授权**的长期事实：
- 触发靠规则匹配（"一定要记住""别忘了""帮我记下来"等），不额外调用模型，行为可预测
- 提取时把触发词去掉，只留事实本身
- 每天对话都会把这段注入 system prompt，且不占用上下文预算的淘汰名额

误报比漏报更烦人，所以规则偏保守：疑问句不记（"你还记得…吗"），
只剩触发词没有内容也不记。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

FACTS_VERSION = 1
MAX_FACT_CHARS = 200
MAX_PROMPT_CHARS = 1200

# 明确的"要记住"语气；刻意不收单字"记得"，避免把"你还记得吗"当成指令
TRIGGER_PATTERNS = (
    r"(?:一定|千万|务必|必须|永远|永久|牢牢|好好|认真)?\s*要?\s*记住",
    r"记牢",
    r"牢记",
    r"别忘了",
    r"(?:不要|别|千万)忘记",
    r"帮我记(?:一下|下来|住|着)?",
    r"替我记(?:一下|下来|住|着)?",
    r"(?:记|写|存|保存|记录)(?:一下|下来|起来)",
    r"要记得",
)
TRIGGER_RE = re.compile("|".join(f"(?:{pattern})" for pattern in TRIGGER_PATTERNS))

LEADING_NOISE = "：:，,。.、!！?？~～ \t\r\n\"'“”‘’"
TRAILING_NOISE = LEADING_NOISE + "。啦哟喔噢哈嘛"
FILLERS = ("啊", "呀", "吧", "哦", "嗯", "喏", "喔", "欸", "诶")
QUESTION_ENDINGS = ("吗", "呢", "么", "？", "?")


def is_remember_request(text: str) -> bool:
    """判断这句话是不是在要求"记住"。"""
    raw = (text or "").strip()
    if len(raw) < 6:
        return False
    if not TRIGGER_RE.search(raw):
        return False
    # 疑问句多半是在考她记不记得，而不是让她记新东西
    if raw.endswith(QUESTION_ENDINGS):
        return False
    return True


def extract_fact(text: str) -> str:
    """去掉触发词和语气词，只留下要记住的内容。"""
    remaining = TRIGGER_RE.sub(" ", text or "")
    remaining = remaining.strip(LEADING_NOISE)
    changed = True
    while changed and remaining:
        changed = False
        for filler in FILLERS:
            if remaining.startswith(filler):
                remaining = remaining[len(filler):].lstrip(LEADING_NOISE)
                changed = True
        if remaining and remaining[-1] in TRAILING_NOISE:
            remaining = remaining.rstrip(TRAILING_NOISE)
            changed = True
    remaining = " ".join(remaining.split())
    if len(remaining) < 2:
        return ""
    return remaining[:MAX_FACT_CHARS]


@dataclass
class FactStore:
    path: Path
    facts: list[dict] = field(default_factory=list)
    max_facts: int = 40
    persist: bool = True

    # ---------- 读写 ----------
    @classmethod
    def load(cls, path: Path, *, max_facts: int = 40, persist: bool = True) -> "FactStore":
        store = cls(path=Path(path), max_facts=max(5, int(max_facts)), persist=persist)
        if not store.path.exists():
            return store
        try:
            raw = json.loads(store.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return store
        if not isinstance(raw, dict):
            return store
        items = raw.get("facts")
        if isinstance(items, list):
            store.facts = [
                {"text": str(item.get("text") or "").strip(),
                 "created": str(item.get("created") or "")}
                for item in items
                if isinstance(item, dict) and str(item.get("text") or "").strip()
            ][-store.max_facts:]
        return store

    def save(self) -> None:
        if not self.persist:
            return
        payload = {"version": FACTS_VERSION, "facts": self.facts}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    # ---------- 写入 ----------
    def offer(self, user_text: str) -> str | None:
        """用户这句话要求记住时，提取并保存；返回保存下来的事实。"""
        if not self.persist or not is_remember_request(user_text):
            return None
        fact = extract_fact(user_text)
        if not fact:
            return None
        if self.add(fact):
            return fact
        return None

    def add(self, fact: str) -> bool:
        cleaned = " ".join((fact or "").split())[:MAX_FACT_CHARS]
        if len(cleaned) < 2:
            return False
        if self._contains(cleaned):
            return False
        self.facts.append(
            {"text": cleaned, "created": datetime.now().isoformat(timespec="seconds")}
        )
        del self.facts[: max(0, len(self.facts) - self.max_facts)]
        self.save()
        return True

    def _contains(self, fact: str) -> bool:
        target = self._normalise(fact)
        return any(self._normalise(item["text"]) == target for item in self.facts)

    @staticmethod
    def _normalise(text: str) -> str:
        return "".join(ch for ch in (text or "").lower() if ch.isalnum())

    def remove(self, index: int) -> bool:
        if 0 <= index < len(self.facts):
            del self.facts[index]
            self.save()
            return True
        return False

    def clear(self) -> None:
        self.facts.clear()
        self.save()

    # ---------- 注入 ----------
    def prompt_block(self) -> str:
        """拼成 system prompt 里的一段；按新旧截断，避免无限膨胀。"""
        if not self.facts:
            return ""
        lines: list[str] = []
        total = 0
        for item in reversed(self.facts):  # 新的优先
            line = f"- {item['text']}"
            if total + len(line) > MAX_PROMPT_CHARS:
                break
            lines.append(line)
            total += len(line)
        if not lines:
            return ""
        lines.reverse()
        return (
            "【用户明确要求你永久记住的事】\n"
            "这些是用户特意交代过的，回答时可以自然地引用，不要忘记：\n"
            + "\n".join(lines)
        )

    def lines(self) -> list[str]:
        return [item["text"] for item in self.facts]
