# 喵斯特莉亚 Q 版桌宠素材

使用用户指定的 shengtu-skill 和 GPT 图像编辑服务生成。游戏截图确定人物身份，上一轮立绘确定补全服装，用户提供的旧桌宠仅参考大头小身体的比例。

当前方案：**只保留 6 张静态差分，放弃行走动画**。桌宠程序按状态切换这 6 张图即可。

## 最终素材

`poses/` 下六张 **512×512 RGBA** PNG，原生分辨率，未经放大：

| 文件 | 状态 | 尺寸 |
| --- | --- | --- |
| `poses/idle.png` | 待机 | 512×512 |
| `poses/thinking.png` | 思考 | 512×512 |
| `poses/working.png` | 工作 | 512×512 |
| `poses/resting.png` | 休息 | 512×512 |
| `poses/happy.png` | 开心招手 | 512×512 |
| `poses/surprised.png` | 惊讶 | 512×512 |

- 直接取自 `source/poses_sheet.png` 的 3 列 × 2 行网格，每格正好 512×512，**没有缩放或重采样**，线条保持原生锐度。
- 六张共用同一套定位基准：人物水平中心对齐 x=256，鞋底对齐 y=488，下方留白 24px。原图集存在格间漂移（横向最多 33px、脚底 14px），已用纯平移抵消，切换状态时不会跳动。
- `poses_preview.jpg`：六种状态的验收总览，棋盘格仅用于显示透明边界。
- `asset_check.json`：每张的尺寸、alpha 范围、可见 bbox、来源格坐标和平移量。

## 原稿

- `source/poses_sheet.png`：1536×1024 生成图集，六个状态的原稿，切图唯一输入。
- `source/chibi_base.png`：Q 版基础立绘，服装与比例基准。
- 生成时用过的布局参考图与透明底基础图体积较大、且不是运行时素材，未包含在仓库里。

## 重新切图

```powershell
python tools/slice_poses.py
```

脚本会重切 6 张、覆盖 `asset_check.json` 并重绘 `poses_preview.jpg`；任一张尺寸、通道、四角透明或基线不符合预期会直接断言失败。

## 已移除

因放弃行走动画，以下内容已删除（约 17.4 MB，先进回收站）：`frames/`（256px 静态帧 + 左右行走共 16 帧）、`prompts/`、`walk_strip.png`、`walk_left_strip.png`、两个行走 GIF 与两张帧序预览、`walk_left_check.json`、`source/walk_frames/`、`source/walk_sheet*.png`、`source/walk_layout.png`，以及只服务旧 256px/行走流程的 `tools/slice_chibi_sheets.py`、`tools/finish_chibi_assets.py`、`tools/prepare_chibi_guides.py`。

`tools/generate_chibi_assets.py` 保留作为生成记录（含身份提示词与调用参数），其中 `walk` 分支已随行走方案作废。

## 说明

- 图集 alpha 最大值为 254 而非 255（生成链路遗留），肉眼无差别；若某些渲染器对半透明边缘敏感，可整体线性拉伸到 0–255。
- 六张之间仍有轻微线条与表情差异，这是逐格生成的固有问题；需要统一笔触时可后续单张重绘。
- 本轮只做素材整理与切图，未编写桌宠运行逻辑。
