## Layout

| Folder | What's in it |
|---|---|
| `backend/` | The Python backend (`alice/`): the model loop, tools, voice, the phone listener, and the evals (`evals/`) |
| `shell/` | The Quickshell (QML) floating book and avatar, and the shader that animates her (`shaders/rig.frag`) |
| `android/` | The phone companion (Kotlin and Compose); see [[Phone Companion]] |
| `tools/` | Scripts that paint her art and prepare the phone voice; see [[Artwork]] |
| `install/`, `install.sh` | The systemd user services and the installer |
| `wiki/` | These pages |

## Evals

Prompt wording is touchy, so four evals check her model's behaviour without running any tool for real. Run all four after changing a prompt or a tool:

```sh
cd backend
uv run python -m evals.routing    # which tool she picks, and with what arguments
uv run python -m evals.mood       # which mood tag she opens with
uv run python -m evals.conduct    # restraint: no claims before results, no tools nobody asked for
uv run python -m evals.chains     # whole multi-step requests against pretend tools
uv run python -m evals.routing 1 steam   # 1 run of only the cases that mention Steam
```

The evals test her usual self. To test an outfit's personality instead, add `ALICE_EVAL_OUTFIT=queen` or `ALICE_EVAL_OUTFIT=evil`; Evil Alice is expected to pout where sweet Alice beams, so her mood score is lower. To compare another model, add `ALICE_MODEL=<ollama tag>`.

The phone's own prompt has its own eval, run against a model of the same size in Ollama:

```sh
uv run python -m evals.offline gemma4:e4b
ALICE_EVAL_PROMPT=light uv run python -m evals.offline gemma4:e2b   # her light brain
```

## Phone app tests

```sh
cd android
./gradlew testDebugUnitTest
```

## The shader

After editing `shell/shaders/rig.frag`, rebuild it:

```sh
/usr/lib/qt6/bin/qsb --qt6 -o shell/shaders/rig.frag.qsb shell/shaders/rig.frag
```

## Trying the shell without touching the real one

Copy `shell/` somewhere, change the socket name in the copy's `shell.qml` (`alice.sock`), and run it with `qs -p <copy>`. Drive it with `qs -p <copy> ipc call alice setState listening`, and send backend events (`{"type": "mood", "mood": "happy"}`, `{"type": "outfit", "outfit": "queen"}`) from a small script listening on that socket.

## Releases

Every push builds the backend checks and the phone APK (the **Build** workflow; the APK is attached to the run). Pushing a version tag such as `v0.2.0` publishes a release with the source and the APK:

```sh
git tag v0.2.0 && git push origin v0.2.0
```

Release APKs are signed with the key in two repository secrets, so each one installs over the last: `ALICE_KEYSTORE_BASE64` (the `.jks` file, base64-encoded) and `ALICE_KEYSTORE_PASSWORD`. Without them the APK is signed with a throwaway debug key. To sign a local build the same way, set `ALICE_KEYSTORE` (the path to the `.jks`) and `ALICE_KEYSTORE_PASSWORD` in the environment or in `~/.gradle/gradle.properties`.
