Set these in `~/.config/alice/env`, then run `systemctl --user restart alice-backend alice-shell`.

| Variable | Default | Purpose |
|---|---|---|
| `ALICE_OUTPUT` | Set at install | Monitor name to float on |
| `ALICE_BASE_URL` | Local Ollama | Any OpenAI-compatible endpoint |
| `ALICE_API_KEY` | `ollama` | API key for that endpoint |
| `ALICE_MODEL` | `alice` | Model name |
| `ALICE_REASONING_EFFORT` | `none` | Reasoning setting. Leave empty to omit it. |
| `ALICE_REPLY_LANGUAGE` | `en` | `en`, or `auto` to follow English and Portuguese |
| `ALICE_WHISPER_MODEL` | `small` | Speech recognition model, such as `medium` |
| `ALICE_VOICE` | `af_bella:70,af_nicole:10,af_sky:10,bf_alice:10` | Kokoro voice, or a weighted blend of voices |
| `ALICE_VOICE_PITCH` | `1.2` | Raises pitch and resonance. `1.0` leaves the voice unchanged. |
| `ALICE_VOICE_PACE` | `1.03` | Speaking pace |
| `JELLYFIN_URL` | None | Address of your Jellyfin server, for music |
| `JELLYFIN_API_KEY` | None | A Jellyfin API key. Keep this file private with `chmod 600`. |
| `TMDB_API_KEY` | None | A free key from themoviedb.org (Settings → API), for films and series |
| `OMDB_API_KEY` | None | A free key from omdbapi.com, for IMDb, Rotten Tomatoes and Metacritic scores. Optional. |
| `ALICE_WATCH_REGION` | `BR` | Country whose streaming services and age ratings she reports |
| `ALICE_STEAM_COUNTRY` | `br` | Steam store region, which sets the currency of game prices |
| `ALICE_WEATHER_LOCATION` | None | Your home city for the weather, like `Campinas, Sao Paulo`. She can also just remember it. |
| `ALICE_NEWS_EDITION` | `us` | Default Google News edition: `us` for English or `br` for Brazilian news |
| `JELLYFIN_USER` | Most recently active | Whose Jellyfin playlists and favorites to use |
| `ALICE_MORNING` | `weather,wishlist` | What the morning summary shows, or `off` |
| `ALICE_STEAM_ROOT` | Found automatically | Steam's install folder, if it's somewhere unusual |
| `ALICE_STEAM_DIR` | `~/.local/share/alice/steam` | Where each day's Steam releases are saved as CSV |
| `ALICE_CONTEXT_TOKENS` | `16384` | The model's context window, matching `num_ctx` in `backend/Modelfile`. A request that fills 80% of it is reported in `journalctl --user -u alice-backend`. |
| `ALICE_KEEP_CHAT` | `1` | Keeps the current conversation in `~/.local/state/alice/chat.json`, readable only by you, so a restart doesn't make her forget it. `0` keeps it in memory only. |
| `ALICE_DEBUG` | Off | Set to `1` to log what she hears, the tools she calls and her answers, for troubleshooting |
| `ALICE_DEBUG_LOG` | `~/.local/state/alice/debug.log` | Where the debug log goes. It holds whole conversations, so it's private to you and rotates at 2 MB. |
| `ALICE_OUTFIT` | By date | Force an outfit to try it, like `halloween`, or empty for her usual dress |
| `ALICE_WORN_FILE` | `~/.local/state/alice/outfit` | Remembers the wardrobe outfit she has on |
| `ALICE_NOTES_DIR` | `~/.local/share/alice/notes` | Where notes are saved |
| `ALICE_BIRTHDAYS_FILE` | `~/.local/share/alice/birthdays.json` | Birthdays she remembers |
| `ALICE_MEMORY_FILE` | `~/.local/share/alice/memory.md` | Her saved memories |

Screen viewing is refused when `ALICE_BASE_URL` isn't local, so screenshots never go to a cloud provider.
