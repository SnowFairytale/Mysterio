"""桌宠离屏冒烟测试：渲染截图、尺寸/持久化校验、DeepSeek 流式解析校验。

不联网、不污染真实 config.json：
    python tools/pet_smoke.py
产物：build/smoke/preview.png 等
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 离屏平台默认不带字体，指定 Windows 字体目录才能渲染中文
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from PyQt6.QtCore import QPoint, QRect, QSize, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from pet import config as config_module  # noqa: E402
from pet import providers  # noqa: E402
from pet.behavior import HAPPY, SURPRISED, THINKING, WORKING, PetBehavior  # noqa: E402
from pet.bubble import ChatBubble  # noqa: E402
from pet.config import Config, POSE_KEYS  # noqa: E402
from pet.chat import (  # noqa: E402
    ChatWorker,
    build_messages,
    compress_memory,
    list_models,
    test_connection,
)
from pet.memory import MemoryStore, estimate_tokens  # noqa: E402
from pet.facts import FactStore, extract_fact, is_remember_request  # noqa: E402
from pet.sprites import SpriteStore  # noqa: E402
from pet.settings import SettingsWindow  # noqa: E402
from pet.window import PetWindow  # noqa: E402

OUT = ROOT / "build" / "smoke"
TMP = Path(tempfile.mkdtemp(prefix="mysterio-smoke-"))
config_module.CONFIG_PATH = TMP / "config.json"

checks: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    checks.append((name, bool(condition), detail))


def compose_preview(path: Path, window: PetWindow, bubble: ChatBubble) -> None:
    width, height = 1040, 600
    canvas = QImage(QSize(width, height), QImage.Format.Format_ARGB32)
    painter = QPainter(canvas)
    grad = QLinearGradient(0, 0, 0, height)
    grad.setColorAt(0.0, QColor("#1b1726"))
    grad.setColorAt(1.0, QColor("#2a2137"))
    painter.fillRect(canvas.rect(), grad)

    tile = 16
    for y in range(150, 470, tile):
        for x in range(70, 430, tile):
            if (x // tile + y // tile) % 2:
                painter.fillRect(x, y, tile, tile, QColor(255, 255, 255, 10))

    painter.drawPixmap(120, 170, window.grab())
    painter.drawPixmap(470, 140, bubble.grab())
    painter.end()
    canvas.save(str(path))


def mock_stream_handler(pieces: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = "".join(
            f"data: {json.dumps({'choices': [{'delta': {'content': p}}]}, ensure_ascii=False)}\n\n"
            for p in pieces
        ) + "data: [DONE]\n\n"
        return httpx.Response(
            200, content=body.encode("utf-8"),
            headers={"Content-Type": "text/event-stream"},
        )

    return handler


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)

    # ---------- 1. 窗口与精灵 ----------
    config = Config()
    config.pet.size = 256
    sprites = SpriteStore()
    window = PetWindow(config, sprites)
    window.show()
    app.processEvents()

    check("默认尺寸 256", window.size() == QSize(256, 256), str(window.size()))
    check("六态素材齐全", len(sprites.preload()) == len(POSE_KEYS), str(sprites.preload()))
    check(
        "256 逻辑 @2x 用满 512 原生像素",
        sprites.scaled("idle", 256, 256, 2.0).size() == QSize(512, 512),
        str(sprites.scaled("idle", 256, 256, 2.0).size()),
    )
    check(
        "256 逻辑 @1x 降采样到 256",
        sprites.scaled("idle", 256, 256, 1.0).size() == QSize(256, 256),
        str(sprites.scaled("idle", 256, 256, 1.0).size()),
    )

    pet_png = OUT / "pet_256.png"
    check("窗口渲染非空", window.grab().save(str(pet_png)), str(pet_png))

    # ---------- 2. 尺寸缩放 ----------
    window.apply_size(320)
    app.processEvents()
    check("缩放到 320", window.size() == QSize(320, 320), str(window.size()))
    window.apply_size(48)  # 低于下限应被夹住
    check("下限夹紧 64", window.size() == QSize(64, 64), str(window.size()))
    window.apply_size(2000)  # 高于上限应被夹住
    check("上限夹紧 768", window.size() == QSize(768, 768), str(window.size()))
    window.apply_size(256)
    app.processEvents()

    # ---------- 3. 表情行为：待机为常态，其余靠触发 ----------
    behavior = PetBehavior(window, rest_after_ms=60_000)
    check("启动即待机", behavior.pose == "idle", behavior.pose)

    behavior.set_busy(THINKING)
    check("等回答时是思考", behavior.pose == "thinking", behavior.pose)
    behavior.flash(HAPPY, 300)
    check("短暂反应优先于忙碌态", behavior.pose == "happy", behavior.pose)
    QTest.qWait(450)
    check("反应结束后回到忙碌态", behavior.pose == "thinking", behavior.pose)

    behavior.set_busy(WORKING)
    check("收字时是工作", behavior.pose == "working", behavior.pose)
    behavior.set_busy(None)
    check("回答结束回到待机", behavior.pose == "idle", behavior.pose)

    reaction = behavior.poke()
    check(
        "戳一下有反应",
        behavior.pose == reaction and reaction in (HAPPY, SURPRISED),
        reaction,
    )
    QTest.qWait(1500)
    check("反应结束回到待机", behavior.pose == "idle", behavior.pose)

    behavior.rest_now()
    check("久无交互会休息", behavior.pose == "resting", behavior.pose)
    behavior.touch()
    check("一动就醒回待机", behavior.pose == "idle", behavior.pose)
    behavior.shutdown()

    # ---------- 4. 对话气泡 ----------
    bubble = ChatBubble()
    bubble.add_user("你好呀，你是谁？")
    bubble.begin_reply()
    for piece in ("我是", "喵斯特", "莉亚，", "住在你桌面上的猫娘。"):
        bubble.add_chunk(piece)
    bubble.end_reply()
    bubble.show()
    app.processEvents()
    text = bubble._history.toPlainText()
    check("流式文本进入聊天记录", "住在你桌面上的猫娘" in text, text.replace("\n", " | ")[:80])
    bubble_png = OUT / "bubble.png"
    check("气泡渲染非空", bubble.grab().save(str(bubble_png)), str(bubble_png))

    # 回归：首次打开必须已经定位，不能出现在系统默认位置（屏幕中间）
    area = app.primaryScreen().availableGeometry()
    fresh = ChatBubble()
    pet_a = QRect(area.center().x() - 128, 400, 256, 256)
    fresh.pop_up(pet_a)
    app.processEvents()
    gap_first = pet_a.top() - (fresh.y() + fresh.height())
    check(
        "首次打开即贴在宠物上方",
        0 <= gap_first <= 12,
        f"bubble={fresh.geometry()} pet={pet_a}",
    )
    check(
        "首次打开水平居中于宠物",
        abs(fresh.x() + fresh.width() // 2 - pet_a.center().x()) <= 6,
        f"{fresh.x() + fresh.width() // 2} vs {pet_a.center().x()}",
    )
    fresh.hide()

    pet_b = QRect(area.center().x() + 120 - 128, 400, 256, 256)
    fresh.pop_up(pet_b)
    app.processEvents()
    check(
        "关掉后挪动宠物，再开仍是新位置",
        abs(fresh.x() + fresh.width() // 2 - pet_b.center().x()) <= 6,
        f"{fresh.x() + fresh.width() // 2} vs {pet_b.center().x()}",
    )
    fresh.close()

    compose_preview(OUT / "preview.png", window, bubble)

    # 气泡跟随：宠物在屏幕中部时贴上方，贴到顶边时翻到下方
    screen = app.primaryScreen().availableGeometry()
    window.move(screen.center().x() - 128, screen.center().y())
    app.processEvents()
    rect = window.geometry()
    bubble.follow(rect)
    gap = rect.top() - (bubble.y() + bubble.height())
    check("气泡贴在宠物上方", 0 <= gap <= 12, f"gap={gap}")

    window.move(rect.x(), screen.top())
    app.processEvents()
    top_rect = window.geometry()
    bubble.follow(top_rect)
    check(
        "顶到屏幕上方时自动翻到下方",
        bubble.y() >= top_rect.bottom() and bubble.y() + bubble.height() <= screen.bottom() + 1,
        f"bubble={bubble.geometry()} pet={top_rect}",
    )

    window.move(screen.right() - 300, screen.bottom() - 320)
    app.processEvents()
    bubble.follow(window.geometry())
    check(
        "右下角时不越界",
        screen.contains(bubble.geometry().topLeft())
        and bubble.geometry().right() <= screen.right() + 1
        and bubble.geometry().bottom() <= screen.bottom() + 1,
        f"bubble={bubble.geometry()} screen={screen}",
    )

    # ---------- 5. 配置与多供应商 ----------
    config.pet.size = 320
    config.pet.x, config.pet.y = 120, 240
    config.pet.rest_after_seconds = 90
    config.chat.provider = "gemini"
    config.chat.api_keys = {"deepseek": "sk-deepseek-1", "gemini": "AIza-gemini-1"}
    config.chat.models = {"gemini": "gemini-2.5-flash"}
    config.chat.proxy = "http://127.0.0.1:7890"
    config.memory.context_tokens = 1234
    config.memory.max_facts = 12
    config.save()
    reloaded = Config.load()
    check(
        "配置往返一致",
        (reloaded.pet.size, reloaded.pet.x, reloaded.pet.y, reloaded.pet.rest_after_seconds)
        == (320, 120, 240, 90),
        str((reloaded.pet.size, reloaded.pet.x, reloaded.pet.y, reloaded.pet.rest_after_seconds)),
    )
    check("供应商持久化", reloaded.chat.provider == "gemini", reloaded.chat.provider)
    check(
        "两套 key 互不覆盖",
        reloaded.chat.key_for("deepseek") == "sk-deepseek-1"
        and reloaded.chat.key_for("gemini") == "AIza-gemini-1",
        f"{reloaded.chat.key_for('deepseek')} / {reloaded.chat.key_for('gemini')}",
    )
    check(
        "未配置时回落到默认模型",
        reloaded.chat.model("deepseek") == "deepseek-chat"
        and reloaded.chat.model("gemini") == "gemini-2.5-flash",
        f"{reloaded.chat.model('deepseek')} / {reloaded.chat.model('gemini')}",
    )

    legacy = TMP / "legacy.json"
    legacy.write_text(
        json.dumps(
            {
                "chat": {
                    "api_key": "sk-old",
                    "base_url": "https://api.deepseek.com",
                    "model": "deepseek-chat",
                    "temperature": 0.7,
                }
            }
        ),
        encoding="utf-8",
    )
    migrated = Config.load(legacy)
    check(
        "旧版扁平配置自动迁移",
        migrated.chat.provider == "deepseek" and migrated.chat.key_for("deepseek") == "sk-old",
        f"{migrated.chat.provider} / {migrated.chat.key_for('deepseek')}",
    )
    check("温度跟着迁移", migrated.chat.temperature == 0.7, str(migrated.chat.temperature))
    check("代理设置随配置保存", reloaded.chat.proxy == "http://127.0.0.1:7890", reloaded.chat.proxy)
    check(
        "记忆设置随配置保存",
        (reloaded.memory.context_tokens, reloaded.memory.max_facts) == (1234, 12),
        f"{reloaded.memory.context_tokens}/{reloaded.memory.max_facts}",
    )

    key, source = providers.resolve_key(
        providers.get("gemini"), {"gemini": "from-config"}, {"GEMINI_API_KEY": "from-env"}
    )
    check(
        "环境变量优先于配置",
        key == "from-env" and "环境变量" in source,
        f"{key} ({source})",
    )

    bad = TMP / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    check("坏配置不崩溃", Config.load(bad).pet.size == 256)
    tight = TMP / "tight.json"
    tight.write_text('{"pet": {"rest_after_seconds": 5, "size": 5}}', encoding="utf-8")
    clamped = Config.load(tight)
    check(
        "异常值被夹紧",
        clamped.pet.rest_after_seconds == 15 and clamped.pet.size == 64,
        f"{clamped.pet.rest_after_seconds}/{clamped.pet.size}",
    )

    # ---------- 6. 设置对话框 ----------
    dialog = SettingsWindow(config, 2.0)
    dialog.show()
    app.processEvents()
    check("设置对话框可实例化", dialog.width() >= 400, str(dialog.width()))
    check("打开时停在上次用的供应商", dialog._current == "gemini", dialog._current)
    dialog._provider.setCurrentIndex(dialog._provider.findData("deepseek"))
    app.processEvents()
    check("切换供应商会换出对应 key", dialog._key.text() == "sk-deepseek-1", dialog._key.text())
    dialog._provider.setCurrentIndex(dialog._provider.findData("gemini"))
    app.processEvents()
    check("切回后仍是自己的 key", dialog._key.text() == "AIza-gemini-1", dialog._key.text())
    loaded = dialog.result_config()
    check("设置回读尺寸", loaded.pet.size == 320, str(loaded.pet.size))
    dialog._size.setValue(256)
    app.processEvents()
    check(
        "256 尺寸提示折算 512 并标 1:1",
        "正好 1:1" in dialog._size_hint.text(),
        dialog._size_hint.text(),
    )
    dialog.shutdown()

    # ---------- 7. OpenAI 兼容供应商（DeepSeek） ----------
    pieces = ["你好", "，我是", "喵斯特莉亚。"]
    seen: dict[str, object] = {}

    def openai_handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=mock_stream_handler(pieces)(request).content,
            headers={"Content-Type": "text/event-stream"},
        )

    received: list[str] = []
    done: list[str] = []
    worker = ChatWorker(
        api_key="sk-test",
        messages=[{"role": "user", "content": "hi"}],
        provider="deepseek",
        transport=httpx.MockTransport(openai_handler),
    )
    worker.chunk.connect(received.append)
    worker.succeeded.connect(done.append)
    worker.run()
    check("DeepSeek 端点正确", str(seen["url"]).endswith("/chat/completions"), str(seen["url"]))
    check("DeepSeek 走 Bearer 鉴权", seen["auth"] == "Bearer sk-test", str(seen["auth"]))
    check("DeepSeek 请求带 stream", seen["body"].get("stream") is True, str(seen["body"].get("stream")))
    check("SSE 分片按序拼接", "".join(received) == "".join(pieces), "".join(received))
    check("成功信号带完整文本", done == ["".join(pieces)], str(done))

    def unauth(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"message": "Authentication Fails"}},
            headers={"Content-Type": "application/json"},
        )

    failures: list[str] = []
    bad_worker = ChatWorker(
        api_key="sk-bad", messages=[{"role": "user", "content": "hi"}], provider="deepseek",
        transport=httpx.MockTransport(unauth),
    )
    bad_worker.failed.connect(failures.append)
    bad_worker.run()
    check("401 给出可读错误", bool(failures) and "401" in failures[0], failures[0] if failures else "无")

    # 代理是否真的生效：指向本机一个没服务的端口，应立刻连接失败
    proxy_fails: list[str] = []
    proxied = ChatWorker(
        api_key="sk-test",
        messages=[{"role": "user", "content": "hi"}],
        provider="deepseek",
        proxy="http://127.0.0.1:9",
    )
    proxied.failed.connect(proxy_fails.append)
    proxied.run()
    check(
        "代理配置确实被使用",
        bool(proxy_fails) and "网络请求失败" in proxy_fails[0],
        proxy_fails[0] if proxy_fails else "无",
    )

    # ---------- 8. Gemini 原生接口 ----------
    gseen: dict[str, object] = {}

    def gemini_handler(request: httpx.Request) -> httpx.Response:
        gseen["url"] = str(request.url)
        gseen["key"] = request.headers.get("x-goog-api-key")
        gseen["body"] = json.loads(request.content)
        body = "".join(
            f"data: {json.dumps({'candidates': [{'content': {'parts': [{'text': p}]}}]}, ensure_ascii=False)}\n\n"
            for p in pieces
        ) + "data: [DONE]\n\n"
        return httpx.Response(
            200, content=body.encode("utf-8"), headers={"Content-Type": "text/event-stream"}
        )

    g_received: list[str] = []
    g_done: list[str] = []
    g_worker = ChatWorker(
        api_key="AIza-x",
        provider="gemini",
        model="gemini-2.5-flash",
        messages=[
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "hi"},
        ],
        transport=httpx.MockTransport(gemini_handler),
    )
    g_worker.chunk.connect(g_received.append)
    g_worker.succeeded.connect(g_done.append)
    g_worker.run()
    check(
        "Gemini 走 streamGenerateContent + alt=sse",
        ":streamGenerateContent" in str(gseen["url"]) and "alt=sse" in str(gseen["url"]),
        str(gseen["url"]),
    )
    check("Gemini 用 x-goog-api-key 鉴权", gseen["key"] == "AIza-x", str(gseen["key"]))
    check(
        "Gemini 系统提示进 systemInstruction",
        gseen["body"]["systemInstruction"]["parts"][0]["text"] == "SYS",
        str(gseen["body"].get("systemInstruction")),
    )
    check(
        "Gemini 角色映射为 user/model",
        [c["role"] for c in gseen["body"]["contents"]] == ["user"]
        and gseen["body"]["contents"][0]["parts"][0]["text"] == "hi",
        str(gseen["body"]["contents"]),
    )
    check(
        "Gemini 生成参数映射",
        gseen["body"]["generationConfig"]["maxOutputTokens"] == config.chat.max_tokens,
        str(gseen["body"]["generationConfig"]),
    )
    check("Gemini 分片拼接", "".join(g_received) == "".join(pieces), "".join(g_received))
    check("Gemini 成功信号", g_done == ["".join(pieces)], str(g_done))

    def gemini_bad_key(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "code": 400,
                    "message": "API key not valid. Please pass a valid API key.",
                    "status": "INVALID_ARGUMENT",
                }
            },
        )

    g_failures: list[str] = []
    g_bad = ChatWorker(
        api_key="bad", provider="gemini", messages=[{"role": "user", "content": "hi"}],
        transport=httpx.MockTransport(gemini_bad_key),
    )
    g_bad.failed.connect(g_failures.append)
    g_bad.run()
    check(
        "Gemini key 错误给中文提示",
        bool(g_failures) and "API Key 无效" in g_failures[0],
        g_failures[0] if g_failures else "无",
    )

    g_probe = test_connection(
        "gemini", "AIza-x",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": "pong"}]}}]}
            )
        ),
    )
    check("Gemini 测试连接", "成功" in g_probe, g_probe)

    model_names = list_models(
        "gemini", "AIza-x",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "models/gemini-2.5-flash",
                            "supportedGenerationMethods": ["generateContent", "countTokens"],
                        },
                        {
                            "name": "models/text-embedding-004",
                            "supportedGenerationMethods": ["embedContent"],
                        },
                    ]
                },
            )
        ),
    )
    check("Gemini 模型列表会过滤掉不能对话的", model_names == ["gemini-2.5-flash"], str(model_names))

    no_key: list[str] = []
    keyless = ChatWorker(api_key="", messages=[{"role": "user", "content": "hi"}], provider="gemini")
    keyless.failed.connect(no_key.append)
    keyless.run()
    check("缺 Key 提前拦截", bool(no_key) and "API Key" in no_key[0], no_key[0] if no_key else "无")

    probe = test_connection(
        "deepseek", "sk-test", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})
        ),
    )
    check("DeepSeek 测试连接", "成功" in probe, probe)

    messages = build_messages("SYS", [{"role": "user", "content": str(i)} for i in range(10)], 2)
    check("历史裁剪保留 system", messages[0]["role"] == "system" and len(messages) == 5, str(len(messages)))

    # ---------- 9. 记忆：落盘、预算、折叠 ----------
    check("token 估算按中文单字", estimate_tokens("你好世界") == 4, str(estimate_tokens("你好世界")))
    check("token 估算按西文 4 字符", estimate_tokens("abcd") == 1, str(estimate_tokens("abcd")))

    mem_path = TMP / "memory.json"
    store = MemoryStore.load(mem_path, context_tokens=1000, max_turns=100)
    filler = "顺手说点别的填充内容一二三四五六七八九十"
    for i in range(15):
        store.add("user", f"第{i}个问题：你还记得我们聊过什么吗，{filler}")
        store.add("assistant", f"第{i}个回答：当然记得，我们一直在聊这个，{filler}")
    store.save()

    reloaded_mem = MemoryStore.load(mem_path, context_tokens=1000, max_turns=100)
    check("记忆落盘可恢复", len(reloaded_mem.turns) == 30, str(len(reloaded_mem.turns)))

    ctx, info = store.context("SYS")
    check("上下文受预算约束", info.tokens <= 1000, f"{info.tokens}")
    check("超预算会折叠早期记忆", info.evicted > 0 and info.compactions >= 1, f"evicted={info.evicted}")
    check(
        "折叠后回到低水位",
        info.tokens <= int(1000 * 0.6) + 80,
        f"{info.tokens} > {int(1000 * 0.6)}",
    )
    check("最近 6 条不淘汰", info.kept >= 6, f"kept={info.kept}")
    check(
        "最近 6 条内容原样保留",
        [m["content"] for m in ctx[-6:]] == [t["content"] for t in store.turns[-6:]],
        "内容不一致",
    )
    check("摘要注入到 system", "记在心里" in ctx[0]["content"] and bool(store.summary), ctx[0]["content"][:40])
    check("上下文首条是 system", ctx[0]["role"] == "system", ctx[0]["role"])
    check("折叠后不会从助手消息开头", ctx[1]["role"] == "user", ctx[1]["role"])

    before = store.summarized
    for _ in range(10):
        store.add("user", "继续追问一些内容" * 4)
        store.add("assistant", "继续回答一些内容" * 4)
    ctx2, info2 = store.context("SYS")
    check("多次折叠会累积", store.summarized > before, f"{before} -> {store.summarized}")
    check("第二次折叠仍受预算约束", info2.tokens <= 1000, str(info2.tokens))

    store.add("user", "这条会失败")
    dropped = store.drop_last("user")
    check("请求失败会撤回最后一条", dropped and store.turns[-1]["role"] != "user", str(dropped))

    capped = MemoryStore.load(TMP / "cap.json", context_tokens=1000, max_turns=10)
    for i in range(30):
        capped.add("user", f"第{i}条")
    check("磁盘条数受上限约束", len(capped.turns) == 10, str(len(capped.turns)))
    check("裁掉旧记录后摘要下标不越界", 0 <= capped.summarized <= len(capped.turns))

    silent = MemoryStore.load(TMP / "silent.json", persist=False)
    silent.add("user", "不该落盘")
    check("关闭记忆时不写磁盘", not (TMP / "silent.json").exists())

    # 后台整理：本地摘录只是兜底，模型压缩才是真正的记忆
    check("折叠后留下待整理素材", store.has_pending(), str(len(store.pending_turns)))
    pending_base, pending_turns = store.take_pending()
    check(
        "取出后清空待整理",
        not store.has_pending() and bool(pending_turns),
        f"{len(pending_turns)}",
    )
    check(
        "待整理素材就是被淘汰的原文",
        any("第0个问题" in item["content"] for item in pending_turns),
        pending_turns[0]["content"][:30] if pending_turns else "无",
    )

    compacted = compress_memory(
        provider="deepseek",
        api_key="sk-test",
        previous_summary=pending_base,
        turns=pending_turns,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"choices": [{"message": {"content": "- 用户最喜欢薄荷绿"}}]}
            )
        ),
    )
    check("DeepSeek 风格记忆压缩", "薄荷绿" in compacted, compacted)
    check("模型摘要会替换本地摘录", store.apply_summary(compacted) and "薄荷绿" in store.summary)

    compacted_g = compress_memory(
        provider="gemini",
        api_key="AIza-x",
        previous_summary="",
        turns=[{"role": "user", "content": "我喜欢薄荷绿"}],
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"candidates": [{"content": {"parts": [{"text": "- 用户喜欢薄荷绿"}]}}]},
            )
        ),
    )
    check("Gemini 风格记忆压缩", "薄荷绿" in compacted_g, compacted_g)

    def memory_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    try:
        compress_memory(
            provider="deepseek", api_key="sk", previous_summary="", turns=[{"role": "user", "content": "x"}],
            transport=httpx.MockTransport(memory_error),
        )
        raised = False
    except Exception as exc:  # noqa: BLE001 - 只关心是否抛出可读错误
        raised = "429" in str(exc)
    check("记忆压缩失败会抛可读错误", raised)

    # 路径解析：默认跟着程序目录走，绝对路径则原样使用
    check(
        "记忆路径默认在程序目录下",
        config.memory_path == config_module.APP_ROOT / "memory.json",
        str(config.memory_path),
    )
    custom = Config()
    custom.memory.file = str(TMP / "custom" / "my-memory.json")
    check("绝对路径会被尊重", custom.memory_path == TMP / "custom" / "my-memory.json", str(custom.memory_path))
    blocker = TMP / "blocker"
    blocker.write_text("x", encoding="utf-8")
    check("不可写路径能识别出来", not config_module._is_writable(blocker / "sub"))
    check("可写路径能识别出来", config_module._is_writable(TMP / "writable"))

    mem_cfg_path = TMP / "memcfg.json"
    mem_cfg_path.write_text(
        '{"memory": {"context_tokens": 5, "max_turns": 5}}', encoding="utf-8"
    )
    mem_cfg = Config.load(mem_cfg_path)
    check(
        "记忆配置被夹紧",
        mem_cfg.memory.context_tokens == 400 and mem_cfg.memory.max_turns == 50,
        f"{mem_cfg.memory.context_tokens}/{mem_cfg.memory.max_turns}",
    )

    mem_dialog = SettingsWindow(config, 2.0, store)
    mem_dialog.show()
    app.processEvents()
    check(
        "设置里能看到记忆统计",
        "条对话" in mem_dialog._memory_stats.text(),
        mem_dialog._memory_stats.text(),
    )
    check("清空记忆按钮可用", mem_dialog._forget_button.isEnabled())
    mem_dialog.shutdown()

    # ---------- 10. 永久记忆：只有明确要求才记 ----------
    should_trigger = [
        "一定要记住：我最喜欢的颜色是薄荷绿",
        "别忘了周五下午三点交报告",
        "帮我记一下，我的车牌是沪A12345",
        "我最喜欢的颜色是薄荷绿，一定要记住",
        "记牢，我养了一只叫豆豆的猫",
    ]
    should_not = [
        "你还记得我最喜欢的颜色吗？",
        "我记不住这个单词",
        "今天天气不错",
        "这个梗我记得住",
    ]
    check(
        "明确要求才触发",
        all(is_remember_request(text) for text in should_trigger),
        str([t for t in should_trigger if not is_remember_request(t)]),
    )
    check(
        "疑问句和普通聊天不触发",
        not any(is_remember_request(text) for text in should_not),
        str([t for t in should_not if is_remember_request(t)]),
    )

    check(
        "提取会去掉触发词",
        extract_fact("一定要记住：我最喜欢的颜色是薄荷绿") == "我最喜欢的颜色是薄荷绿",
        extract_fact("一定要记住：我最喜欢的颜色是薄荷绿"),
    )
    check(
        "触发词在句尾也能提取干净",
        extract_fact("我最喜欢的颜色是薄荷绿，一定要记住") == "我最喜欢的颜色是薄荷绿",
        extract_fact("我最喜欢的颜色是薄荷绿，一定要记住"),
    )
    check("只剩触发词时不保存", extract_fact("一定要记住！") == "", extract_fact("一定要记住！"))

    fact_path = TMP / "facts.json"
    facts = FactStore.load(fact_path, max_facts=40)
    saved = facts.offer("一定要记住：我最喜欢的颜色是薄荷绿")
    check("命中后写入永久记忆", saved == "我最喜欢的颜色是薄荷绿" and len(facts.facts) == 1, str(saved))
    check("重复内容不重复记", facts.offer("一定要记住：我最喜欢的颜色是薄荷绿") is None and len(facts.facts) == 1)
    check("非指令不写入", facts.offer("你还记得那个吗？") is None, str(len(facts.facts)))

    reloaded_facts = FactStore.load(fact_path, max_facts=40)
    check(
        "永久记忆能落盘恢复",
        reloaded_facts.lines() == ["我最喜欢的颜色是薄荷绿"],
        str(reloaded_facts.lines()),
    )

    reloaded_facts.add("我养了一只叫豆豆的猫")
    reloaded_facts.add("周五下午三点交报告")
    block = reloaded_facts.prompt_block()
    check(
        "永久记忆拼成 system 片段",
        "永久记住" in block and "豆豆" in block,
        block.replace("\n", " | ")[:60],
    )

    ctx_with_facts, _ = store.context("SYS\n\n" + block)
    check(
        "永久记忆注入到 system",
        "豆豆" in ctx_with_facts[0]["content"],
        ctx_with_facts[0]["content"][-60:],
    )

    capped_facts = FactStore.load(TMP / "facts_cap.json", max_facts=5)
    for i in range(8):
        capped_facts.add(f"第{i}条事实")
    check(
        "永久记忆条数受上限约束",
        len(capped_facts.facts) == 5 and capped_facts.facts[-1]["text"] == "第7条事实",
        str(len(capped_facts.facts)),
    )

    remove_ok = capped_facts.remove(0)
    check("可以删除单条永久记忆", remove_ok and len(capped_facts.facts) == 4)
    capped_facts.clear()
    check("可以清空永久记忆", not capped_facts.facts and not capped_facts.prompt_block())

    silent_facts = FactStore.load(TMP / "facts_silent.json", persist=False)
    silent_facts.offer("一定要记住：这条不该落盘")
    check("关闭记忆时不写永久记忆", not (TMP / "facts_silent.json").exists())

    fact_dialog = SettingsWindow(config, 2.0, store, reloaded_facts)
    fact_dialog.show()
    app.processEvents()
    check(
        "设置里能看到永久记忆",
        fact_dialog._facts_list.count() >= 3,
        str(fact_dialog._facts_list.count()),
    )
    fact_dialog.shutdown()

    # ---------- 11. 设置窗口：最小化 / 关闭 / 保存的三种收起方式 ----------
    win = SettingsWindow(config, 2.0, store, reloaded_facts)
    type_bits = int(win.windowFlags()) & int(Qt.WindowType.WindowType_Mask)
    check(
        "设置窗口是普通窗口（会有任务栏按钮）",
        type_bits == int(Qt.WindowType.Window),
        hex(type_bits),
    )
    check(
        "设置窗口不是 Tool 窗口",
        type_bits != int(Qt.WindowType.Tool),
        hex(type_bits),
    )
    check("设置窗口带标题用于任务栏识别", win.windowTitle() == "喵斯特莉亚 · 设置", win.windowTitle())
    check("刚创建时不会自动弹出", not win.isVisible(), str(win.isVisible()))

    win.show()
    app.processEvents()
    check("打开后才可见", win.isVisible(), str(win.isVisible()))

    win.showMinimized()
    app.processEvents()
    check("最小化后进入任务栏状态", win.isMinimized(), str(win.isMinimized()))
    win.show_and_focus()
    app.processEvents()
    check("可以从任务栏恢复", not win.isMinimized() and win.isVisible(), str(win.isMinimized()))
    check(
        "首次打开不会停在屏幕左上角",
        win.geometry().topLeft() != QPoint(0, 0),
        str(win.geometry().topLeft()),
    )
    first_pos = win.geometry().topLeft()
    win.hide()
    app.processEvents()
    win.show_and_focus()
    app.processEvents()
    check("再次打开保持原位置", win.geometry().topLeft() == first_pos, str(win.geometry().topLeft()))

    applied_configs: list[object] = []
    win.applied.connect(applied_configs.append)
    win._save()
    app.processEvents()
    check(
        "保存后直接收起窗口",
        bool(applied_configs) and not win.isVisible(),
        f"applied={len(applied_configs)} visible={win.isVisible()}",
    )
    check(
        "保存收起不等于最小化（任务栏图标要消失）",
        not win.isMinimized(),
        str(win.isMinimized()),
    )

    win.show_and_focus()
    app.processEvents()
    check("收起后还能再次打开", win.isVisible() and not win.isMinimized(), str(win.isVisible()))

    win.close()
    app.processEvents()
    check("点关闭会隐藏窗口", not win.isVisible(), str(win.isVisible()))
    check("点关闭不会留下最小化状态", not win.isMinimized(), str(win.isMinimized()))

    win.shutdown()
    app.processEvents()
    check("退出时能真正关闭", not win.isVisible(), str(win.isVisible()))

    # ---------- 12. 设置里的网络任务生命周期 ----------
    net = SettingsWindow(config, 2.0)
    net._worker = None

    def slow_task():
        QTest.qWait(500)
        return "ok"

    first = net._start(slow_task, net._show_test)
    second = net._start(slow_task, net._show_test)
    check("同一时间只跑一个网络任务", first is True and second is False, f"{first}/{second}")

    for _ in range(80):
        QTest.qWait(50)
        if net._worker is None:
            break
    QTest.qWait(200)
    check("任务结束后清空 worker 引用", net._worker is None, "仍指向已销毁对象")
    check("任务结束后按钮恢复可用", net._test_button.isEnabled() and net._fetch.isEnabled())

    again = net._start(lambda: "ok2", net._show_test)
    check("可以连续发起第二次任务", again is True, str(again))
    for _ in range(80):
        QTest.qWait(50)
        if net._worker is None:
            break
    QTest.qWait(200)

    closed_ok = True
    try:
        net.shutdown()
    except Exception as exc:  # noqa: BLE001 - 只关心是否抛异常
        closed_ok = False
        detail = f"{type(exc).__name__}: {exc}"
    check("有网络任务历史时关闭窗口也不抛异常", closed_ok, "" if closed_ok else detail)

    # ---------- 12. 汇总 ----------
    report = {
        "python": sys.version.split()[0],
        "platform": app.platformName(),
        "device_pixel_ratio": window.devicePixelRatioF(),
        "screen_logical": [app.primaryScreen().size().width(), app.primaryScreen().size().height()],
        "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
        "artifacts": [str(p.relative_to(ROOT)) for p in (pet_png, bubble_png, OUT / "preview.png")],
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if not ok else ""))
    print(f"\n{len(checks) - len(failed)}/{len(checks)} 通过 · 平台 {app.platformName()} · dpr {window.devicePixelRatioF()}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
