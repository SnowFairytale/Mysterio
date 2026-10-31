"""Generate artwork through the user-selected shengtu skill; no pet runtime code."""
from pathlib import Path
import argparse
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "miastria_chibi"

# 生图脚本与三张参考图属于本地素材，没有随仓库提供。
# 想复现生成流程时，用环境变量指向你自己的文件即可。
SKILL = Path(os.environ.get("SHENGTU_SKILL", "references/generate_image.py"))
SCREENSHOT = Path(os.environ.get("PET_REF_SCREENSHOT", "references/game-screenshot.jpg"))
PROPORTIONS = Path(os.environ.get("PET_REF_PROPORTIONS", "references/proportions.png"))
IDENTITY_ART = Path(os.environ.get("PET_REF_IDENTITY_ART", "references/identity-art.png"))

IDENTITY = """Miastria, the girl in the original game screenshot: very long lavender-silver
hair in a high side ponytail with a distinct white front lock, blue eyes with small pink
highlights, dark cat-ear headphones with MAGENTA ear interior on image left and BLUE ear
interior on image right, magenta ear cups, white high-collar blouse, magenta ribbon and
small silver bell, black chest frill, loose black sleeves with magenta cuffs, a short black
pleated skirt with magenta/white hem, dark stockings, black/magenta ankle sneakers.
Simplify small details into clean readable shapes. Keep colors and identifying accessories."""

BASE_PROMPT = """Create ONE brand-new super-deformed CHIBI desktop-pet sprite. Reference 1
is the character's original portrait and determines identity. Reference 2 is our earlier
full-body design and determines outfit only. Reference 3 is a DIFFERENT character and
determines ONLY the cute big-head/small-body PROPORTIONS and compact sprite readability.
Do not copy reference 3's identity, hair color, ears, eyes or costume.
Use approximately 2.2 heads tall excluding the headset ears: huge round head taking about
45 percent of head-to-foot height, large expressive eyes, tiny compact torso, very short
legs, small rounded hands and shoes. The requested result must look like a chibi desktop
pet at 180-220 pixels high, with crisp readable outlines and simplified cel shading.
""" + IDENTITY + """
Neutral idle stance, both feet on the same baseline, relaxed arms, friendly closed-mouth
smile. Full body and entire ponytail visible, centered, spacious margins on all sides.
Transparent alpha background, isolated character, no gradient or glow or cast shadow,
no scenery, no ground, no text, no logos, no border, no checkerboard baked into artwork.
Do not make a normal-proportion anime woman or long legs. This is a small cute chibi."""

POSE_PROMPT = """Edit the FIRST attached image, a precisely arranged 3-column by 2-row
sprite sheet. The SECOND attached image is the definitive CHIBI character design.
Preserve that exact big-head/tiny-body chibi identity, head shape, body proportions,
outfit, colors, ear asymmetry, ponytail, eyes, outlines and shading in EVERY cell.
The sheet is 1536x1024 pixels; each cell is exactly 512x512. Keep one complete chibi
inside each cell with empty transparent gutters. Keep all sprites at identical scale.
All standing sprites have their shoe soles on the same relative baseline, 480 pixels
from the top of their own cell. Do not add any written cell labels or grid lines.
Row 1 left: relaxed neutral idle with arms down.
Row 1 middle: thoughtful, one little finger at chin and eyes looking upward.
Row 1 right: working, operating a tiny dark laptop with magenta accents at waist level.
Row 2 left: resting with eyes closed, both little hands gently clasped in front.
Row 2 middle: cheerful open smile and one hand waving hello.
Row 2 right: surprised with wide eyes, small open mouth and both hands near cheeks.
Only those six states. Each full character including ears, hair and shoes must fit
inside its own cell. Same character, same drawing style, no outfit redesign.
Transparent alpha background, no scenery, no glow, no floor shadows, no text.
""" + IDENTITY

WALK_ACTIONS = [
    "Left foot lands well forward while the right leg stretches behind; arms swing gently opposite the legs.",
    "She plants weight on the left foot and bends her right knee, lifting the right heel behind.",
    "The right knee passes forward under the skirt while the left foot supports her.",
    "She reaches the right foot forward for the next step, with her left leg trailing.",
    "Right foot lands well forward while the left leg stretches behind; arms swing gently opposite the legs.",
    "She plants weight on the right foot and bends her left knee, lifting the left heel behind.",
    "The left knee passes forward under the skirt while the right foot supports her.",
    "She reaches the left foot forward for the next step, transitioning naturally back to the first pose.",
]

WALK_PROMPT = """Edit the FIRST image into one clean desktop-pet sprite. Reference 1 is
the exact chibi model: match her face, 2.2-head proportions, long lavender-silver ponytail,
white front hair lock, blue eyes, asymmetrical magenta/blue cat-ear headset, headphones,
white-and-black blouse, magenta bow, black skirt, dark tights and sneakers exactly.
Reference 2 is the original game screenshot for identity. Keep the same front-right
three-quarter camera and cheerful expression. Change only the pose to this walk-cycle
phase: {action}
Keep the head, headset ears and torso at the same screen position and scale as the base.
Full figure fully in frame, both feet visible, generous clear margins. Transparent alpha
background only. Do not draw a checkerboard, floor, shadow, glow, scene, border or text.
Keep crisp dark outlines separating the pale hair from its transparent background.
Preserve the compact chibi body and short legs; do not make her tall or realistic.
""" + IDENTITY


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["base", "poses", "walk"])
    parser.add_argument("--frame", type=int, choices=range(8))
    args = parser.parse_args()
    (OUT / "source").mkdir(parents=True, exist_ok=True)
    (OUT / "prompts").mkdir(parents=True, exist_ok=True)
    if args.stage == "base":
        prompt, size, output = BASE_PROMPT, "1024x1024", OUT / "source" / "chibi_base.png"
        references = [SCREENSHOT, IDENTITY_ART, PROPORTIONS]
    elif args.stage == "poses":
        prompt, size, output = POSE_PROMPT, "1536x1024", OUT / "source" / "poses_sheet.png"
        references = [OUT / "source/poses_layout.png", OUT / "source/chibi_base_transparent.png"]
    else:
        if args.frame is None:
            parser.error("walk stage requires --frame 0..7")
        prompt = WALK_PROMPT.format(action=WALK_ACTIONS[args.frame])
        size, output = "1024x1024", OUT / "walk" / f"{args.frame:02}.png"
        references = [OUT / "source/chibi_base_transparent.png", SCREENSHOT]
    prompt_path = OUT / "prompts" / (f"walk-{args.frame:02}.txt" if args.stage == "walk" else args.stage + ".txt")
    prompt_path.write_text(prompt, encoding="utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(SKILL), "--provider", "gpt", "--mode", "edit",
               "--quality", "medium", "--prompt", prompt, "--size", size, "--out", str(output)]
    for ref in references:
        command.extend(["--image", str(ref)])
    print("Generating", args.stage, "via shengtu-skill ->", output, flush=True)
    subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
