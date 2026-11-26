# Alice

A local AI assistant that floats on the [niri](https://github.com/YaLTeR/niri) Wayland desktop. She lives in a storybook: idle as a slim spine on your screen, and opening up to read, talk and act when you call her.

Everything runs on your own machine by default: the language model (through Ollama), speech recognition and her voice.

**Full documentation is in the [wiki](../../wiki):** installation, usage, every feature, configuration, privacy, the phone app, artwork and development.

## What she does

- **Talks and listens:** chat by typing or by voice, with spoken replies in a local voice, lip sync and painted moods
- **Uses your desktop:** opens apps, runs commands, manages windows, notes, alarms, the clipboard and files, and can look at your screen when you ask. Anything that changes your computer waits for your **Allow** on a card showing the real command.
- **Looks things up:** web search with sources, weather and forecasts, news, currency, films and series without spoilers, and Steam prices, sales, reviews and recommendations
- **Plays music** from your Jellyfin server
- **Remembers** preferences and birthdays, greets you on holidays and gives a morning summary
- **Has a wardrobe:** holiday outfits, a proud Queen Alice and a sarcastic Evil Alice, each with her own manner
- **Comes along on your phone:** an Android companion that talks to her over Tailscale, with a small on-phone model when the PC is out of reach

See [Features](../../wiki/Features) for the full list.

## Quick start

You need Linux with niri and PipeWire, and a GPU with about 16 GB of VRAM for the tested setup. On Arch Linux:

```sh
sudo pacman -S quickshell uv ollama pipewire grim libnotify glib2 curl
sudo systemctl enable --now ollama
git clone https://github.com/Hiero/alice-assistant
cd alice-assistant
./install.sh
```

Then add the keybinds the installer prints to your niri config. `Mod+Shift+A` types to her and `Mod+Shift+S` talks. See [Installation](../../wiki/Installation) and [Usage](../../wiki/Usage) for the details.

The repository includes only placeholder drawings of her; see [Artwork](../../wiki/Artwork) to add or paint your own.

## Made with AI

Alice was developed with the help of AI coding assistants. The code is reviewed and tested on a real desktop, but expect rough edges, and read what an action will do before you approve it.

## License

Alice is free software under the [GNU General Public License v3.0](LICENSE) or any later version: you may use, change and share her, and anyone who shares a changed version must share its source under the same license.

## Credits

Film and series data comes from [TMDB](https://www.themoviedb.org/) and [OMDb](https://www.omdbapi.com/). This product uses the TMDB API but is not endorsed or certified by TMDB. Weather comes from [Open-Meteo](https://open-meteo.com/), exchange rates from the European Central Bank via [Frankfurter](https://frankfurter.dev/), and the passages she reads from Lewis Carroll's Alice books, which are in the public domain.
