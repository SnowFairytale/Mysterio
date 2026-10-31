"""Slice the 3x2 pose sheet into six 512x512 desktop-pet stills.

No resampling: each cell is cropped at native size, trimmed to its visible
alpha bounds, then translated so every pose shares one horizontal centre and
one sole baseline. That keeps the artwork's original pixels crisp while
removing the grid drift so state switches do not make the sprite jump.
"""
from pathlib import Path
import json

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1] / "miastria_chibi"
SHEET = ROOT / "source" / "poses_sheet.png"
OUT = ROOT / "poses"
CELL = 512
ALPHA_FLOOR = 24      # same visibility threshold the earlier check used
CENTER_X = 256
SOLE_Y = 488          # keeps the 244/256 bottom margin of the old 256px frames
POSES = [("idle", "待机"), ("thinking", "思考"), ("working", "工作"),
         ("resting", "休息"), ("happy", "开心招手"), ("surprised", "惊讶")]
FONT = "C:/Windows/Fonts/msyh.ttc"


def visible_bbox(image):
    alpha = np.array(image.getchannel("A"))
    ys, xs = np.where(alpha > ALPHA_FLOOR)
    if len(xs) == 0:
        raise ValueError("empty cell")
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def checker_tile(size, tile=16):
    w, h = size
    canvas = Image.new("RGB", size, "#f4f5f8")
    draw = ImageDraw.Draw(canvas)
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            if (x // tile + y // tile) % 2:
                draw.rectangle((x, y, x + tile - 1, y + tile - 1), fill="#e3e6ed")
    return canvas


def main():
    sheet = Image.open(SHEET).convert("RGBA")
    cols, rows = sheet.width // CELL, sheet.height // CELL
    if (cols, rows) != (3, 2) or sheet.width % CELL or sheet.height % CELL:
        raise ValueError(f"unexpected sheet size {sheet.size}")

    OUT.mkdir(parents=True, exist_ok=True)
    frames, report = [], []
    for index, (name, _) in enumerate(POSES):
        row, col = divmod(index, 3)
        cell = sheet.crop((col * CELL, row * CELL, (col + 1) * CELL, (row + 1) * CELL))
        left, top, right, bottom = visible_bbox(cell)
        sprite = cell.crop((left, top, right, bottom))
        offset = (CENTER_X - sprite.width // 2, SOLE_Y - sprite.height)
        frame = Image.new("RGBA", (CELL, CELL))
        frame.alpha_composite(sprite, offset)
        path = OUT / f"{name}.png"
        frame.save(path, optimize=True)

        saved = Image.open(path)
        assert saved.mode == "RGBA" and saved.size == (CELL, CELL)
        alpha = saved.getchannel("A")
        box = visible_bbox(saved)
        assert alpha.getpixel((0, 0)) == 0 and alpha.getpixel((CELL - 1, CELL - 1)) == 0
        assert box[0] > 0 and box[1] > 0 and box[2] < CELL and box[3] < CELL
        assert box[3] == SOLE_Y and abs((box[0] + box[2]) / 2 - CENTER_X) <= 1
        frames.append(saved)
        report.append({
            "file": str(path.relative_to(ROOT)), "size": saved.size, "mode": saved.mode,
            "alpha_range": alpha.getextrema(), "bbox": box,
            "source_cell": {"col": col, "row": row, "bbox": [left, top, right, bottom]},
            "shift": {"x": offset[0] - left, "y": offset[1] - top},
        })
    (ROOT / "asset_check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    cell_w, cell_h = CELL, CELL + 46
    preview = Image.new("RGB", (cell_w * 3, cell_h * 2), "#f1f2f7")
    font = ImageFont.truetype(FONT, 22)
    for index, (frame, (_, label)) in enumerate(zip(frames, POSES)):
        tile = checker_tile((cell_w, cell_h))
        ImageDraw.Draw(tile).text((16, 11), label, font=font, fill="#312638")
        tile.paste(frame, (0, 44), frame)
        preview.paste(tile, ((index % 3) * cell_w, (index // 3) * cell_h))
    preview.save(ROOT / "poses_preview.jpg", quality=94)

    for item in report:
        print(f"{Path(item['file']).name:14s} {item['size']} bbox={item['bbox']} "
              f"shift=({item['shift']['x']:+d},{item['shift']['y']:+d})")
    print(f"Wrote {len(report)} aligned {CELL}x{CELL} poses -> {OUT}")


if __name__ == "__main__":
    main()
