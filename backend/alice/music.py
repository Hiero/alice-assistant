"""Find music on a Jellyfin server and play it through a background mpv.

JellyBox reports that it doesn't accept remote control, so songs can't be started inside
it. It can still be paused or skipped through playerctl, which is used when mpv is idle.
"""

import asyncio
import json
import os
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
MPV_SOCKET = Path(os.environ["XDG_RUNTIME_DIR"]) / "alice-mpv.sock"
# JellyBox's name on MPRIS, as playerctl -l lists it.
EXTERNAL_PLAYER = os.environ.get("ALICE_EXTERNAL_PLAYER", "com.ryanheise.bg_demo.channel.audio")
MAX_QUEUE = 100
MAX_SEARCH_RESULTS = 10
RESUME_WATCH_STEPS = 12  # Half-second checks after resuming, so 6 seconds of guarding against a stray pause.
# What the model passes as a query when the user asked for no song in particular.
GENERIC_QUERIES = {"", "any", "anything", "something", "music", "song", "songs", "a song", "surprise", "surprise me", "random", "random song", "a random song", "random music"}


def read_settings() -> tuple[str, str]:
    """Read from the environment first, then the private settings file."""
    values = dict(os.environ)
    path = CONFIG_HOME / "alice/env"
    if path.exists():
        for line in path.read_text().splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and not name.startswith("#"):
                values.setdefault(name, value.strip())
    url, key = values.get("JELLYFIN_URL", "").rstrip("/"), values.get("JELLYFIN_API_KEY", "")
    if not url or not key:
        raise RuntimeError("Jellyfin isn't configured. Set JELLYFIN_URL and JELLYFIN_API_KEY.")
    return url, key


def auth_header(key: str) -> str:
    return f'Authorization: MediaBrowser Token="{key}"'


def fetch(path: str, **params: str) -> dict | list:
    url, key = read_settings()
    query = "?" + urllib.parse.urlencode(params) if params else ""
    name, value = auth_header(key).split(": ", 1)
    request = urllib.request.Request(url + path + query, headers={name: value})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Jellyfin returned HTTP {error.code}.") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError("Couldn't reach the Jellyfin server.") from error


def describe(item: dict) -> dict:
    artists = item.get("Artists") or ([item["AlbumArtist"]] if item.get("AlbumArtist") else [])
    return {"id": item["Id"], "title": item.get("Name"), "artist": ", ".join(artists), "album": item.get("Album")}


def plain(text: str) -> str:
    """Fold case and accents, so "Legiao" matches "Legião"."""
    decomposed = unicodedata.normalize("NFKD", text.casefold().strip())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def rank_songs(items: list[dict], query: str, artist: str) -> list[dict]:
    """Keep the artist's songs if one was named, then put an exact title first and shorter titles before longer
    ones, since "Help" should beat "Help Me Rhonda"."""
    wanted, wanted_artist = plain(query), plain(artist)
    if wanted_artist:
        items = [i for i in items if wanted_artist in plain(" ".join(i.get("Artists") or []))]
    return sorted(items, key=lambda i: (plain(i.get("Name", "")) != wanted, len(i.get("Name", ""))))


def search_songs(query: str, artist: str = "") -> list[dict]:
    found = fetch("/Items", SearchTerm=query, IncludeItemTypes="Audio", Recursive="true", Limit="50")
    return rank_songs(found.get("Items", []), query, artist)


def album_tracks(query: str, artist: str) -> list[dict]:
    found = fetch("/Items", SearchTerm=query, IncludeItemTypes="MusicAlbum", Recursive="true", Limit="10")
    albums = rank_songs(found.get("Items", []), query, "") if not artist else [
        a for a in found.get("Items", []) if plain(artist) in plain(a.get("AlbumArtist") or "")
    ]
    if not albums:
        return []
    tracks = fetch("/Items", ParentId=albums[0]["Id"], IncludeItemTypes="Audio", Recursive="true", SortBy="ParentIndexNumber,IndexNumber")
    return tracks.get("Items", [])


def random_tracks() -> list[dict]:
    found = fetch("/Items", IncludeItemTypes="Audio", Recursive="true", SortBy="Random", Limit=str(MAX_QUEUE))
    return found.get("Items", [])


