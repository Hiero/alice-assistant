package io.github.hiero.alice

import android.app.Application
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.Network
import android.net.Uri
import android.os.SystemClock
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.BufferedWriter
import java.io.IOException
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.InetSocketAddress
import java.net.Socket

data class LinkItem(val name: String, val url: String, val detail: String)

data class ChatItem(
    val fromUser: Boolean,
    val text: String,
    val links: List<LinkItem> = emptyList(),
    val linksTitle: String = "",
    val isError: Boolean = false,
    val onPhone: Boolean = false,  // Said while she ran on the phone, not on the PC.
)

data class ConfirmCard(val id: String, val title: String, val detail: String)

enum class Connection { NOT_SET_UP, CONNECTING, ONLINE, OFFLINE }

/** Auto uses the PC whenever it answers; PC and Phone force one side. */
enum class Mode { AUTO, PC, PHONE }

/** Talks to the Alice backend on the PC over Tailscale, using the same JSON lines as the desktop shell,
 * and falls back to the phone's own Alice when the PC is out of reach or Phone mode is picked. */
class AliceViewModel(app: Application) : AndroidViewModel(app) {
    private val prefs = app.getSharedPreferences("alice", Context.MODE_PRIVATE)

    val items = mutableStateListOf<ChatItem>()
    var connection by mutableStateOf(Connection.OFFLINE); private set
    var mode by mutableStateOf(runCatching { Mode.valueOf(prefs.getString("mode", "AUTO")!!) }.getOrDefault(Mode.AUTO)); private set
    var mood by mutableStateOf("neutral"); private set
    var thinking by mutableStateOf(false); private set
    var status by mutableStateOf(""); private set
    var confirm by mutableStateOf<ConfirmCard?>(null); private set
    /** "listening", "thinking" or "speaking", as the backend tells the shell; it sets her pose. */
    var stage by mutableStateOf("listening"); private set
    /** Her mouth keeps moving until then (uptime ms): about as long as reading the reply aloud would take. */
    var speakingUntil by mutableLongStateOf(0L); private set
    private var speakStart = 0L
    private var spokenChars = 0
    private var moodRest: Job? = null

    val usingPc: Boolean get() = mode != Mode.PHONE && connection == Connection.ONLINE

    /** What she has on, as the PC last said; kept, so she's dressed the same offline. */
    var dress by mutableStateOf(Dress(prefs.getString("outfit", "")!!, prefs.getBoolean("outfitFaces", false), prefs.getBoolean("hat", false))); private set

    private fun wear(json: JSONObject?, hat: Boolean = dress.hat) {
        if (json == null) return
        dress = Dress(json.optString("outfit"), json.optBoolean("faces"), json.optBoolean("hat", hat))
        prefs.edit().putString("outfit", dress.outfit).putBoolean("outfitFaces", dress.faces).putBoolean("hat", dress.hat).apply()
        phoneModel.newConversation()
        DebugLog.log("wearing ${dress.outfit.ifEmpty { "her usual dress" }}${if (dress.hat) " and her party hat" else ""}")
    }

    /** Debug builds: dresses her on the phone only (the PC sets it again on the next connection). */
    fun tryOn(outfit: String, hat: Boolean) {
        val name = if (outfit == "usual") "" else outfit
        wear(JSONObject().put("outfit", name).put("faces", name == "evil").put("hat", hat))
    }

    /** Talking to her: recorded for the PC's Whisper, or recognised on the phone when the PC is out of reach. */
    private val voiceInput = VoiceInput(app)
    var listening by mutableStateOf(false); private set

    fun talk() {
        if (listening) { voiceInput.stop(); return }
        if (thinking) return
        hush()
        if (!usingPc && mode == Mode.PC) {
            items.add(ChatItem(false, "I can't reach your PC right now. Switch to Auto or Phone to talk to me here.", isError = true))
            return
        }
        listening = true
        status = "Listening… tap 🎤 again to stop"
        if (usingPc) {
            DebugLog.log("listening for the PC")
            voiceInput.record { pcm ->
                viewModelScope.launch {
                    listening = false
                    status = ""
                    if (pcm.isEmpty()) { status = "I didn't hear anything."; return@launch }
                    if (!usingPc) { status = "Your PC stopped answering. Try again?"; return@launch }
                    thinking = true
                    replyIndex = -1
                    send(JSONObject().put("type", "voice_message").put("pcm", android.util.Base64.encodeToString(pcm, android.util.Base64.NO_WRAP)).put("voice", voiceOn))
                }
            }
        } else {
            DebugLog.log("listening on the phone")
            voiceInput.recognise(
                done = { text ->
                    listening = false
                    status = if (text.isBlank()) "I didn't hear anything." else ""
                    if (text.isNotBlank()) sendMessage(text)
                },
                failed = { reason -> listening = false; status = reason },
            )
        }
    }

