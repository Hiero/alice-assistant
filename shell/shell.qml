import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import QtQuick
import QtQuick.Shapes

// Floating Alice surface. The surface covers the whole output and never moves;
// the artwork moves inside it. Moving the surface itself during a drag creates a
// feedback loop, because pointer events keep arriving relative to its old position.
// Only hitArea takes input, so everything else is click-through.
// States: dormant, listening, thinking, speaking.
// Drive it with: qs -p shell ipc call alice setState thinking
PanelWindow {
    id: win

    property string state: "dormant"
    readonly property bool awake: state !== "dormant"
    readonly property var validStates: ["dormant", "listening", "thinking", "speaking"]
    readonly property string outputName: Quickshell.env("ALICE_OUTPUT") ?? ""

    readonly property int spineWidth: 38
    readonly property int spineHeight: 196
    readonly property int closedWidth: saved.sideways ? spineHeight : spineWidth
    readonly property int closedHeight: saved.sideways ? spineWidth : spineHeight
    readonly property int stageWidth: 340
    readonly property int stageHeight: 450

    // Stays true until the closing animation ends, so she isn't shoved mid-animation.
    property bool expanded: false
    onAwakeChanged: {
        if (awake) {
            shrinkTimer.stop();
            expanded = true;
            if (backend.connected)
                sendEvent({ type: "opened", mode: openingForTalk ? "talk" : "click" });
            openingForTalk = false;
        } else {
            answerConfirm(false);
            speech = [];
            setMood("neutral");
            if (backend.connected)
                sendEvent({ type: "stop_speaking" });
            promptOpen = false;
            replyText = "";
            shrinkTimer.restart();
        }
    }

    Timer {
        id: shrinkTimer
        interval: 750
        onTriggered: win.expanded = false
    }

    function clamp(value, min, max) {
        return Math.round(Math.max(min, Math.min(value, max)));
    }

    function moveOpenTo(x, y) {
        saved.openX = clamp(x, 0, root.width - stageWidth);
        saved.openY = clamp(y, 0, root.height - stageHeight);
        saved.hasOpenSpot = true;
    }

    function toggleSideways() {
        if (awake)
            return;
        saved.sideways = !saved.sideways;
        moveTo(saved.right, saved.bottom);
        positionFile.writeAdapter();
    }

    // saved holds the spine's distance from the right and bottom edges.
    function moveTo(right, bottom) {
        const width = closedWidth;
        const height = closedHeight;
        const inset = (width - spineWidth) / 2;
        saved.right = clamp(right, inset, root.width - width + inset);
        saved.bottom = clamp(bottom, 0, root.height - height);
    }

    FileView {
        id: positionFile
        path: Quickshell.statePath("position.json")
        blockLoading: true

        JsonAdapter {
            id: saved
            property real right: 32
            property real bottom: 64
            property bool sideways: false
            // The open book has its own spot, used once it has been dragged.
            property bool hasOpenSpot: false
            property real openX: 0
            property real openY: 0
            // The typing box above her replies instead of below them.
            property bool promptOnTop: false
        }
    }

    screen: Quickshell.screens.find(s => s.name === outputName) ?? Quickshell.screens[0]
    anchors { left: true; right: true; top: true; bottom: true }
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "alice"
    WlrLayershell.layer: WlrLayer.Top
    WlrLayershell.keyboardFocus: promptOpen ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
    mask: Region {
        item: hitArea
        Region { item: bubble }
        Region { item: promptBox }
        Region { item: confirmCard }
    }

    // The face laid over her neutral one. Speaking keeps the neutral face: the mouth moves on its own.
    readonly property string face: state === "thinking" ? "thinking" : state === "listening" ? "listening" : "neutral"

    // Her motion, in the rig shader's units (radians and pixels), eased toward targets every frame.
    property real headAngle: 0
    property real headTurn: 0
    property real headNod: 0
    property real breath: 0
    property real hairSway: 0
    property real blink: 0
    property real mouthLevel: 0
    // Occasional idle gestures: a curious tilt, or a glance to one side.
    property real gestureAngle: 0
    property real gestureTurn: 0

    property real clock: 0
    property real breathPhase: 0
    property real hairSwing: 0
    property real hairVelocity: 0
    property real lastStageX: 0

    // Lip sync. The backend sends each spoken sentence's loudness and when it starts playing.
    // Each entry is {start (ms since the epoch), fps, levels}.
    property var speech: []
    // The backend sends mouth events; until then she flaps along while speaking.
    property bool lipSync: false

    // Her mood, from the tag she opens a reply with. Each mood adds a pose, overlays from
    // tools/make_faces.py and an emote on top of her usual motion. Angles and turns are toward
    // the chat column; negative ones look away. lidsFor and the emote's 3 seconds count from
    // when the mood arrived.
    readonly property var moodStyles: ({
        happy: { hop: 3, wiggle: 1, happyEyes: true, blush: 0.35, emote: "✨", emoteX: 22, emoteY: 6 },
        excited: { hop: 6, wiggle: 1.6, happyEyes: true, blush: 0.5, breath: 2.4, emote: "✨", emoteX: 22, emoteY: 6 },
        surprised: { hop: 5, nod: -1.5, emote: "❗" },
        curious: { angle: 0.07, turn: 1.2, emote: "❓" },
        worried: { angle: -0.03, nod: 1.2, breath: 3.2, emote: "💦", emoteX: 202, emoteY: 80 },
        sad: { angle: -0.02, nod: 2.6, lids: 1, breath: 5.5 },
        pout: { angle: -0.05, turn: -3.2, blush: 0.45, emote: "💢" },
        embarrassed: { nod: 1.6, turn: -2, blush: 1, lids: 0.8, lidsFor: 2.5, emote: "💦", emoteX: 202, emoteY: 80 },
        sleepy: { angle: 0.05, nod: 2, lids: 1, breath: 6, emote: "💤", emoteStays: true }
    })
    property string mood: "neutral"
    readonly property var moodStyle: moodStyles[mood] ?? ({})
    property real moodAge: 0
    // Moods with a painted face from tools/make_expressions.py. Those replace the drawn lids and
    // blush; without the files she keeps the overlays from tools/make_faces.py.
    property var moodArt: ({})
    readonly property bool hasMoodArt: !!moodArt[mood]
    // 1 while her mouth is moving, when a painted face gives its mouth up to the lip sync.
    property real talking: 0
    property real happyEyes: 0
    property real halfLids: 0
    property real blushLevel: 0
    property real hop: 0
    property real emoteScale: 0
    property real emoteOpacity: 0
    property real emoteFloat: 0

    function setMood(next) {
        if (next === mood)
            return;
        mood = next;
        moodAge = 0;
    }

    // After a reply she settles back to calm, unless she's asked something new first.
    Timer {
        id: moodRest
        interval: 30000
        onTriggered: if (!win.busy) win.setMood("neutral")
    }

    onStateChanged: if (state === "listening") blinkSoon()

    // Moves value part of the way to target: an exponential approach, so it looks the same at any frame rate.
    // A higher rate gets there faster; about 1/rate seconds closes two thirds of the gap.
    function ease(value, target, rate, dt) {
        return value + (target - value) * (1 - Math.exp(-rate * dt));
    }

    // How open her mouth should be right now: the loudness of the sentence playing, looked up by time.
    // Sentences that have finished are dropped; before the first one starts, she's quiet.
    function speechLevel() {
        const now = Date.now();
        while (speech.length && now > speech[0].start + speech[0].levels.length * 1000 / speech[0].fps)
            speech.shift();
        if (!speech.length || now < speech[0].start)
            return 0;
        return speech[0].levels[Math.floor((now - speech[0].start) * speech[0].fps / 1000)] ?? 0;
    }

    function animate(dt) {
        clock += dt;
        const t = clock;
        // Toward the chat column, so she faces whoever she's talking to.
        const side = root.chatOnLeft ? -1 : 1;

        let mouth = 0;
        if (lipSync)
            mouth = speechLevel();
        else if (state === "speaking")
            mouth = 0.55 + 0.45 * Math.sin(t * 13) * Math.sin(t * 5.3);
        mouthLevel = ease(mouthLevel, mouth, mouth > mouthLevel ? 30 : 14, dt);

        // Never quite still: slow, out-of-step drifts, plus whatever gesture is under way.
        let angle = 0.018 * Math.sin(t * 0.53) + 0.012 * Math.sin(t * 1.27 + 1.3) + gestureAngle;
        let turn = 1.0 * Math.sin(t * 0.31 + 2.0) + gestureTurn;
        let nod = 0.4 * Math.sin(t * 0.71);
        if (state === "listening") {
            angle += 0.045 * side;
            turn += 2.2 * side;
            nod += 0.8;
        } else if (state === "thinking") {
            angle -= 0.06 * side;
            turn -= 2.5 * side;
            nod -= 1.2;
        } else if (state === "speaking") {
            angle += 0.012 * Math.sin(t * 2.3);
            turn += 1.4 * side;
            nod += 1.6 * mouthLevel;
        }

        // Her mood leans on top of all that, most strongly just after it arrives.
        moodAge += dt;
        const style = moodStyle;
        angle += (style.angle ?? 0) * side + (style.wiggle ?? 0) * 0.03 * Math.sin(t * 6) * Math.exp(-moodAge * 1.5);
        turn += (style.turn ?? 0) * side;
        nod += style.nod ?? 0;
        hop = (style.hop ?? 0) * Math.abs(Math.sin(moodAge * 9)) * Math.exp(-moodAge * 2.2);
        // A painted face has its own eyes and blush. Only happy keeps the smiling ^^ eyes over it.
        const drawn = !hasMoodArt;
        happyEyes = ease(happyEyes, style.happyEyes && moodAge < 1.8 && (drawn || mood === "happy") ? 1 : 0, 14, dt);
        const lidsNow = style.lidsFor === undefined || moodAge < style.lidsFor;
        halfLids = ease(halfLids, drawn && lidsNow ? (style.lids ?? 0) : 0, 6, dt);
        blushLevel = ease(blushLevel, drawn ? (style.blush ?? 0) : 0, 3, dt);
        talking = ease(talking, state === "speaking" || mouthLevel > 0.03 ? 1 : 0, 10, dt);
        // Emotes pop in with a little overshoot, bob, and fade after 3 seconds unless they stay.
        emoteScale = Math.min(1, moodAge / 0.18) * (1 + 0.25 * Math.exp(-moodAge * 8) * Math.sin(moodAge * 30));
        emoteOpacity = style.emote ? Math.max(0, Math.min(1, ((style.emoteStays ? 1e9 : 3) - moodAge) / 0.4)) : 0;
        emoteFloat = 2 * Math.sin(t * 2.2);
        const previousAngle = headAngle;
        headAngle = ease(headAngle, angle, 3.5, dt);
        headTurn = ease(headTurn, turn, 3.5, dt);
        headNod = ease(headNod, nod, 5, dt);

        breathPhase += dt * 2 * Math.PI / (style.breath ?? (state === "speaking" ? 3.0 : 4.2));
        breath = 0.9 * (0.5 - 0.5 * Math.cos(breathPhase));

        // Hair is a spring: it trails behind her head and the book when they move, then settles.
        const moved = stage.x - lastStageX;
        lastStageX = stage.x;
        hairVelocity += (-60 * hairSwing - 6 * hairVelocity) * dt - (headAngle - previousAngle) * 400 - moved * 0.25;
        hairSwing = Math.max(-4, Math.min(4, hairSwing + hairVelocity * dt));
        hairSway = hairSwing + 0.5 * Math.sin(t * 0.83);
    }

    FrameAnimation {
        running: win.expanded
        onTriggered: win.animate(Math.min(frameTime, 0.1))
    }

    // A blink shortly after she starts listening, as if looking up at you.
    function blinkSoon() {
        blinkTimer.interval = 300;
        blinkTimer.restart();
    }

    // Blinks at random intervals of 2 to 6.5 seconds, sometimes twice in a row.
    Timer {
        id: blinkTimer
        interval: 2500
        running: win.expanded
        repeat: true
        onTriggered: {
            interval = 2200 + Math.random() * 4300;
            blinkAnimation.loops = Math.random() < 0.15 ? 2 : 1;
            blinkAnimation.restart();
        }
    }

    SequentialAnimation {
        id: blinkAnimation
        NumberAnimation { target: win; property: "blink"; to: 1; duration: 60; easing.type: Easing.InQuad }
        PauseAnimation { duration: 50 }
        NumberAnimation { target: win; property: "blink"; to: 0; duration: 100; easing.type: Easing.OutQuad }
    }

    // Every 5 to 14 seconds an idle gesture: a tilt of the head, a glance to one side, or (a quarter of the time)
    // nothing. It lasts 1.5 to 3.5 seconds; animate() eases her into it and back.
    Timer {
        interval: 8000
        running: win.expanded
        repeat: true
        onTriggered: {
            interval = 5000 + Math.random() * 9000;
            const way = Math.random() < 0.5 ? -1 : 1;
            const pick = Math.random();
            win.gestureAngle = pick < 0.4 ? way * (0.03 + Math.random() * 0.04) : 0;
            win.gestureTurn = pick >= 0.4 && pick < 0.75 ? way * (1.5 + Math.random() * 1.5) : 0;
            gestureEnd.interval = 1500 + Math.random() * 2000;
            gestureEnd.restart();
        }
    }

    Timer {
        id: gestureEnd
        onTriggered: {
            win.gestureAngle = 0;
            win.gestureTurn = 0;
        }
    }

    property var pendingConfirm: null
    property bool recording: false
    property bool replaceOnDelta: false

    function sendEvent(event) {
        backend.write(JSON.stringify(event) + "\n");
        backend.flush();
    }

    property bool openingForTalk: false
    property bool partyHat: false
    // A holiday or wardrobe outfit folder under assets/outfits, or "" for her usual dress.
    property string outfit: ""
    // Whether that outfit brings its own face overlays (Evil Alice's red eyes), or shares the usual ones.
    property bool outfitFaces: false
    readonly property string faceDir: outfit && outfitFaces ? "assets/outfits/" + outfit + "/" : "assets/"
    // Room above her head inside the clip, for tall outfits like the witch hat.
    readonly property int hatRoom: 70

    function talk() {
        speech = [];
        setMood("neutral");
        if (!awake) {
            openingForTalk = true;
            state = "listening";
        }
        if (!backend.connected)
            replyText = "The backend isn't running.";
        else
            sendEvent({ type: "talk" });
    }

    function answerConfirm(approved) {
        if (!pendingConfirm)
            return false;
        sendEvent({ type: "confirm_reply", id: pendingConfirm.id, approved: approved });
        pendingConfirm = null;
        return true;
    }

    property bool promptOpen: false
    property bool busy: false
    property string replyText: ""
    // Links a tool sent along with the reply, like Steam releases: {title, items: [{name, url, price}]}.
    property var linkList: null
    // A week's forecast from the weather tool: {place, now, days: [{label, icon, high, low, rain}]}.
    property var weatherCard: null
    // Names from the latest list stay linkable in later replies, even once the list is hidden.
    property var linkUrls: ({})
    property var linkPattern: null

    function escapeHtml(text) {
        return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    function setLinks(items) {
        const urls = {};
        for (const item of items)
            if (item.name.length >= 3 && /^(https:\/\/(store\.steampowered\.com|news\.google\.com)\/|file:\/\/\/home\/)/.test(item.url))
                urls[escapeHtml(item.name)] = item.url;
        // Longest first, so "Icy Tower 2" wins over "Icy Tower".
        const names = Object.keys(urls).sort((a, b) => b.length - a.length);
        linkUrls = urls;
        linkPattern = names.length
            ? new RegExp("(^|[^\\w])(" + names.map(n => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")(?![\\w])", "g")
            : null;
    }

    // Her replies as safe styled text: light Markdown, plus links on game names she mentions.
    function formatReply(text) {
        let html = escapeHtml(text)
            .replace(/^#{1,6}\s+/gm, "")
            .replace(/^\s*[-*]\s+/gm, "• ")
            .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
            .replace(/__(.+?)__/g, "<b>$1</b>")
            .replace(/(^|[^*\w])\*(?!\s)([^*\n]+?)\*(?!\w)/g, "$1<i>$2</i>")
            .replace(/`([^`\n]+)`/g, "$1");
        if (linkPattern)
            html = html.replace(linkPattern, (match, before, name) => before + '<a href="' + linkUrls[name] + '">' + name + "</a>");
        return html.replace(/\n/g, "<br>");
    }

    function openPrompt() {
        if (!awake)
            state = "listening";
        promptOpen = true;
        promptInput.forceActiveFocus();
    }

    function sendMessage(text) {
        const trimmed = text.trim();
        if (!trimmed || busy)
            return false;
        if (!awake)
            state = "listening";
        if (!backend.connected) {
            replyText = "The backend isn't running.";
            return false;
        }
        busy = true;
        replyText = "";
        linkList = null;
        weatherCard = null;
        speech = [];
        setMood("neutral");
        sendEvent({ type: "user_message", text: trimmed });
        return true;
    }

    function handleEvent(line) {
        const event = JSON.parse(line);
        if (event.type === "state" && awake)
            state = event.state;
        else if (event.type === "delta") {
            if (replaceOnDelta) {
                replaceOnDelta = false;
                replyText = "";
            }
            replyText += event.text;
        } else if (event.type === "recording") {
            recording = event.active;
            if (recording) {
                busy = true;
                linkList = null;
                weatherCard = null;
                replyText = "Listening…";
            }
        } else if (event.type === "transcript") {
            replyText = "“" + event.text + "”";
            replaceOnDelta = true;
        } else if (event.type === "status") {
            replyText = event.text;
            replaceOnDelta = true;
        } else if (event.type === "quote") {
            if (!busy) {
                replyText = event.text;
                // Cards and lists belong to the answer they came with, not to the day's passage.
                linkList = null;
                weatherCard = null;
            }
        } else if (event.type === "mood") {
            setMood(event.mood);
        } else if (event.type === "mouth") {
            lipSync = true;
            speech.push({ start: Date.now() + event.delay * 1000, fps: event.fps, levels: event.levels });
        } else if (event.type === "games" || event.type === "links") {
            linkList = event;
            setLinks(event.items);
        } else if (event.type === "weather") {
            weatherCard = event;
        } else if (event.type === "occasion") {
            partyHat = event.hat;
            outfit = event.outfit ?? "";
            outfitFaces = event.faces ?? false;
        } else if (event.type === "outfit") {
            outfit = event.outfit ?? "";
            outfitFaces = event.faces ?? false;
        } else if (event.type === "notice") {
            busy = false;
            replyText = event.message;
        }
        else if (event.type === "confirm") {
            pendingConfirm = event;
            if (!awake)
                answerConfirm(false);
        } else if (event.type === "confirm_expired")
            pendingConfirm = null;
        else if (event.type === "done") {
            busy = false;
            moodRest.restart();
        }
        else if (event.type === "error") {
            busy = false;
            replyText = "Something went wrong: " + event.message;
        }
    }

    Socket {
        id: backend
        path: Quickshell.env("XDG_RUNTIME_DIR") + "/alice.sock"
        connected: true
        onConnectedChanged: if (!connected) {
            win.busy = false;
            win.recording = false;
            win.pendingConfirm = null;
        }
        parser: SplitParser { onRead: line => win.handleEvent(line) }
    }

    Timer {
        interval: 1000
        repeat: true
        running: !backend.connected
        onTriggered: backend.connected = true
    }

    IpcHandler {
        target: "alice"

        function setState(next: string): string {
            if (!win.validStates.includes(next))
                return "unknown state: " + next;
            win.state = next;
            return next;
        }

        function rotate(): void {
            win.toggleSideways();
        }

        // Moves the typing box above or below her replies, and remembers the choice.
        function togglePrompt(): string {
            saved.promptOnTop = !saved.promptOnTop;
            positionFile.writeAdapter();
            return saved.promptOnTop ? "top" : "bottom";
        }

        function getState(): string {
            return win.state;
        }

        // Puts on a wardrobe outfit (queen, evil) or takes it off again; "usual" changes back.
        // Bind it to a key in niri, like: qs -p shell ipc call alice toggleOutfit evil
        function toggleOutfit(name: string): string {
            const next = win.outfit === name ? "usual" : name;
            win.sendEvent({ type: "set_outfit", name: next });
            return next;
        }

        function wear(name: string): string {
            win.sendEvent({ type: "set_outfit", name: name });
            return name;
        }

        function prompt(): void {
            win.openPrompt();
        }

        function talk(): void {
            win.talk();
        }

        function confirm(approved: bool): bool {
            return win.answerConfirm(approved);
        }

        function send(text: string): string {
            return win.sendMessage(text) ? "sent" : "not sent";
        }

        function nudge(dx: int, dy: int): string {
            if (win.awake) {
                win.moveOpenTo(stage.x + dx, stage.y + dy);
                positionFile.writeAdapter();
                return saved.openX + "," + saved.openY;
            }
            win.moveTo(saved.right - dx, saved.bottom - dy);
            positionFile.writeAdapter();
            return saved.right + "," + saved.bottom;
        }
    }

    Item {
        id: root
        anchors.fill: parent

        // Fixed-size artwork area. An open book is pushed fully on-screen near an
        // edge; saved is untouched, so closing returns her to where she was left.
        Item {
            id: stage

            readonly property real freeX: root.width - saved.right - win.spineWidth / 2 - width / 2
            readonly property real freeY: root.height - saved.bottom - height

            width: win.stageWidth
            height: win.stageHeight
            x: win.expanded ? win.clamp(saved.hasOpenSpot ? saved.openX : freeX, 0, root.width - width) : freeX
            y: win.expanded ? win.clamp(saved.hasOpenSpot ? saved.openY : freeY, 0, root.height - height) : freeY

            Image {
                x: aliceClip.x + (aliceClip.width - width) / 2
                y: aliceClip.y + win.hatRoom + (aliceClip.height - win.hatRoom - height) / 2
                width: 340; height: 340
                source: "assets/glow.svg"
                sourceSize: Qt.size(340, 340)
                opacity: win.state === "listening" ? 0.9 : win.awake ? 0.45 : 0
                Behavior on opacity { NumberAnimation { duration: 500 } }
            }

            // Alice is clipped at the book's spine, so she appears to rise out of it.
            Item {
                id: aliceClip
                y: -win.hatRoom
                width: parent.width
                height: 378 + win.hatRoom
                clip: true

                Item {
                    id: alice
                    width: 240; height: 351
                    x: (parent.width - width) / 2
                    y: win.hatRoom + (win.awake ? 30 : parent.height - win.hatRoom + 10)

                    Behavior on y { NumberAnimation { duration: 700; easing.type: Easing.OutCubic } }

                    // Everything she wears and every face, drawn into one layer that shaders/rig.frag
                    // bends like a Live2D rig. The margins leave room for hats and for the head tilt.
                    Item {
                        id: rig
                        readonly property int marginSide: 50
                        readonly property int marginTop: 110
                        x: -marginSide; y: -marginTop
                        width: alice.width + 2 * marginSide
                        height: alice.height + marginTop
                        layer.enabled: win.expanded
                        layer.smooth: true
                        transform: Translate { y: -win.hop }
                        layer.effect: ShaderEffect {
                            fragmentShader: "shaders/rig.frag.qsb"
                            readonly property size rigSize: Qt.size(width, height)
                            // Measured on the art, which is drawn at half size here.
                            readonly property point neck: Qt.point(rig.marginSide + 120, rig.marginTop + 180)
                            readonly property point faceCentre: Qt.point(rig.marginSide + 120, rig.marginTop + 124)
                            readonly property size faceRadius: Qt.size(46, 52)
                            readonly property real headAngle: win.headAngle
                            readonly property real headTurn: win.headTurn
                            readonly property real headNod: win.headNod
                            readonly property real breath: win.breath
                            readonly property real sway: win.hairSway
                        }

                        Item {
                            id: body
                            x: rig.marginSide; y: rig.marginTop
                            width: alice.width; height: alice.height

                            // Worn on birthdays, tilted over the right side of her head.
                            Image {
                                z: 1
                                visible: win.partyHat && win.outfit !== "halloween" && win.outfit !== "queen"  // A hat or crown wins.
                                x: 150; y: -14
                                width: 62; height: 78
                                rotation: 18
                                source: "assets/party-hat.png"
                                sourceSize: Qt.size(124, 156)
                                fillMode: Image.PreserveAspectFit
                                onStatusChanged: if (status === Image.Error) source = "placeholder/party-hat.svg"
                            }

                            // The neutral face is always drawn; the others fade in over it, so her
                            // outline never thins mid-fade. All stay loaded, so nothing flickers.
                            Repeater {
                                model: ["neutral", "thinking", "listening"]

                                Image {
                                    required property string modelData
                                    // Art is drawn at half its pixel size, centred and standing on the same
                                    // spot, so outfits with a taller or wider canvas (a hat) keep her in place.
                                    readonly property bool art: status === Image.Ready && source.toString().indexOf("assets/") >= 0
                                    width: art ? implicitWidth / 2 : parent.width
                                    height: art ? implicitHeight / 2 : parent.height
                                    x: (parent.width - width) / 2
                                    y: parent.height - height
                                    smooth: true
                                    mipmap: true
                                    source: win.outfit ? "assets/outfits/" + win.outfit + "/alice-" + modelData + ".png" : "assets/alice-" + modelData + ".png"
                                    opacity: modelData === "neutral" || win.face === modelData ? 1 : 0
                                    Behavior on opacity { NumberAnimation { duration: 140 } }
                                    onStatusChanged: if (status === Image.Error)
                                        source = source.toString().indexOf("outfits/") >= 0 ? "assets/alice-" + modelData + ".png" : "placeholder/alice.svg"
                                }
                            }

                            // A painted face for her mood, from tools/make_expressions.py, over any base face.
                            // The eyes stay while the mood lasts; the mouth fades out whenever hers is moving.
                            Repeater {
                                model: Object.keys(win.moodStyles)

                                Item {
                                    id: moodFace
                                    required property string modelData
                                    property real shown: win.mood === modelData ? 1 : 0
                                    Behavior on shown { NumberAnimation { duration: 160 } }
                                    anchors.fill: parent
                                    visible: shown > 0.01 && speakingArt.art

                                    Repeater {
                                        model: ["-eyes", ""]

                                        Image {
                                            required property string modelData
                                            width: implicitWidth / 2
                                            height: implicitHeight / 2
                                            x: (parent.width - width) / 2
                                            y: parent.height - height
                                            smooth: true
                                            mipmap: true
                                            asynchronous: true
                                            source: win.faceDir + "alice-mood-" + moodFace.modelData + modelData + ".png"
                                            opacity: moodFace.shown * (modelData === "" ? 1 - win.talking : 1)
                                            visible: status === Image.Ready
                                            onStatusChanged: if (status === Image.Ready && modelData === "") {
                                                const known = Object.assign({}, win.moodArt);
                                                known[moodFace.modelData] = true;
                                                win.moodArt = known;
                                            }
                                        }
                                    }
                                }
                            }

                            // Expression overlays from tools/make_faces.py, over any face. Missing files are skipped.
                            Repeater {
                                model: ["blush", "halflid", "happy"]

                                Image {
                                    required property string modelData
                                    width: implicitWidth / 2
                                    height: implicitHeight / 2
                                    x: (parent.width - width) / 2
                                    y: parent.height - height
                                    smooth: true
                                    mipmap: true
                                    source: win.faceDir + "alice-" + modelData + ".png"
                                    opacity: modelData === "blush" ? win.blushLevel
                                        : modelData === "halflid" ? win.halfLids * (1 - win.happyEyes)
                                        : win.happyEyes
                                    visible: status === Image.Ready && speakingArt.art && opacity > 0.01
                                }
                            }

                            // Her open mouth, cut from the speaking art and opened as wide as her voice is loud.
                            // Every face shares the same mouth spot, so it fits over any of them.
                            Item {
                                id: mouth
                                x: 104; y: 137
                                width: 33; height: 14
                                clip: true
                                visible: speakingArt.art && opacity > 0.01
                                opacity: Math.min(1, win.mouthLevel * 4)
                                // Opens downward from her upper lip.
                                transform: Scale { origin.y: 2.5; yScale: 0.35 + 0.65 * win.mouthLevel }

                                Image {
                                    id: speakingArt
                                    readonly property bool art: status === Image.Ready && source.toString().indexOf("assets/") >= 0
                                    width: implicitWidth / 2
                                    height: implicitHeight / 2
                                    x: (body.width - width) / 2 - mouth.x
                                    y: body.height - height - mouth.y
                                    smooth: true
                                    mipmap: true
                                    source: win.outfit ? "assets/outfits/" + win.outfit + "/alice-speaking.png" : "assets/alice-speaking.png"
                                    onStatusChanged: if (status === Image.Error && source.toString().indexOf("outfits/") >= 0)
                                        source = "assets/alice-speaking.png"
                                }
                            }

                            // Closed eyes from tools/make_blink.py, over any face. Without the file she doesn't blink.
                            Image {
                                width: implicitWidth / 2
                                height: implicitHeight / 2
                                x: (parent.width - width) / 2
                                y: parent.height - height
                                smooth: true
                                mipmap: true
                                source: win.faceDir + "alice-blink.png"
                                visible: status === Image.Ready && speakingArt.art
                                // Smiling ^^ eyes are already shut.
                                opacity: win.blink * (1 - win.happyEyes)
                            }

                            // A little mark by her head for the mood: sparkles, a sweat drop, a "?".
                            Text {
                                x: (win.moodStyle.emoteX ?? 176) - width / 2
                                y: (win.moodStyle.emoteY ?? -4) - height / 2 + win.emoteFloat
                                text: win.moodStyle.emote ?? ""
                                font.family: "Noto Color Emoji"
                                font.pixelSize: 28
                                scale: win.emoteScale
                                opacity: win.emoteOpacity
                                visible: opacity > 0.01
                            }
                        }
                    }
                }
            }

            Image {
                id: spine
                width: win.spineWidth; height: win.spineHeight
                // Placed at the book's own spot, even while the open book is shifted elsewhere.
                x: (parent.width - width) / 2 + stage.freeX - stage.x
                y: parent.height - height + stage.freeY - stage.y + restOffset
                // Rotation turns around the center, so this keeps it resting on the same line.
                property real restOffset: saved.sideways ? (win.spineHeight - win.spineWidth) / 2 : 0
                rotation: saved.sideways ? 90 : 0
                Behavior on rotation { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
                Behavior on restOffset { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
                source: "assets/book-spine.png"
                onStatusChanged: if (status === Image.Error) source = "placeholder/book-spine.svg"
                sourceSize: Qt.size(76, 392)
                opacity: win.awake ? 0 : 1
                Behavior on opacity { NumberAnimation { duration: 250 } }
            }

            // A page turning, seen as a sliver sweeping along that top edge.
            Rectangle {
                id: turningPage
                // Follows the top edge of the pages, which rises away from the gutter.
                y: openBook.y + 32 - 38 * Math.abs(x + width / 2 - parent.width / 2) / (openBook.width / 2)
                width: 16; height: 22
                radius: 3
                color: "#fffaf0"
                border.color: "#cdbf9f"
                visible: win.state === "thinking"

                NumberAnimation on x {
                    running: win.state === "thinking"
                    loops: Animation.Infinite
                    from: openBook.x + openBook.width - 26
                    to: openBook.x + 10
                    duration: 900
                    easing.type: Easing.InOutQuad
                }
            }

            Image {
                id: openBook
                width: 242; height: 175
                anchors { horizontalCenter: parent.horizontalCenter; bottom: parent.bottom }
                source: "assets/book-back.png"
                onStatusChanged: if (status === Image.Error) source = "placeholder/book-back.svg"
                sourceSize: Qt.size(484, 350)
                opacity: win.awake ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 250 } }
            }
        }

        // The input region. A direct child of root so the mask tracks its movement.
        Item {
            id: hitArea
            width: win.awake ? openBook.width : win.closedWidth
            height: win.awake ? openBook.height : win.closedHeight
            x: (win.awake ? stage.x : stage.freeX) + (stage.width - width) / 2
            y: (win.awake ? stage.y : stage.freeY) + stage.height - height

            MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton | Qt.RightButton
                cursorShape: dragging ? Qt.ClosedHandCursor : Qt.PointingHandCursor

                property point pressPoint
                property real pressRight: 0
                property real pressBottom: 0
                property bool dragging: false

                // Measured against root, which stays still, so nothing feeds back.
                onPressed: mouse => {
                    pressPoint = mapToItem(root, mouse.x, mouse.y);
                    pressRight = win.awake ? stage.x : saved.right;
                    pressBottom = win.awake ? stage.y : saved.bottom;
                    dragging = false;
                }
                onPositionChanged: mouse => {
                    if (!(mouse.buttons & Qt.LeftButton))
                        return;
                    const p = mapToItem(root, mouse.x, mouse.y);
                    const dx = p.x - pressPoint.x;
                    const dy = p.y - pressPoint.y;
                    if (!dragging && Math.hypot(dx, dy) < 5)
                        return;
                    dragging = true;
                    if (win.awake) {
                        win.moveOpenTo(pressRight + dx, pressBottom + dy);
                    } else {
                        win.moveTo(pressRight - dx, pressBottom - dy);
                    }
                }
                onReleased: if (dragging) positionFile.writeAdapter()
                onClicked: mouse => {
                    if (dragging)
                        return;
                    if (mouse.button === Qt.RightButton) {
                        win.toggleSideways();
                        return;
                    }
                    if (win.awake)
                        win.state = "dormant";
                    else
                        win.openPrompt();
                }
            }
        }

        // The chat column sits beside her, on whichever side has room, tucked in close
        // enough that the speech bubble's tail can reach her mouth. The book starts 49 px in.
        readonly property int columnWidth: 360
        readonly property bool chatOnLeft: stage.x >= columnWidth - 40
        readonly property real columnX: chatOnLeft ? stage.x + 40 - columnWidth : stage.x + stage.width - 40
        readonly property bool promptOnTop: saved.promptOnTop
        // Her mouth on the artwork: (420, 491) on the 832 px art, which is drawn 240 px wide.
        readonly property real mouthX: stage.x + aliceClip.x + alice.x + 121
        readonly property real mouthY: stage.y + aliceClip.y + alice.y + 142
        readonly property real restingMouthY: stage.y + 30 + 142
        // The space the bubble and confirm card share, beside the typing box.
        readonly property real chatTop: promptOnTop ? (win.promptOpen ? promptBox.y + promptBox.height + 8 : stage.y) : stage.y - win.hatRoom
        readonly property real chatBottom: !promptOnTop && win.promptOpen ? promptBox.y - 8 : stage.y + stage.height

        // Collapsed items have no size, which also removes them from the input mask.
        Rectangle {
            id: promptBox
            x: root.columnX
            y: root.promptOnTop ? stage.y : stage.y + stage.height - 42
            width: win.promptOpen ? root.columnWidth : 0
            height: win.promptOpen ? 42 : 0
            radius: 10
            color: "#f8f1de"
            border.color: "#5b2333"
            border.width: 2
            clip: true

            TextInput {
                id: promptInput
                anchors { fill: parent; leftMargin: 14; rightMargin: 14 }
                verticalAlignment: TextInput.AlignVCenter
                color: "#2b2116"
                font.pixelSize: 15
                clip: true
                readOnly: win.busy
                onAccepted: if (win.sendMessage(text)) clear()
                Keys.onEscapePressed: win.promptOpen = false
            }
        }

        Rectangle {
            id: confirmCard

            readonly property bool shown: win.pendingConfirm !== null
            readonly property int detailHeight: Math.min(confirmDetail.contentHeight, 240)

            x: root.columnX
            y: root.promptOnTop ? root.chatTop : root.chatBottom - height
            width: shown ? root.columnWidth : 0
            height: shown ? detailHeight + 104 : 0
            radius: 10
            color: "#f8f1de"
            border.color: "#5b2333"
            border.width: 2
            clip: true

            Text {
                id: confirmTitle
                x: 14; y: 12
                width: root.columnWidth - 28
                text: win.pendingConfirm?.title ?? ""
                color: "#5b2333"
                font.pixelSize: 15
                font.bold: true
            }

            // The full detail stays reachable by scrolling: this is what gets approved.
            Flickable {
                x: 14; y: 38
                width: root.columnWidth - 28
                height: confirmCard.detailHeight
                contentHeight: confirmDetail.contentHeight
                boundsBehavior: Flickable.StopAtBounds
                clip: true

                Text {
                    id: confirmDetail
                    width: root.columnWidth - 28
                    text: win.pendingConfirm?.detail ?? ""
                    textFormat: Text.PlainText
                    wrapMode: Text.WrapAnywhere
                    color: "#2b2116"
                    font.family: "monospace"
                    font.pixelSize: 13
                }
            }

            Row {
                anchors { right: parent.right; bottom: parent.bottom; margins: 12 }
                spacing: 8

                Repeater {
                    model: [
                        { label: "Deny", approved: false, fill: "#ece2c6", ink: "#2b2116" },
                        { label: "Allow", approved: true, fill: "#5b2333", ink: "#f8f1de" }
                    ]

                    Rectangle {
                        required property var modelData
                        width: 84; height: 34
                        radius: 8
                        color: modelData.fill

                        Text {
                            anchors.centerIn: parent
                            text: parent.modelData.label
                            color: parent.modelData.ink
                            font.pixelSize: 14
                        }

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.answerConfirm(parent.modelData.approved)
                        }
                    }
                }
            }
        }

        // The speech bubble's tail, from the bubble's edge toward her mouth. It sits on top of
        // the bubble so its base covers the border there, and outside it so it isn't clipped.
        Shape {
            id: bubbleTail

            readonly property real edgeX: root.chatOnLeft ? bubble.x + bubble.width - 3 : bubble.x + 3
            readonly property real baseY: Math.max(bubble.y + 18, Math.min(root.restingMouthY, bubble.y + bubble.height - 34))
            readonly property real tipX: root.mouthX + (root.chatOnLeft ? -34 : 34)
            readonly property real tipY: root.mouthY + 4

            z: 1
            visible: bubble.shown
            preferredRendererType: Shape.CurveRenderer

            ShapePath {
                fillColor: "#f8f1de"
                strokeColor: "#5b2333"
                strokeWidth: 2
                joinStyle: ShapePath.RoundJoin
                startX: bubbleTail.edgeX; startY: bubbleTail.baseY
                PathQuad { x: bubbleTail.tipX; y: bubbleTail.tipY; controlX: (bubbleTail.edgeX + bubbleTail.tipX) / 2; controlY: bubbleTail.baseY + 2 }
                PathQuad { x: bubbleTail.edgeX; y: bubbleTail.baseY + 16; controlX: (bubbleTail.edgeX + bubbleTail.tipX) / 2; controlY: bubbleTail.baseY + 18 }
            }
        }

        Rectangle {
            id: bubble

            readonly property bool shown: win.awake && win.replyText !== "" && !win.pendingConfirm
            readonly property int maxHeight: Math.min(390, root.chatBottom - root.chatTop)

            x: root.columnX
            // Level with her mouth where it fits, so the tail stays short; pushed up or down when it doesn't.
            y: Math.max(root.chatTop, Math.min(root.restingMouthY - 40, root.chatBottom - height))
            width: shown ? root.columnWidth : 0
            height: shown ? Math.min(replyContent.height + 28, maxHeight) : 0
            radius: 10
            color: "#f8f1de"
            border.color: "#5b2333"
            border.width: 2
            clip: true

            Flickable {
                id: replyScroll
                anchors { fill: parent; margins: 14 }
                contentHeight: replyContent.height
                boundsBehavior: Flickable.StopAtBounds
                clip: true
                // Follow the end of her reply; a link list below it stays put to scroll through.
                onContentHeightChanged: contentY = Math.max(0, Math.min(reply.contentHeight, contentHeight) - height)

                Column {
                    id: replyContent
                    width: root.columnWidth - 28
                    spacing: 10

                    Text {
                        id: reply
                        width: parent.width
                        text: win.formatReply(win.replyText)
                        textFormat: Text.StyledText
                        linkColor: "#7a2e44"
                        onLinkActivated: link => Qt.openUrlExternally(link)
                        wrapMode: Text.Wrap
                        color: "#2b2116"
                        font.pixelSize: 15

                        MouseArea {
                            anchors.fill: parent
                            acceptedButtons: Qt.NoButton
                            cursorShape: reply.hoveredLink ? Qt.PointingHandCursor : Qt.ArrowCursor
                        }
                    }

                    // The weather tool's week at a glance, under her answer.
                    Column {
                        visible: !!win.weatherCard
                        width: parent.width
                        spacing: 6

                        Text {
                            width: parent.width
                            text: (win.weatherCard?.place ?? "") + " · now " + (win.weatherCard?.now ?? "")
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            color: "#5b2333"
                            font.pixelSize: 13
                            font.bold: true
                        }

                        Row {
                            id: forecastRow
                            readonly property var days: win.weatherCard?.days ?? []
                            width: parent.width

                            Repeater {
                                model: forecastRow.days

                                Rectangle {
                                    required property var modelData
                                    required property int index
                                    width: forecastRow.width / Math.max(forecastRow.days.length, 1)
                                    height: dayColumn.height + 10
                                    radius: 6
                                    color: index === 0 ? "#efe3c4" : "transparent"

                                    Column {
                                        id: dayColumn
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        y: 5
                                        spacing: 1

                                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.label; color: "#5b2333"; font.pixelSize: 11; font.bold: index === 0 }
                                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.icon; font.pixelSize: 20 }
                                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.high + "°"; color: "#2b2116"; font.pixelSize: 13; font.bold: true }
                                        Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.low + "°"; color: "#6b5b45"; font.pixelSize: 12 }
                                        Text {
                                            anchors.horizontalCenter: parent.horizontalCenter
                                            text: modelData.rain + "%"
                                            color: modelData.rain >= 60 ? "#2a5d8f" : "#6b8fb0"
                                            font.pixelSize: 11
                                            font.bold: modelData.rain >= 60
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Column {
                        visible: !!win.linkList
                        width: parent.width
                        spacing: 4

                        Text {
                            width: parent.width
                            text: win.linkList?.title ?? ""
                            wrapMode: Text.Wrap
                            color: "#5b2333"
                            font.pixelSize: 14
                            font.bold: true
                        }

                        Repeater {
                            model: win.linkList?.items ?? []

                            Item {
                                id: linkRow
                                required property var modelData
                                // News puts its source under the headline; short items like games keep it on the right.
                                readonly property bool stacked: modelData.detail !== undefined
                                width: parent.width
                                height: (stacked ? linkName.contentHeight + linkPrice.contentHeight + 2 : Math.max(linkName.contentHeight, linkPrice.contentHeight)) + 6

                                Text {
                                    id: linkName
                                    width: linkRow.stacked ? parent.width : parent.width - linkPrice.width - 8
                                    text: linkRow.modelData.name
                                    textFormat: Text.PlainText
                                    wrapMode: Text.Wrap
                                    color: "#7a2e44"
                                    font.pixelSize: 14
                                    font.underline: linkMouse.containsMouse
                                }

                                Text {
                                    id: linkPrice
                                    x: linkRow.stacked ? 0 : parent.width - width
                                    y: linkRow.stacked ? linkName.contentHeight + 2 : 0
                                    text: linkRow.modelData.detail ?? linkRow.modelData.price
                                    textFormat: Text.PlainText
                                    color: "#6b5b45"
                                    font.pixelSize: linkRow.stacked ? 12 : 13
                                }

                                MouseArea {
                                    id: linkMouse
                                    anchors.fill: linkName
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: Qt.openUrlExternally(linkRow.modelData.url)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