def artist_tracks(query: str) -> list[dict]:
    found = fetch("/Artists", searchTerm=query, Limit="5")
    artists = found.get("Items", [])
    if not artists:
        return []
    artists.sort(key=lambda a: plain(a.get("Name", "")) != plain(query))
    tracks = fetch("/Items", ArtistIds=artists[0]["Id"], IncludeItemTypes="Audio", Recursive="true", Limit=str(MAX_QUEUE), SortBy="Random")
    return tracks.get("Items", [])


_user_id = ""


def user_id() -> str:
    """Whose playlists and favorites to use: JELLYFIN_USER, or whoever used Jellyfin most recently."""
    global _user_id
    if not _user_id:
        users = fetch("/Users")
        wanted = os.environ.get("JELLYFIN_USER", "").strip().casefold()
        chosen = [u for u in users if u.get("Name", "").casefold() == wanted] if wanted else []
        if not chosen:
            chosen = sorted(users, key=lambda u: u.get("LastActivityDate", ""), reverse=True)
        if not chosen:
            raise RuntimeError("Jellyfin has no users.")
        _user_id = chosen[0]["Id"]
    return _user_id


def playlists() -> list[dict]:
    found = fetch("/Items", IncludeItemTypes="Playlist", Recursive="true", UserId=user_id(), Fields="ChildCount")
    return found.get("Items", [])


def playlist_tracks(query: str) -> list[dict]:
    lists = playlists()
    wanted = plain(query)
    matching = sorted((p for p in lists if wanted in plain(p.get("Name", ""))), key=lambda p: plain(p.get("Name", "")) != wanted)
    if not matching:
        return []
    found = fetch(f"/Playlists/{matching[0]['Id']}/Items", UserId=user_id(), Limit=str(MAX_QUEUE))
    return found.get("Items", [])


def favorite_tracks() -> list[dict]:
    found = fetch("/Items", IncludeItemTypes="Audio", Recursive="true", Filters="IsFavorite", UserId=user_id(), SortBy="Random", Limit=str(MAX_QUEUE))
    return found.get("Items", [])


def mix_tracks(seed_id: str) -> list[dict]:
    """Jellyfin's instant mix: songs that sound like this one, by genre and artist."""
    found = fetch(f"/Items/{seed_id}/InstantMix", UserId=user_id(), Limit="50")
    return found.get("Items", [])


def apply_level(current: float, level: str) -> float:
    """Apply "50", "+10" or "-10" to a percentage. An empty level just reports it."""
    text = level.strip().rstrip("%")
    if not text:
        return current
    if not re.fullmatch(r"[+-]?\d{1,3}(\.\d+)?", text):
        raise ValueError('Volume must look like "50", "+10" or "-10".')
    value = current + float(text) if text[0] in "+-" else float(text)
    return min(100.0, max(0.0, value))