    /** Whether she speaks her replies aloud (in her PC voice, while the PC is in reach). */
    var voiceOn by mutableStateOf(prefs.getBoolean("voice", true)); private set
    val voice = VoicePlayer(app)
    private val phoneVoice = PhoneVoice(app, voice)
    /** Her voice offline too: Kokoro on the phone makes speech at about real time on the Fold6 and adds to the
     * memory her model already takes, so it's a separate switch in Setup, off unless turned on. */
    var phoneVoiceOn by mutableStateOf(prefs.getBoolean("phoneVoice", false)); private set
    val phoneVoiceInstalled: Boolean get() = phoneVoice.available
    private val speaksOnPhone: Boolean get() = voiceOn && phoneVoiceOn && phoneVoice.available

    fun setPhoneVoice(on: Boolean) {
        phoneVoiceOn = on
        prefs.edit().putBoolean("phoneVoice", on).apply()
        if (!on) { phoneVoice.stop(); phoneVoice.release() }
    }

    /** Stops her talking, wherever the voice is coming from. */
    private fun hush() {
        phoneVoice.stop()
        voice.stop()
    }

    fun setVoice(on: Boolean) {
        voiceOn = on
        prefs.edit().putBoolean("voice", on).apply()
        if (!on) hush()
    }

    /** The debug log (DebugLog.kt): on by default in debug builds, switched in Setup. */
    var debugLog by mutableStateOf(prefs.getBoolean("debug", BuildConfig.DEBUG)); private set

    fun setDebugLogging(on: Boolean) {
        debugLog = on
        prefs.edit().putBoolean("debug", on).apply()
        DebugLog.enabled = on
        if (!on) DebugLog.clear()
    }

    var host: String get() = prefs.getString("host", DEFAULT_HOST)!!; private set(v) { prefs.edit().putString("host", v).apply() }
    var port: Int get() = prefs.getInt("port", DEFAULT_PORT); private set(v) { prefs.edit().putInt("port", v).apply() }
    var token: String get() = prefs.getString("token", "")!!; private set(v) { prefs.edit().putString("token", v).apply() }

    @Volatile private var socket: Socket? = null
    @Volatile private var writer: BufferedWriter? = null
    private val writeLock = Mutex()
    private var replyIndex = -1  // The bubble the PC's reply is streaming into; -1 starts a new one.
    private var generation = 0  // Each connection attempt's number, so an old one ending can't undo a newer one.
    private var foreground = false
    private var retry: Job? = null
    private var retryDelayMs = FIRST_RETRY_MS
    private var apps: List<PhoneAlice.App> = emptyList()
    private val store = PhoneStore(app)
    private val tools by lazy { PhoneTools(app, store) { launch(it) } }
    private var syncingActions = 0
    // Full 32-bit precision on the GPU by default: in 16-bit, tested on the Fold6, her model misread and miswrote
    // numbers ("37 plus 48" became "three plus eight is eleven"; 10 minutes became "101:100"). It costs about 5 s
    // more to load and 4 s more on a chat's first reply; later replies are about as quick.
    val phoneModel = PhoneModel(app, store, { dress.outfit }, { prefs.getString("modelSetup", "gpu32")!! }, { currentBrain() })

    /** Her phone brain, as picked in Setup: "auto", "full" or "light". */
    var brainChoice by mutableStateOf(prefs.getString("brain", "auto")!!); private set
    val lightInstalled: Boolean get() = phoneModel.lightFile.exists()

    fun chooseBrain(choice: String) {
        brainChoice = choice
        prefs.edit().putString("brain", choice).apply()
        DebugLog.log("brain choice: $choice")
    }

    /** Which brain to run now. Auto uses the full one unless the phone is warm or its battery low and not charging,
     * when the light one spares it. */
    fun currentBrain(): String {
        if (!lightInstalled) return "full"
        if (brainChoice != "auto") return brainChoice
        val app = getApplication<Application>()
        val warm = app.getSystemService(android.os.PowerManager::class.java).currentThermalStatus >= android.os.PowerManager.THERMAL_STATUS_MODERATE
        val battery = app.getSystemService(android.os.BatteryManager::class.java)
        val low = battery.getIntProperty(android.os.BatteryManager.BATTERY_PROPERTY_CAPACITY) in 1..20 && !battery.isCharging
        return if (warm || low) "light" else "full"
    }

    /** Debug builds: how her model runs ("gpu", "gpu32" or "cpu"); it reloads for the next reply. */
    fun setModelSetup(setup: String) {
        prefs.edit().putString("modelSetup", setup).apply()
        phoneModel.release()
        DebugLog.log("model setup: $setup")
    }

    /** Debug builds: straight to her model, past the shortcuts and lookups, for testing it. */
    fun probe(text: String) {
        items.add(ChatItem(true, text, onPhone = true))
        askPhoneModel(text)
    }
    private var syncing = 0  // How many stored phone turns the PC is being sent right now.
    private val phoneItems = mutableListOf<ChatItem>()  // What was said on the phone, kept under the PC's history until syncing exists.

