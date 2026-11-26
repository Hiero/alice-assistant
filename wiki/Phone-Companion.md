An Android companion for Alice (this page is also `android/README.md` in the repository). With your PC in reach she is the same Alice as on the desktop: the app talks to the backend over Tailscale. Without it, a small model on the phone (Gemma 4 E4B) keeps her company and can open apps and start web searches.

An **Auto · PC · Phone** switch picks the side. Auto uses the PC whenever it answers.

## Build and install

Gradle 8.11 can't run on very new JDKs, so build with JDK 17 or 21:

```sh
cd android
JAVA_HOME=/path/to/jdk-21 ./gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Her faces and the launcher icon are copied from `shell/assets` at build time. Without them the app still builds, with a placeholder.

## Reach the PC

The backend listens on the PC's Tailscale address, port 7720, and creates a token in `~/.config/alice/phone-token`. Hand the token to the app over adb, so it never has to be typed:

```sh
adb shell am start -n io.github.hiero.alice/.MainActivity --es token "$(cat ~/.config/alice/phone-token)"
```

Add `--es host <address>` with the PC's Tailscale address (`tailscale ip -4` on the PC), or type it in the app's settings. To build it into your own copies of the app instead, put `aliceDefaultHost=<address>` in `~/.gradle/gradle.properties`. If Tailscale on the phone only lets some apps through, add Alice to that list.

## Her phone model

The model is too big for the APK, so it's copied into the app's own storage once (3.7 GB):

```sh
curl -L -o gemma-4-E4B-it.litertlm \
  https://huggingface.co/litert-community/gemma-4-E4B-it-litert-lm/resolve/main/gemma-4-E4B-it.litertlm
adb push gemma-4-E4B-it.litertlm /data/local/tmp/alice-phone.litertlm
adb shell run-as io.github.hiero.alice cp /data/local/tmp/alice-phone.litertlm files/
adb shell rm /data/local/tmp/alice-phone.litertlm
```

Reinstalling the app keeps it; uninstalling deletes it.

### Her light brain

A second, smaller model (Gemma 4 E2B, 2.6 GB) spares the phone when it's warm or its battery is low. With it she only chats: her prompt (`offline-prompt-light.txt`) teaches no actions, the app ignores any it writes, and only the plain commands the app matches itself are done ("open X", "search for X", timers, alarms, settings, music, weather, money, Steam, news, notes and "remember that…"). Asked "what can you do?", the app answers with the list for the brain in use. Setup has Auto (light when warm or low on battery), Full and Light. Install it the same way, as `files/alice-phone-light.litertlm`, from `litert-community/gemma-4-E2B-it-litert-lm`. Test its prompt with `ALICE_EVAL_PROMPT=light uv run python -m evals.offline gemma4:e2b`.

## What she can do without the PC

Her phone model answers with an action tag when there's something to do, and the app does it:

- open apps (a kind of app, like music or maps, opens the phone's default one) and web searches
- the weather (Open-Meteo), exchange rates (the ECB's, via Frankfurter), news (Google News) and Steam (a game's price, sale and release, or the wishlist games on sale), fetched with the phone's own internet; she then says what they mean, with news and Steam results as links under her reply. The PC tells the phone the Steam account and region and the news edition; no API keys leave the PC
- alarms and timers in the Clock app, notes and "remember that…" (added to her PC notes and memory on the next sync)
- the flashlight, vibrate/silent/sound, volume, music keys; Do Not Disturb and brightness after you allow them once. Android doesn't let apps switch Wi-Fi, mobile data, Bluetooth or airplane mode, so for those she opens the switch for you
- holidays, birthdays and her outfit for the day, from what the PC last told her

## Talking to her

With the text box empty, the button becomes 🎤. It listens until you go quiet (or you tap it again), with the same rules as the desktop's talk key. With the PC in reach the recording goes to the PC's Whisper, which tells English from Portuguese; without it, Android's on-device recogniser is used, allowed to pick English or Brazilian Portuguese (its offline language packs must be installed).

## Her voice

With 🔊 on and the PC in reach, the PC makes her voice (Kokoro, as on the desktop) and streams it to the phone, with lip sync.

Offline, the phone can make it itself with Kokoro through [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) (the library is fetched into `app/libs` on the first build). It's the same voice, but on a Fold6 it makes speech at about real time and adds roughly 1 GB to the memory her model takes, so it's off unless you switch on "Voice when offline" in Setup. To install it (360 MB):

```sh
curl -L https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-multi-lang-v1_0.tar.bz2 | tar -xj
uv run --project backend python tools/make_phone_voice.py kokoro-multi-lang-v1_0
D=/data/local/tmp/kokoro; K=kokoro-multi-lang-v1_0
adb shell mkdir -p $D
adb push $K/model.onnx $K/tokens.txt $K/lexicon-us-en.txt $K/espeak-ng-data $D/
adb push $K/voices-alice.bin $D/voices.bin
adb shell run-as io.github.hiero.alice cp -r $D files/ && adb shell rm -rf $D
```

Her phone prompt is `app/src/main/assets/offline-prompt.txt`. Test changes on the PC before installing them, with a model of the same size in Ollama:

```sh
cd backend
uv run python -m evals.offline gemma4:e4b
```

Plain "open X" and "search for X" requests never reach the model; `PhoneAlice.kt` matches those itself (`./gradlew testDebugUnitTest`).
