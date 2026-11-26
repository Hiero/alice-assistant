The repository only includes simple placeholder drawings, and her painted art is never committed (`shell/assets/*.png` and `art/` are ignored by git). Alice works with the placeholders; for a painted Alice, either draw your own or paint one with the tools below.

## What the shell looks for

Add PNG files with these names to `shell/assets/`:

| File | Used for | Suggested size |
|---|---|---|
| `alice-neutral.png` | Default and speaking with mouth closed | 480x702, transparent |
| `alice-listening.png` | Listening | 480x702, transparent |
| `alice-thinking.png` | Thinking | 480x702, transparent |
| `alice-speaking.png` | Speaking with mouth open | 480x702, transparent |
| `alice-blink.png`, `alice-happy.png`, `alice-halflid.png`, `alice-blush.png` | Closed, smiling, drooping eyes and a blush, laid over any face for blinks and moods | 480x702, transparent |
| `alice-mood-happy.png`, `alice-mood-happy-eyes.png`, and the same for excited, surprised, curious, worried, sad, pout, embarrassed and sleepy | A painted face for each mood, laid over any face; the `-eyes` one leaves her mouth free while she talks. Optional: without them she uses the overlays above. | 480x702, transparent |
| `outfits/<name>/alice-neutral.png` (and listening, thinking, speaking) | Wardrobe and holiday outfits: `queen`, `evil`, `christmas`, `easter`, `halloween`, `new_year`, `sweethearts`. A taller picture leaves room for a hat. | 480 wide, transparent |
| `book-spine.png` | Closed book | 76x392, transparent |
| `book-back.png` | Open book | 484x350, transparent |
| `party-hat.png` | Worn on birthdays | 124x156, transparent |

Restart with `systemctl --user restart alice-shell`.

All faces must line up pixel for pixel, with her mouth in the same spot, because the open mouth is cut from `alice-speaking.png`. She moves without extra art: `shell/shaders/rig.frag` bends the picture so her head tilts, turns and nods, she breathes and her hair sways. The head pivot and face centre it uses are measured on the art (`neck` and `faceCentre` in `shell/shell.qml`); if you draw her face somewhere else, move them too.

## Painting her with a local ComfyUI

The tools in `tools/` paint the whole set with [ComfyUI](https://github.com/comfyanonymous/ComfyUI) and an Illustrious (SDXL anime) checkpoint, by default `waiIllustriousSDXL_v170.safetensors`. A LoRA keeps her face and outfits in one style; it is set with `ALICE_ART_LORA` and `ALICE_ART_LORA_WEIGHT` (0.8 by default), and an empty `ALICE_ART_LORA` paints without one. Use a LoRA whose licence allows what you do with the pictures; none is included. `COMFYUI_URL` and `ALICE_ART_CHECKPOINT` choose the server and the model.

Start ComfyUI, and free the GPU first with `ollama stop alice`. On the tested GPU each picture takes about 10 seconds. Then, in this order:

```sh
# 1. Her four states from a base portrait (art/set/base.png, 832x1216 on white), with the background cut away
uv run --with pillow --with numpy python tools/make_base.py
# 2. Blink, smiling eyes, half-closed lids and blush, drawn over her face
uv run --with opencv-python-headless --with numpy python tools/make_faces.py
# 3. A painted face for each mood
uv run --with pillow --with numpy python tools/make_expressions.py
# 4. Every outfit; Evil Alice also gets red-eyed copies of the overlays from steps 2 and 3
uv run --with pillow --with numpy python tools/make_outfit.py
# 5. The phone app's launcher icon
uv run --with pillow python tools/make_phone_icon.py
```

Every painting tool takes `--try N` to paint N candidates onto a contact sheet instead of writing assets, for example `tools/make_expressions.py --try 4 sad pout` or `tools/make_outfit.py --try 4 halloween`. Put the seed you like in the table at the top of the tool (`STATES`, `MOODS` or `OUTFITS`), then run it without `--try`.

The face positions (`BOX`, `AREA`, `MOUTH` and `EYES` in `make_expressions.py`, `EYES` and `CHEEKS` in `make_faces.py`, `KEEP` and `EYES` in `make_outfit.py`) are measured on the current base. After painting a new base, measure them again, or the lids and moods land in the wrong place. `make_base.py` documents how the current base was painted.

### Tips

- Outfits keep her face and repaint everything around it. Tags go in `OUTFITS`, and things to keep out in its second column.
- A hat taller than her hair needs room above her head (third column) and often a rough sketch to start from, since the model otherwise paints the empty space as background; see `witch_hat` and `SKETCHES` in `make_outfit.py`.
- Her irises are yellow like her hair, so the tools exempt the middle of each eye from the "keep the bangs on top" rule.
- Some looks won't take with a strong LoRA (eyes looking up, closed eyes): try another wording, or lower `ALICE_ART_LORA_WEIGHT` for that run.
