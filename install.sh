#!/usr/bin/env bash
# Sets up Alice on this machine. Safe to run again.
# Usage: ./install.sh [--no-services]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
MODEL_DIR="$DATA_HOME/alice/models"
KOKORO_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
BASE_MODEL="gemma4:12b"

INSTALL_SERVICES=1
for arg in "$@"; do
    case "$arg" in
        --no-services) INSTALL_SERVICES=0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

echo "==> Checking required programs"
missing=()
for tool in qs uv ollama niri pw-record pw-cat pw-play pactl grim notify-send gio curl systemctl; do
    command -v "$tool" >/dev/null || missing+=("$tool")
done
if ((${#missing[@]})); then
    echo "Missing: ${missing[*]}" >&2
    echo "On Arch: sudo pacman -S quickshell uv ollama niri pipewire grim libnotify glib2 curl" >&2
    exit 1
fi

echo "==> Installing the Python backend"
(cd "$ROOT/backend" && uv sync --python 3.12)

echo "==> Downloading the speech voice, if needed"
mkdir -p "$MODEL_DIR"
for file in kokoro-v1.0.onnx voices-v1.0.bin; do
    if [[ ! -s "$MODEL_DIR/$file" ]]; then
        curl -fL -o "$MODEL_DIR/$file.part" "$KOKORO_URL/$file"
        mv "$MODEL_DIR/$file.part" "$MODEL_DIR/$file"
    fi
done

echo "==> Preparing the language model"
ollama pull "$BASE_MODEL"
ollama create alice -f "$ROOT/backend/Modelfile"

# Her default pictures, unless pictures of your own are already in shell/assets. All or nothing, since the faces
# must line up pixel for pixel.
if [[ ! -e "$ROOT/shell/assets/alice-neutral.png" ]]; then
    echo "==> Adding her default pictures"
    cp -r "$ROOT/shell/default-art/." "$ROOT/shell/assets/"
fi

echo "==> Writing service files"
for template in "$ROOT"/install/*.service.in; do
    sed -e "s|@ROOT@|$ROOT|g" -e "s|@UV@|$(command -v uv)|g" -e "s|@QS@|$(command -v qs)|g" \
        "$template" > "${template%.in}"
done

if ((INSTALL_SERVICES)); then
    mkdir -p "$CONFIG_HOME/alice"
    touch "$CONFIG_HOME/alice/env"
    chmod 600 "$CONFIG_HOME/alice/env"
    if ! grep -qs '^ALICE_OUTPUT=' "$CONFIG_HOME/alice/env"; then
        output="$(niri msg -j focused-output 2>/dev/null | sed -n 's/.*"name":"\([^"]*\)".*/\1/p' | head -1 || true)"
        printf 'ALICE_OUTPUT=%s\n' "$output" >> "$CONFIG_HOME/alice/env"
    fi
    echo "==> Enabling autostart"
    systemctl --user enable --now "$ROOT/install/alice-backend.service" "$ROOT/install/alice-shell.service"
fi

cat <<DONE

Alice is installed.

Add these to the binds section of ~/.config/niri/config.kdl:

    Mod+Shift+A { spawn "qs" "-p" "$ROOT/shell" "ipc" "call" "alice" "prompt"; }
    Mod+Shift+S { spawn "qs" "-p" "$ROOT/shell" "ipc" "call" "alice" "talk"; }
    Mod+Shift+X { spawn "qs" "-p" "$ROOT/shell" "ipc" "call" "alice" "setState" "dormant"; }

DONE