    private val connectivity = app.getSystemService(ConnectivityManager::class.java)
    private val networkWatcher = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            // A new network (Wi-Fi to mobile, or coming back from none) is worth an immediate try.
            viewModelScope.launch { retryDelayMs = FIRST_RETRY_MS; if (connection == Connection.OFFLINE) connect() }
        }
    }

    init {
        DebugLog.init(app, debugLog)
        DebugLog.log("app started, mode $mode, ${BuildConfig.VERSION_NAME}")
        DebugLog.log("offline speech recognition available: ${runCatching { VoiceInput(app).canRecogniseOffline }.getOrDefault(false)}")
        connection = if (token.isEmpty() || host.isEmpty()) Connection.NOT_SET_UP else Connection.OFFLINE
        runCatching { connectivity?.registerDefaultNetworkCallback(networkWatcher) }
    }

    /** Without the PC, she works out the day's outfit and hat herself, from what the PC last told her. */
    private fun dressForTheDay() {
        val offline = store.offline()
        val today = java.time.LocalDate.now()
        val name = Occasions.outfit(today, offline.worn)
        val hat = Occasions.greeting(today, offline.birthdays, offline.greetings).second
        if (name != dress.outfit || hat != dress.hat) wear(JSONObject().put("outfit", name).put("faces", name == "evil").put("hat", hat))
    }

    /** On the first open of a day with a holiday or birthday, she says so, as the desktop does. */
    private fun greetForTheDay() {
        val today = java.time.LocalDate.now().toString()
        if (prefs.getString("greeted", "") == today) return
        val offline = store.offline()
        val (words, _) = Occasions.greeting(java.time.LocalDate.now(), offline.birthdays, offline.greetings)
        prefs.edit().putString("greeted", today).apply()
        if (words.isEmpty()) return
        DebugLog.log("greeting for the day")
        items.add(ChatItem(false, words, onPhone = !usingPc))
        mood = "happy"
        if (speaksOnPhone) phoneVoice.say(words) else { speakStart = 0L; speak(words.length) }
        finishTurn()
    }

    fun setForeground(visible: Boolean) {
        foreground = visible
        if (visible) {
            apps = installedApps()
            if (!usingPc) { dressForTheDay(); greetForTheDay() }
            retryDelayMs = FIRST_RETRY_MS
            connect()
        } else {
            retry?.cancel()
            hush()  // She doesn't keep talking from a pocket.
            voiceInput.stop()
        }
    }

    fun changeMode(newMode: Mode) {
        mode = newMode
        prefs.edit().putString("mode", newMode.name).apply()
        DebugLog.log("mode $newMode")
        if (newMode == Mode.PHONE) {
            disconnect()
        } else {
            retryDelayMs = FIRST_RETRY_MS
            connect()
        }
    }

    fun configure(newHost: String?, newPort: Int?, newToken: String?) {
        newHost?.trim()?.takeIf { it.isNotEmpty() }?.let { host = it }
        newPort?.takeIf { it in 1..65535 }?.let { port = it }
        newToken?.trim()?.takeIf { it.isNotEmpty() }?.let { token = it }
        disconnect()
        retryDelayMs = FIRST_RETRY_MS
        connect()
    }

    /** Opens the connection to the PC, if one is set up and none is open. The PC must answer "hello" (which carries
     * the token and the phone's apps) with "hello_ok", and with it sends the conversation, her outfit and what the
     * phone needs to stand in for it offline. Then events stream in until the socket closes, when she falls back to
     * the phone and a retry is scheduled, waiting twice as long each time up to MAX_RETRY_MS. */
    fun connect() {
        if (token.isEmpty() || host.isEmpty()) { connection = Connection.NOT_SET_UP; return }
        if (mode == Mode.PHONE || connection == Connection.CONNECTING || connection == Connection.ONLINE) return
        retry?.cancel()
        connection = Connection.CONNECTING
        val mine = ++generation
        val s = Socket()
        socket = s
        val appList = appsJson()
        viewModelScope.launch(Dispatchers.IO) {
            var refused = false
            try {
                s.keepAlive = true
                s.connect(InetSocketAddress(host, port), CONNECT_TIMEOUT_MS)
                val out = BufferedWriter(OutputStreamWriter(s.getOutputStream(), Charsets.UTF_8))
                val input = BufferedReader(InputStreamReader(s.getInputStream(), Charsets.UTF_8))
                writer = out
                write(JSONObject().put("type", "hello").put("token", token).put("apps", appList))
                s.soTimeout = HELLO_TIMEOUT_MS
                val answer = input.readLine()?.let { JSONObject(it) }
                if (answer?.optString("type") != "hello_ok") {
                    refused = true
                    throw IOException("The PC refused the connection. Check the token in Setup.")
                }
                s.soTimeout = 0
                withContext(Dispatchers.Main) {
                    if (mine != generation) return@withContext
                    DebugLog.log("connected to the PC at $host:$port")
                    // Her phone model holds about 4 GB; with the PC back, give it back to the phone.
                    phoneModel.release()
                    phoneVoice.release()
                    connection = Connection.ONLINE
                    status = ""
                    retryDelayMs = FIRST_RETRY_MS
                    showHistory(answer.optJSONArray("history"))
                    val outfit = answer.optJSONObject("outfit")
                    store.saveOffline(answer.optString("memories"), answer.optJSONArray("context"),
                        outfit?.optJSONArray("birthdays"), outfit?.optString("worn").orEmpty(), outfit?.optJSONObject("greetings"))
                    wear(outfit)
                    answer.optJSONObject("settings")?.let { settings ->
                        prefs.edit().putString("steamId", settings.optString("steam_id")).putString("steamCountry", settings.optString("steam_country", "br"))
                            .putString("newsEdition", settings.optString("news_edition", "us")).apply()
                    }
                    greetForTheDay()
                    syncPhoneTurns()
                }
                while (true) {
                    val line = input.readLine() ?: break
                    val event = runCatching { JSONObject(line) }.getOrNull() ?: continue
                    withContext(Dispatchers.Main) { if (mine == generation) handle(event) }
                }
            } catch (e: Exception) {
                DebugLog.log("PC connection ended: ${e.javaClass.simpleName}: ${e.message}")
                if (refused) withContext(Dispatchers.Main) { status = e.message!! }
            } finally {
                runCatching { s.close() }
                withContext(Dispatchers.Main) {
                    if (mine != generation) return@withContext
                    socket = null
                    writer = null
                    syncing = 0
                    syncingActions = 0
                    connection = Connection.OFFLINE
                    dressForTheDay()
                    stage = "listening"
                    if (thinking) {
                        thinking = false
                        replyIndex = -1
                        items.add(ChatItem(false, "Your PC stopped answering in the middle of that. Try again?", isError = true))
                    }
                    confirm = null
                    // A wrong token won't fix itself, so it isn't retried.
                    if (!refused) scheduleRetry()
                }
            }
        }
    }

    private fun scheduleRetry() {
        if (!foreground || mode == Mode.PHONE) return
        retry?.cancel()
        val wait = retryDelayMs
        retryDelayMs = (retryDelayMs * 2).coerceAtMost(MAX_RETRY_MS)
        retry = viewModelScope.launch {
            delay(wait)
            connect()
        }
    }

    fun disconnect() {
        generation++
        retry?.cancel()
        runCatching { socket?.close() }
        socket = null
        writer = null
        if (thinking) { thinking = false; replyIndex = -1 }
        confirm = null
        connection = if (token.isEmpty()) Connection.NOT_SET_UP else Connection.OFFLINE
    }

    /** Tells the PC what was said on the phone while it was out of reach, so she knows about it there too. */
    private fun syncPhoneTurns() {
        val turns = store.pending()
        val actions = store.actions()
        if ((turns.isEmpty() && actions.isEmpty()) || syncing > 0 || syncingActions > 0) return
        syncing = turns.size
        syncingActions = actions.size
        DebugLog.log("sending ${turns.size} phone turns and ${actions.size} notes or memories to the PC")
        send(JSONObject().put("type", "phone_sync").put("turns", store.pendingJson(turns)).put("actions", store.actionsJson(actions)))
    }

    private fun showHistory(history: JSONArray?) {
        if (history == null) return
        val shown = (0 until history.length()).mapNotNull { history.optJSONObject(it) }.map {
            ChatItem(fromUser = it.optString("role") == "user", text = it.optString("text"), onPhone = it.optBoolean("phone"))
        }
        items.clear()
        items.addAll(shown)
        items.addAll(phoneItems)
        replyIndex = -1
    }

    fun sendMessage(text: String) {
        val clean = text.trim()
        if (clean.isEmpty() || thinking) return
        when {
            usingPc -> {
                items.add(ChatItem(true, clean))
                replyIndex = -1
                thinking = true
                hush()
                DebugLog.log("to PC: $clean")
                send(JSONObject().put("type", "user_message").put("text", clean).put("voice", voiceOn))
            }
            mode == Mode.PC -> {
                items.add(ChatItem(false, "I can't reach your PC right now. Switch to Auto or Phone to talk to me here.", isError = true))
                connect()
            }
            else -> answerOnPhone(clean)
        }
    }

    /** A message while the PC is out of reach. Simple requests ("open Spotify", "set a timer for 5 minutes") are
     * understood by PhoneAlice's patterns and done at once; everything else goes to her model on the phone. */
    private fun answerOnPhone(text: String) {
        if (apps.isEmpty()) apps = installedApps()
        val asked = ChatItem(true, text, onPhone = true)
        items.add(asked)
        phoneItems.add(asked)
        store.add("user", text)
        val light = currentBrain() == "light"
        val quick = PhoneAlice.shortcut(text, apps, light)
        if (quick != null || !phoneModel.available) {
            var answer = quick ?: PhoneAlice.answer(text, apps)
            // A command is done before she says so, so she never claims something that failed (a missing permission).
            (answer.action as? PhoneAlice.Action.Tool)?.let { tool ->
                val outcome = tools.run(tool.tag, tool.arg, text)
                DebugLog.log("command ${tool.tag} ${tool.arg}: ${if (outcome.done) "done" else "not done"} ${outcome.note}")
                if (!outcome.done) answer = answer.copy(text = outcome.note.trim('(', ')').ifEmpty { "That didn't work on the phone." }, mood = "worried")
                answer = answer.copy(action = null)
                pendingNote = if (outcome.done) "You just did [${tool.tag}: ${tool.arg}] for the user." else "Your [${tool.tag}: ${tool.arg}] did NOT work: ${outcome.note.trim('(', ')')} Nothing changed."
            }
            addPhoneReply(answer.text)
            store.add("assistant", "[${answer.mood}] ${answer.text}")
            mood = answer.mood
            speakStart = 0L
            hush()
            if (speaksOnPhone) phoneVoice.say(answer.text) else speak(answer.text.length)
            finishTurn()
            DebugLog.log("phone shortcut: \"$text\" -> ${answer.action ?: "no action"}")
            answer.action?.let { action ->
                perform(action)
                // Her model never saw this turn, so it hears about it with the next message.
                pendingNote = when (action) {
                    is PhoneAlice.Action.Search -> "You just opened a web search for \"${action.query}\" for the user."
                    is PhoneAlice.Action.OpenApp -> "You just opened ${action.app.label} for the user."
                    is PhoneAlice.Action.OpenRole -> "You just opened ${action.role.label} for the user."
                    is PhoneAlice.Action.Tool -> "You just did [${action.tag}: ${action.arg}] for the user."
                }
            }
            return
        }
        askPhoneModel(text, PhoneAlice.lookup(text, homeCity()))
    }

    // What the app did for her since her model last spoke (or failed to do), told to it with the next message,
    // since the model never sees an action's outcome otherwise.
    private var pendingNote = ""

    /** The home city she remembers (a memory like "The user's home city is São Carlos, São Paulo."), as on the PC. */
    private fun homeCity(): String = store.offline().memories.lines()
        .firstNotNullOfOrNull { Regex("home (?:city|town) is (.+?)\\.?$", RegexOption.IGNORE_CASE).find(it.trim())?.groupValues?.get(1) }.orEmpty()

    /** Opens what she decided on, logging which app actually took it. */
    private fun perform(action: PhoneAlice.Action): Boolean = when (action) {
        is PhoneAlice.Action.OpenApp -> launch(getApplication<Application>().packageManager.getLaunchIntentForPackage(action.app.pkg))
        is PhoneAlice.Action.OpenRole -> launch(roleIntent(action.role))
        is PhoneAlice.Action.Search -> launch(searchIntent(action.query))
        is PhoneAlice.Action.Tool -> tools.run(action.tag, action.arg).done
    }

    private fun addPhoneReply(text: String, isError: Boolean = false) {
        val reply = ChatItem(false, text, onPhone = true, isError = isError)
        items.add(reply)
        phoneItems.add(reply)
    }

    /** Her own model writes the reply; the mood and action tags in it set her face and do the action.
     *
     * In order: the model's reply streams into a bubble (and her voice, sentence by sentence); if it asked for a
     * lookup (weather, currency, news, Steam), the app fetches it and the model is asked a second time to react to
     * what was found; then any action tag is done, and what really happened is kept in [pendingNote] for the model's
     * next message. [lookup], when given, is a Steam or news request already understood: it's fetched first, and
     * her model only says what it found. */
    private fun askPhoneModel(text: String, lookup: Pair<String, String>? = null) {
        thinking = true
        stage = "thinking"
        moodRest?.cancel()
        if (phoneModel.state != PhoneModel.State.READY) status = "Waking up on your phone… this takes a few seconds"
        // The time it was sent, as the PC stamps messages, so she can set alarms and knows the day.
        val stamp = java.time.LocalDateTime.now().format(java.time.format.DateTimeFormatter.ofPattern("EEEE, yyyy-MM-dd HH:mm", java.util.Locale.ENGLISH))
        val stamped = "$text\n\n(Sent $stamp. Phone: ${tools.stateLine()}.)"
        val prompt = if (pendingNote.isNotEmpty()) "($pendingNote)\n\n$stamped" else stamped
        pendingNote = ""
        DebugLog.log("to phone model: $prompt")
        val raw = StringBuilder()
        var index = -1
        var shown = 0
        var spoken = 0  // How much of the reply has gone to her voice.
        hush()
        val talking = speaksOnPhone
        // Sends her voice each finished sentence as it streams in, and the rest once the reply is complete.
        fun speakFinished(text: String, final: Boolean) {
            if (!talking || spoken >= text.length) return
            val rest = text.substring(spoken)
            if (final) { phoneVoice.say(rest); spoken = text.length; return }
            val end = Regex("(?<=[.!?…:])\\s+").findAll(rest).lastOrNull() ?: return
            phoneVoice.say(rest.substring(0, end.range.first))
            spoken += end.range.last + 1
        }
        /** Streams one model reply into her bubble, after [before] (what she already said this turn). */
        suspend fun stream(message: String, before: String): PhoneAlice.Parsed {
            raw.setLength(0)
            phoneModel.reply(message).collect { piece ->
                raw.append(piece)
                val parsed = PhoneAlice.parse(raw.toString())
                parsed.mood?.let { if (it != mood) mood = it }
                if (parsed.text.isEmpty()) return@collect
                val shownText = "$before ${parsed.text}".trim()
                if (index == -1) {
                    status = ""
                    stage = "speaking"
                    speakStart = 0L
                    items.add(ChatItem(false, shownText, onPhone = true))
                    index = items.lastIndex
                } else if (index in items.indices) {
                    items[index] = items[index].copy(text = shownText)
                }
                if (!talking) speak(shownText.length - shown)
                speakFinished(shownText, final = false)
                shown = shownText.length
            }
            DebugLog.log("phone model wrote: $raw")
            val parsed = PhoneAlice.parse(raw.toString())
            speakFinished("$before ${parsed.text}".trim(), final = true)
            return parsed
        }
        viewModelScope.launch {
            try {
                var links = emptyList<LinkItem>()
                var linksTitle = ""
                var parsed = if (lookup != null) PhoneAlice.Parsed(null, "", null, null, lookup.first, lookup.second) else stream(prompt, "")
                if (lookup != null) DebugLog.log("looking up at once: [${lookup.first}: ${lookup.second}]")
                // The light brain only talks: whatever tag it writes is not done (it adds stray ones to chit-chat).
                if (lookup == null && phoneModel.loadedBrain == "light" && parsed.tag != null) {
                    DebugLog.log("light brain wrote [${parsed.tag}: ${parsed.arg}], ignored")
                    parsed = parsed.copy(tag = null, arg = null, open = null, search = null)
                }
                var reply = parsed.text
                // Facts from the internet are fetched here, then she says what they mean.
                if (parsed.tag in INFO_TAGS) {
                    val arg = parsed.arg.orEmpty()
                    status = when (parsed.tag) {
                        "weather" -> "Looking at the sky…"
                        "currency" -> "Checking today's rate…"
                        "news" -> "Reading the news…"
                        else -> "Checking Steam…"
                    }
                    val found = try {
                        when (parsed.tag) {
                            "weather" -> PhoneInfo.weather(arg)
                            "currency" -> PhoneInfo.currency(arg)
                            "news" -> PhoneInfo.news(arg, prefs.getString("newsEdition", "us")!!)
                            else -> if (arg.trim().equals("wishlist", true)) PhoneInfo.steamWishlist(prefs.getString("steamId", "")!!, prefs.getString("steamCountry", "br")!!)
                                else PhoneInfo.steamGame(arg, prefs.getString("steamCountry", "br")!!)
                        }
                    } catch (e: Exception) {
                        DebugLog.log("${parsed.tag} lookup failed: ${e.message}")
                        PhoneInfo.Found("It failed: the phone couldn't reach the internet (${e.javaClass.simpleName}). Say so, and suggest trying again later.")
                    }
                    val facts = found.text
                    DebugLog.log("${parsed.tag} result: ${facts.take(300)}")
                    val asked = if (lookup != null) "The user asked: $text\n" else ""
                    // The figures are written into her bubble by the app; her model only reacts to them.
                    val followUp = if (found.shown.isNotEmpty())
                        "($asked" + "Result of your [${parsed.tag}: ${parsed.arg}]: $facts\nThe user already sees this, written out: ${found.shown})\n" +
                            "Now add ONE short sentence with your reaction or advice for what they asked, like whether to take an umbrella or whether it's a good deal, " +
                            "starting with a mood tag, with no action tag. Do not write any number, price, percentage, date or amount: those are already shown."
                    else "($asked" + "Result of your [${parsed.tag}: ${parsed.arg}]: $facts)\nNow tell the user what it means for what they asked, in one or two sentences, starting with a mood tag, with no action tag. Avoid numbers."
                    val first = listOf(reply, found.shown).filter { it.isNotEmpty() }.joinToString("\n\n")
                    parsed = stream(followUp, first).copy(tag = null, arg = null, open = null, search = null)
                    reply = "$first ${parsed.text}".trim()
                    status = ""
                    if (found.links.isNotEmpty() && index in items.indices) {
                        links = found.links
                        linksTitle = found.title
                    }
                }
                var did = ""
                parsed.open?.let { name ->
                    val action = PhoneAlice.resolve(name, apps)
                    if (action == null || !perform(action)) reply = (reply + " (I couldn't find $name on your phone, though.)").trim()
                    else did = " (Opened ${(action as? PhoneAlice.Action.OpenApp)?.app?.label ?: name} on the phone.)"
                }
                parsed.search?.let { if (perform(PhoneAlice.Action.Search(it))) did = " (Opened a web search for \"$it\" on the phone.)" }
                val tag = parsed.tag
                if (tag != null && tag != "open" && tag != "search") {
                    val outcome = tools.run(tag, parsed.arg.orEmpty(), text)
                    DebugLog.log("$tag ${parsed.arg}: ${if (outcome.done) "done" else "not done"} ${outcome.note}")
                    // She hears what really happened with the next message, so she never thinks a failed action worked.
                    pendingNote = if (outcome.done) "Your [$tag: ${parsed.arg}] worked." else
                        "Your [$tag: ${parsed.arg}] did NOT work: ${outcome.note.trim('(', ')')} Nothing changed."
                    if (outcome.note.isNotEmpty()) reply = "$reply ${outcome.note}".trim()
                    if (outcome.did.isNotEmpty()) did = " (${outcome.did}, on the phone.)"
                }
                store.add("assistant", "[${parsed.mood ?: "neutral"}] $reply$did")
                if (index in items.indices) items[index] = items[index].copy(text = reply, links = links, linksTitle = linksTitle)
                else if (reply.isNotEmpty()) items.add(ChatItem(false, reply, onPhone = true))
                phoneItems.add(ChatItem(false, reply, onPhone = true))
            } catch (e: Exception) {
                status = ""
                DebugLog.log("phone model failed: ${e.javaClass.simpleName}: ${e.message}")
                addPhoneReply(e.message ?: "Something went wrong on the phone.", isError = true)
            } finally {
                thinking = false
                finishTurn()
            }
        }
    }

    fun answerConfirm(approved: Boolean) {
        val card = confirm ?: return
        confirm = null
        send(JSONObject().put("type", "confirm_reply").put("id", card.id).put("approved", approved))
    }

    private fun send(message: JSONObject) {
        viewModelScope.launch(Dispatchers.IO) {
            try { write(message) } catch (_: IOException) { runCatching { socket?.close() } }
        }
    }

    private suspend fun write(message: JSONObject) = writeLock.withLock {
        val out = writer ?: throw IOException("not connected")
        out.write(message.toString())
        out.write("\n")
        out.flush()
    }

    private fun handle(event: JSONObject) {
        when (val type = event.optString("type")) {
            "delta", "state", "audio" -> Unit
            else -> DebugLog.log("from PC: $type ${event.toString().take(300)}")
        }
        when (event.optString("type")) {
            "state" -> {
                stage = event.optString("state", "listening")
                if (stage == "thinking") { thinking = true; moodRest?.cancel() }
            }
            "delta" -> { status = ""; appendReply(event.optString("text")); if (!voiceOn) speak(event.optString("text").length) }
            "audio" -> playAudio(event)
            "transcript" -> {
                // What the PC heard, shown as if it had been typed.
                items.add(ChatItem(true, event.optString("text")))
                replyIndex = -1
            }
            "outfit" -> wear(event)
            "mood" -> mood = event.optString("mood", "neutral")
            "status", "notice" -> status = event.optString("text", event.optString("message"))
            "confirm" -> confirm = ConfirmCard(event.optString("id"), event.optString("title"), event.optString("detail"))
            "confirm_expired" -> confirm = null
            "links", "games" -> attachLinks(event)
            "phone_action" -> perform(event)
            "error" -> {
                items.add(ChatItem(false, event.optString("message"), isError = true))
                thinking = false
                replyIndex = -1
                stage = "listening"
            }
            "done" -> { thinking = false; replyIndex = -1; status = ""; finishTurn() }
            "sync_ok" -> {
                store.dropSynced(syncing)
                store.dropActions(syncingActions)
                DebugLog.log("PC merged ${event.optInt("added")} of $syncing phone turns and kept ${event.optInt("applied")} of $syncingActions notes or memories")
                syncing = 0
                syncingActions = 0
                if (event.has("memories")) store.setMemories(event.optString("memories"))
                phoneItems.clear()
                if (!thinking) showHistory(event.optJSONArray("history"))
            }
        }
    }

    private fun playAudio(event: JSONObject) {
        if (!voiceOn || !foreground) return
        val pcm = runCatching { android.util.Base64.decode(event.optString("pcm"), android.util.Base64.DEFAULT) }.getOrNull() ?: return
        val raw = event.optJSONArray("levels") ?: JSONArray()
        val levels = FloatArray(raw.length()) { raw.optDouble(it).toFloat() }
        DebugLog.log("voice: %.1fs of speech".format(pcm.size / 2.0 / event.optInt("rate", 24000)))
        voice.play(pcm, event.optInt("rate", 24000), event.optInt("fps", 30), levels)
    }

    /** Text arriving keeps her mouth moving at a reading pace, a little past the last word. */
    private fun speak(chars: Int) {
        if (chars == 0) return
        val now = SystemClock.uptimeMillis()
        if (speakStart == 0L || now > speakingUntil) { speakStart = now; spokenChars = 0 }
        spokenChars += chars
        speakingUntil = speakStart + (spokenChars * MS_PER_CHAR).coerceAtMost(MAX_SPEAKING_MS)
    }

    /** After a reply she settles back to calm, unless she's asked something new first. */
    private fun finishTurn() {
        stage = "listening"
        speakStart = 0L
        moodRest?.cancel()
        moodRest = viewModelScope.launch {
            delay(MOOD_REST_MS + (speakingUntil - SystemClock.uptimeMillis()).coerceAtLeast(0))
            if (!thinking) mood = "neutral"
        }
    }

    private fun appendReply(text: String) {
        if (text.isEmpty()) return
        if (replyIndex !in items.indices || items[replyIndex].fromUser) {
            items.add(ChatItem(false, text.trimStart()))
            replyIndex = items.lastIndex
        } else {
            items[replyIndex] = items[replyIndex].copy(text = items[replyIndex].text + text)
        }
    }

    private fun attachLinks(event: JSONObject) {
        val raw = event.optJSONArray("items") ?: JSONArray()
        val links = (0 until raw.length()).mapNotNull { raw.optJSONObject(it) }.map {
            LinkItem(it.optString("name"), it.optString("url"), it.optString("price", it.optString("detail")))
        }
        if (links.isEmpty()) return
        if (replyIndex !in items.indices) {
            items.add(ChatItem(false, ""))
            replyIndex = items.lastIndex
        }
        items[replyIndex] = items[replyIndex].copy(links = links, linksTitle = event.optString("title"))
    }

    private fun perform(event: JSONObject) {
        when (event.optString("action")) {
            "open_app" -> launch(getApplication<Application>().packageManager.getLaunchIntentForPackage(event.optString("package")))
            "search" -> perform(PhoneAlice.Action.Search(event.optString("query")))
        }
    }

    private fun launch(intent: Intent?): Boolean {
        if (intent == null) { status = "That app can't be opened."; DebugLog.log("launch: no intent"); return false }
        val pm = getApplication<Application>().packageManager
        val handler = intent.resolveActivity(pm)
        DebugLog.log("launch: ${intent.action} ${intent.dataString ?: ""} ${intent.categories ?: ""} -> ${handler?.flattenToShortString() ?: "nothing"}")
        if (handler == null) { status = "Nothing on the phone can open that."; return false }
        return runCatching { getApplication<Application>().startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }
            .onFailure { status = "Couldn't open that: ${it.message}"; DebugLog.log("launch failed: ${it.message}") }
            .isSuccess
    }

    /** The app the phone uses for a kind of job, as picked in its default-apps settings. */
    private fun roleIntent(role: PhoneAlice.Role): Intent = when (role) {
        PhoneAlice.Role.CAMERA -> Intent(android.provider.MediaStore.INTENT_ACTION_STILL_IMAGE_CAMERA)
        PhoneAlice.Role.CLOCK -> Intent(android.provider.AlarmClock.ACTION_SHOW_ALARMS)
        PhoneAlice.Role.SETTINGS -> Intent(android.provider.Settings.ACTION_SETTINGS)
        else -> Intent.makeMainSelectorActivity(
            Intent.ACTION_MAIN,
            when (role) {
                PhoneAlice.Role.MUSIC -> Intent.CATEGORY_APP_MUSIC
                PhoneAlice.Role.MAPS -> Intent.CATEGORY_APP_MAPS
                PhoneAlice.Role.BROWSER -> Intent.CATEGORY_APP_BROWSER
                PhoneAlice.Role.EMAIL -> Intent.CATEGORY_APP_EMAIL
                PhoneAlice.Role.GALLERY -> Intent.CATEGORY_APP_GALLERY
                PhoneAlice.Role.CALENDAR -> Intent.CATEGORY_APP_CALENDAR
                PhoneAlice.Role.CONTACTS -> Intent.CATEGORY_APP_CONTACTS
                PhoneAlice.Role.MESSAGES -> Intent.CATEGORY_APP_MESSAGING
                else -> Intent.CATEGORY_APP_CALCULATOR
            },
        )
    }

    fun openLink(url: String) = launch(Intent(Intent.ACTION_VIEW, Uri.parse(url)))

    /** A search page in the default browser. Android's own web-search request was claimed by several apps on
     * the Fold6, one of which handed Firefox a broken link, so this is a plain link every browser opens. */
    private fun searchIntent(query: String): Intent =
        Intent(Intent.ACTION_VIEW, Uri.parse("https://www.google.com/search?q=" + Uri.encode(query))).addCategory(Intent.CATEGORY_BROWSABLE)

    private fun installedApps(): List<PhoneAlice.App> {
        val pm = getApplication<Application>().packageManager
        val launcher = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        return pm.queryIntentActivities(launcher, 0)
            .map { PhoneAlice.App(it.loadLabel(pm).toString(), it.activityInfo.packageName) }
            .distinctBy { it.pkg }
            .sortedBy { it.label.lowercase() }
    }

    private fun appsJson(): JSONArray {
        if (apps.isEmpty()) apps = installedApps()
        return JSONArray().apply { apps.forEach { put(JSONObject().put("label", it.label).put("package", it.pkg)) } }
    }

    override fun onCleared() {
        voice.stop()
        phoneModel.release()
        runCatching { connectivity?.unregisterNetworkCallback(networkWatcher) }
        disconnect()
    }

    companion object {
        val DEFAULT_HOST: String = BuildConfig.DEFAULT_HOST
        const val DEFAULT_PORT = 7720
        const val CONNECT_TIMEOUT_MS = 4000
        const val HELLO_TIMEOUT_MS = 8000
        const val FIRST_RETRY_MS = 2000L
        const val MAX_RETRY_MS = 30000L
        const val MS_PER_CHAR = 55L
        /** Tags whose facts are fetched from the internet and then put into words by a second reply. */
        val INFO_TAGS = setOf("weather", "currency", "news", "steam")
        const val MAX_SPEAKING_MS = 8000L
        const val MOOD_REST_MS = 30000L
    }
}