async def system_volume(level: str) -> str:
    async def pactl(*args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "pactl", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        output, error = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(error.decode(errors="replace").strip() or "pactl failed")
        return output.decode(errors="replace")

    found = re.search(r"(\d+)%", await pactl("get-sink-volume", "@DEFAULT_SINK@"))
    if found is None:
        raise RuntimeError("Couldn't read the system volume.")
    target = apply_level(float(found.group(1)), level)
    if level.strip():
        await pactl("set-sink-volume", "@DEFAULT_SINK@", f"{target:.0f}%")
    return f"System volume is {target:.0f}%."


class Player:
    """Owns the background mpv and pauses music while Alice listens or speaks."""

    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None
        self.queue: list[dict] = []  # What was handed to mpv, in order, so its playlist position names a song.
        self.volume = 100.0
        self.ducked = False  # Alice is listening or speaking, so music stays paused until she's done.
        # Whether to resume mpv or JellyBox once she's done: only what she paused herself, or what was asked to
        # resume while she was busy.
        self.resume_mpv = False
        self.resume_external = False
        self.guard: asyncio.Task | None = None  # keep_playing, while it watches for a stray pause.

    def cancel_guard(self) -> None:
        if self.guard is not None:
            self.guard.cancel()
            self.guard = None

    async def keep_playing(self, watch_mpv: bool, watch_external: bool) -> None:
        """Undo the stray pause a Bluetooth headset sends when it leaves call mode."""
        for _ in range(RESUME_WATCH_STEPS):
            await asyncio.sleep(0.5)
            try:
                if watch_mpv and await self.command("get_property", "pause"):
                    await self.command("set_property", "pause", False)
                    return
                if watch_external and await self.external("status") == "Paused":
                    await self.external("play")
                    return
            except (OSError, RuntimeError, TimeoutError):
                return

    async def command(self, *words) -> object:
        """Send one command over mpv's JSON IPC socket and return its data. mpv also writes events to the socket,
        so lines are skipped until the reply carrying our request_id."""
        reader, writer = await asyncio.open_unix_connection(str(MPV_SOCKET))
        try:
            writer.write(json.dumps({"command": list(words), "request_id": 1}).encode() + b"\n")
            await writer.drain()
            while line := await asyncio.wait_for(reader.readline(), 5):
                reply = json.loads(line)
                if reply.get("request_id") == 1:
                    if reply.get("error") != "success":
                        raise RuntimeError(f"mpv: {reply.get('error')}")
                    return reply.get("data")
            raise RuntimeError("mpv closed the connection.")
        finally:
            writer.close()

    async def ensure_running(self) -> None:
        if self.process is not None and self.process.returncode is None:
            return
        MPV_SOCKET.unlink(missing_ok=True)
        self.process = await asyncio.create_subprocess_exec(
            "mpv", "--idle=yes", "--no-video", "--no-terminal", f"--input-ipc-server={MPV_SOCKET}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        for _ in range(50):
            if MPV_SOCKET.exists():
                break
            await asyncio.sleep(0.1)
        # Set over the socket so the key never appears in the process list.
        _, key = read_settings()
        await self.command("set_property", "http-header-fields", [auth_header(key)])
        await self.command("set_property", "volume", self.volume)

    async def is_active(self) -> bool:
        if self.process is None or self.process.returncode is not None or not self.queue:
            return False
        try:
            return (await self.command("get_property", "playlist-pos")) not in (None, -1)
        except (OSError, RuntimeError, TimeoutError):
            return False

    async def external(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            "playerctl", "-p", EXTERNAL_PLAYER, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        output, _ = await process.communicate()
        return output.decode(errors="replace").strip() if process.returncode == 0 else ""

    async def play(self, tracks: list[dict]) -> None:
        self.cancel_guard()
        url, _ = read_settings()
        await self.ensure_running()
        if await self.external("status") == "Playing":
            await self.external("pause")
        self.resume_external = False
        self.queue = [describe(track) for track in tracks[:MAX_QUEUE]]
        # Asked for while she's still speaking: load it paused, and let unduck start it when she's done.
        await self.command("set_property", "pause", self.ducked)
        for index, track in enumerate(self.queue):
            mode = "replace" if index == 0 else "append"
            await self.command("loadfile", f"{url}/Audio/{track['id']}/stream?static=true", mode)
        self.resume_mpv = self.ducked

    async def add(self, tracks: list[dict]) -> str:
        """Add songs after the current queue, or start them if nothing of Alice's is playing."""
        if not await self.is_active():
            await self.play(tracks)
            return "started"
        url, _ = read_settings()
        room = MAX_QUEUE - len(self.queue)
        for track in tracks[:max(room, 0)]:
            self.queue.append(describe(track))
            await self.command("loadfile", f"{url}/Audio/{track['Id']}/stream?static=true", "append")
        return "queued"

    async def current_title(self) -> tuple[str, str, str]:
        """(Jellyfin id if known, title, artist) of whatever is playing, here or in JellyBox."""
        if await self.is_active():
            index = await self.command("get_property", "playlist-pos")
            if isinstance(index, int) and index < len(self.queue):
                track = self.queue[index]
                return track["id"], track.get("title") or "", track.get("artist") or ""
        if await self.external("status"):
            title = await self.external("metadata", "--format", "{{title}}")
            artist = await self.external("metadata", "--format", "{{artist}}")
            return "", title, artist
        return "", "", ""

    async def control(self, action: str) -> str:
        self.cancel_guard()
        if action in ("pause", "stop"):
            self.resume_mpv = self.resume_external = False
        if await self.is_active():
            if action == "pause":
                await self.command("set_property", "pause", True)
            elif action == "resume":
                self.resume_mpv = self.ducked
                await self.command("set_property", "pause", self.ducked)
            elif action == "next":
                await self.command("playlist-next", "force")
            elif action == "previous":
                await self.command("playlist-prev", "force")
            elif action == "stop":
                await self.command("stop")
                self.queue = []
            else:
                raise ValueError(f"Unknown action: {action}")
            return f"Done: {action}."
        external = {"pause": "pause", "resume": "play", "next": "next", "previous": "previous", "stop": "pause"}
        if action not in external:
            raise ValueError(f"Unknown action: {action}")
        if not await self.external("status"):
            return "Nothing is playing."
        if self.ducked and action == "resume":
            self.resume_external = True
        else:
            await self.external(external[action])
        return f"Done: {action} in JellyBox."

    async def music_volume(self, level: str) -> str:
        if await self.is_active():
            current = float(await self.command("get_property", "volume"))
            self.volume = apply_level(current, level)
            await self.command("set_property", "volume", self.volume)
            return f"Music volume is {self.volume:.0f}%."
        if await self.external("status"):
            reported = await self.external("volume")
            if not reported:
                return "JellyBox doesn't allow its volume to be changed from here."
            target = apply_level(float(reported) * 100, level)
            if level:
                await self.external("volume", f"{target / 100:.2f}")
            return f"JellyBox volume is {target:.0f}%."
        self.volume = apply_level(self.volume, level)
        return f"Nothing is playing. Music will start at {self.volume:.0f}%."

    async def now_playing(self) -> str:
        if await self.is_active():
            index = await self.command("get_property", "playlist-pos")
            paused = await self.command("get_property", "pause")
            track = self.queue[index] if isinstance(index, int) and index < len(self.queue) else {}
            state = "paused" if paused and not self.resume_mpv else "playing"
            return json.dumps({"state": state, "title": track.get("title"), "artist": track.get("artist"), "album": track.get("album")}, ensure_ascii=False)
        status = await self.external("status")
        if status:
            info = await self.external("metadata", "--format", "{{title}} | {{artist}} | {{album}}")
            return f"JellyBox: {status}. {info}"
        return "Nothing is playing."

    async def duck(self) -> None:
        """Pause whatever plays while she listens or speaks, remembering what to resume."""
        if self.ducked:
            return
        self.cancel_guard()
        self.ducked = True
        try:
            if await self.is_active() and not await self.command("get_property", "pause"):
                await self.command("set_property", "pause", True)
                self.resume_mpv = True
            if await self.external("status") == "Playing":
                await self.external("pause")
                self.resume_external = True
        except (OSError, RuntimeError, TimeoutError) as error:
            print(f"couldn't pause music: {error}", flush=True)

    async def unduck(self) -> None:
        """Resume what duck paused, then guard it for a few seconds against a headset's stray pause."""
        self.ducked = False
        resume_mpv, resume_external = self.resume_mpv, self.resume_external
        self.resume_mpv = self.resume_external = False
        try:
            if resume_mpv and self.queue:
                await self.command("set_property", "pause", False)
            if resume_external:
                await self.external("play")
            if resume_mpv or resume_external:
                self.guard = asyncio.create_task(self.keep_playing(resume_mpv, resume_external))
        except (OSError, RuntimeError, TimeoutError) as error:
            print(f"couldn't resume music: {error}", flush=True)


player = Player()


def search_library(query: str) -> dict:
    """Search titles, artists and albums, because Jellyfin only matches an item's own name."""
    songs = search_songs(query)[:MAX_SEARCH_RESULTS]
    artists = fetch("/Artists", searchTerm=query, Limit="5").get("Items", [])
    albums = fetch("/Items", SearchTerm=query, IncludeItemTypes="MusicAlbum", Recursive="true", Limit="5").get("Items", [])
    sample = artist_tracks(query)[:MAX_SEARCH_RESULTS] if artists else []
    return {
        "songs_with_this_title": [describe(item) for item in songs],
        "artists": [artist.get("Name") for artist in artists],
        "songs_by_artist": [describe(item) for item in sample],
        "albums": [{"title": album.get("Name"), "artist": album.get("AlbumArtist")} for album in albums],
    }


async def find_music(query: str) -> str:
    return json.dumps(await asyncio.to_thread(search_library, query), ensure_ascii=False)


async def play_music(query: str = "", kind: str = "song", artist: str = "") -> str:
    query, artist = query.strip(), artist.strip()
    if plain(query) in GENERIC_QUERIES:
        query = ""
    # "A random song by X" arrives in several shapes, and all of them mean shuffling X.
    if artist and (kind == "random" or not query):
        kind, query, artist = "artist", artist, ""
    elif kind == "random" and query:
        kind = "artist"
    if kind not in ("random", "favorites") and not query:
        raise ValueError("Say what to play, or use kind random.")
    if kind == "favorites":
        tracks = await asyncio.to_thread(favorite_tracks)
        if not tracks:
            return "There are no favorite songs in Jellyfin yet. Songs can be marked with the heart in Jellyfin or JellyBox."
        await player.play(tracks)
        first = describe(tracks[0])
        return f"Playing {len(tracks)} favorite song(s) shuffled, starting with {first['title']} by {first['artist']}."
    if kind == "playlist":
        tracks = await asyncio.to_thread(playlist_tracks, query)
        if not tracks:
            names = [p.get("Name") for p in await asyncio.to_thread(playlists)]
            return f"No playlist matches {query!r}. Playlists: {', '.join(names) or 'none yet'}."
    elif kind == "random":
        tracks = await asyncio.to_thread(random_tracks)
    elif kind == "album":
        tracks = await asyncio.to_thread(album_tracks, query, artist)
    elif kind == "artist":
        tracks = await asyncio.to_thread(artist_tracks, query)
    elif kind == "song":
        tracks = (await asyncio.to_thread(search_songs, query, artist))[:1]
        # The model sometimes files an artist or album name under "song".
        if not tracks and not artist:
            tracks = await asyncio.to_thread(artist_tracks, query)
        if not tracks and not artist:
            tracks = await asyncio.to_thread(album_tracks, query, "")
    else:
        raise ValueError("kind must be song, album, artist, playlist, favorites or random.")
    if not tracks:
        return f"No {kind} matching {query!r} is in the library."
    await player.play(tracks)
    first = describe(tracks[0])
    return f"Playing {len(tracks)} track(s), starting with {first['title']} by {first['artist']}."


async def music_control(action: str) -> str:
    return await player.control(action)


async def set_volume(level: str = "", target: str = "music") -> str:
    if target == "system":
        return await system_volume(level)
    if target == "music":
        return await player.music_volume(level)
    raise ValueError("target must be music or system.")


async def now_playing() -> str:
    return await player.now_playing()


async def playlist_names() -> str:
    found = await asyncio.to_thread(playlists)
    if not found:
        return "There are no playlists in Jellyfin yet."
    return "Playlists: " + "; ".join(f"{p.get('Name')} ({p.get('ChildCount', '?')} songs)" for p in found)


async def queue_music(query: str, kind: str = "song", artist: str = "") -> str:
    """Add a song, album or artist to the end of what's playing."""
    query, artist = query.strip(), artist.strip()
    if not query and artist:
        kind, query = "artist", artist
    if not query:
        raise ValueError("Say what to add to the queue.")
    if kind == "album":
        tracks = await asyncio.to_thread(album_tracks, query, artist)
    elif kind == "artist":
        tracks = (await asyncio.to_thread(artist_tracks, query))[:10]
    else:
        tracks = (await asyncio.to_thread(search_songs, query, artist))[:1]
    if not tracks:
        return f"No {kind} matching {query!r} is in the library."
    if await player.external("status") == "Playing" and not await player.is_active():
        return "JellyBox is playing, and its queue can't be changed from here. I can play it instead, which stops JellyBox."
    done = await player.add(tracks)
    first = describe(tracks[0])
    what = f"{first['title']} by {first['artist']}" if len(tracks) == 1 else f"{len(tracks)} songs, starting with {first['title']}"
    return f"Nothing was playing, so I started {what}." if done == "started" else f"Added {what} to the queue."


async def play_similar(query: str = "") -> str:
    """An instant mix from a named song, or from whatever is playing."""
    seed_id, title, artist = "", query.strip(), ""
    if not title:
        seed_id, title, artist = await player.current_title()
        if not title:
            return "Nothing is playing, so say a song to base the mix on."
    if not seed_id:
        found = await asyncio.to_thread(search_songs, title, artist)
        if not found:
            return f"{title!r} isn't in the library, so I can't make a mix from it."
        seed_id = found[0]["Id"]
        title = found[0].get("Name", title)
    tracks = await asyncio.to_thread(mix_tracks, seed_id)
    if len(tracks) < 2:
        return f"Jellyfin couldn't find songs like {title}."
    await player.play(tracks)
    return f"Playing a mix of {len(tracks)} songs like {title}, starting with {describe(tracks[0])['title']} by {describe(tracks[0])['artist']}."
