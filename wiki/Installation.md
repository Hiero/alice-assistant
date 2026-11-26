## Requirements

- Linux with niri and PipeWire
- An NVIDIA GPU with about 16 GB of VRAM is the tested setup. Other setups may need a smaller model.
- About 12 GB of disk space
- `quickshell`, `uv`, `ollama` (running as a service), `grim`, `libnotify`, `glib2`, `curl`

On Arch Linux:

```sh
sudo pacman -S quickshell uv ollama pipewire grim libnotify glib2 curl
sudo systemctl enable --now ollama
```

## Installing

```sh
git clone https://github.com/Hiero/alice-assistant
cd alice-assistant
./install.sh
```

The script installs the backend, downloads the voice and language models, and enables autostart for your graphical session. It's safe to run again. Use `./install.sh --no-services` to skip autostart.

Afterwards, add the keybinds it prints to the `binds` section of your niri config.

Next: [[Usage]] for the keybinds, and [[Configuration]] for keys and settings.

## Uninstalling

```sh
systemctl --user disable --now alice-backend alice-shell
ollama rm alice
```

Then remove `~/.local/share/alice`, `~/.config/alice`, and this folder.
