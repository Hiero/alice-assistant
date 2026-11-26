"""Make the voice files for Alice's offline voice on the phone (android/README.md).

    uv run --project backend python tools/make_phone_voice.py <kokoro-multi-lang-v1_0 folder>

The folder is sherpa-onnx's Kokoro v1.0 release, unpacked. Its voices.bin is the same 54 voices as the PC's
voices-v1.0.bin, as raw float32 arrays of 510 x 256. This writes voices-alice.bin next to it: a copy with
speaker 0 replaced by her blend (ALICE_VOICE, as in backend/alice/voice.py), which the phone asks for.
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from alice.voice import MODEL_DIR, VOICE  # noqa: E402

STYLE = (510, 256)


def main() -> None:
    folder = Path(sys.argv[1])
    voices = np.fromfile(folder / "voices.bin", dtype=np.float32).reshape(-1, *STYLE)
    pc = np.load(MODEL_DIR / "voices-v1.0.bin")
    # The layouts must agree, or the blend would mix the wrong voices.
    if not np.allclose(voices[2], pc["af_bella"].reshape(STYLE)):
        sys.exit("voices.bin doesn't match the PC's voices: is this kokoro-multi-lang-v1_0?")
    parts = [part.split(":") for part in VOICE.split(",")] if ":" in VOICE else [(VOICE, "1")]
    total = sum(float(weight) for _, weight in parts)
    voices[0] = sum(pc[name.strip()].reshape(STYLE) * (float(weight) / total) for name, weight in parts)
    voices.astype(np.float32).tofile(folder / "voices-alice.bin")
    print(f"wrote {folder / 'voices-alice.bin'} with {VOICE} as speaker 0")


if __name__ == "__main__":
    main()
