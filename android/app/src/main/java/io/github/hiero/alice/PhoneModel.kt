package io.github.hiero.alice

import android.content.Context
import android.util.Log
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.Contents
import com.google.ai.edge.litertlm.Conversation
import com.google.ai.edge.litertlm.ConversationConfig
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import com.google.ai.edge.litertlm.Message
import com.google.ai.edge.litertlm.SamplerConfig
import com.google.ai.edge.litertlm.ThinkingConfig
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.Executors
import kotlin.random.Random

/**
 * Alice's own small model on the phone (Gemma 4 E4B through LiteRT-LM), for when the PC can't be reached.
 * The model file is too big for the APK, so it is copied into the app's files over adb; see android/README.md.
 * One thread owns the engine and the conversation, as LiteRT-LM requires.
 */
class PhoneModel(
    private val context: Context, private val store: PhoneStore, private val outfit: () -> String = { "" },
    /** "gpu32" (the GPU in full 32-bit precision, the default), "gpu" (16-bit: faster, but it garbles numbers) or "cpu". */
    private val setup: () -> String = { "gpu" },
    /** Which brain to run: "full" (Gemma 4 E4B) or "light" (E2B, which only talks; see offline-prompt-light.txt). */
    private val brain: () -> String = { "full" },
) {
    enum class State { MISSING, IDLE, LOADING, READY, FAILED }

    var state by mutableStateOf(State.IDLE); private set
    var backendName by mutableStateOf(""); private set

    val file = File(context.filesDir, MODEL_FILE)
    val lightFile = File(context.filesDir, LIGHT_FILE)
    /** The brain that's loaded, or about to be. */
    var loadedBrain by mutableStateOf(""); private set

    private val thread = Executors.newSingleThreadExecutor { Thread(it, "alice-model") }
    private val dispatcher = thread.asCoroutineDispatcher()
    private val scope = CoroutineScope(SupervisorJob() + dispatcher)
    private val lock = Mutex()
    private var engine: Engine? = null
    private var conversation: Conversation? = null
    private var turns = 0
    private var lastTurnAt = 0L
    private var idleRelease: kotlinx.coroutines.Job? = null
    private fun prompt(light: Boolean) = context.assets.open(if (light) "offline-prompt-light.txt" else "offline-prompt.txt").bufferedReader().use { it.readText().trim() }

    init {
        if (!file.exists()) state = State.MISSING
    }

    /** Whether she can use it: the file is there (it may have been copied in since) and it didn't fail to load. */
    val available: Boolean get() = file.exists() && state != State.FAILED

    /** Her reply to [text], streamed in pieces as the model writes it. */
    fun reply(text: String): Flow<String> = flow {
        idleRelease?.cancel()
        lock.withLock {
            ensureLoaded()
            val started = System.nanoTime()
            var first = 0L
            var chars = 0
            // A failed reply (a full context, say) leaves the conversation unusable, so it's dropped; if nothing
            // was said yet, the message is tried once more in a fresh one.
            for (attempt in 1..2) {
                try {
                    freshConversationIfStale().sendMessageAsync(text).collect {
                        if (first == 0L) first = System.nanoTime()
                        val piece = it.toString()
                        chars += piece.length
                        emit(piece)
                    }
                    break
                } catch (e: Exception) {
                    if (e is kotlinx.coroutines.CancellationException) throw e
                    DebugLog.log("phone conversation failed (${e.message}), starting a fresh one")
                    runCatching { conversation?.close() }
                    conversation = null
                    if (attempt == 2 || chars > 0) throw e
                }
            }
            val done = System.nanoTime()
            val timing = "reply on $backendName: first words after %.2fs, %d chars in %.2fs".format((first - started) / 1e9, chars, (done - started) / 1e9)
            Log.i(TAG, timing)
            DebugLog.log(timing)
            turns++
            lastTurnAt = System.currentTimeMillis()
        }
        // The model holds about 4 GB of the phone's memory, so it's let go after a while unused.
        idleRelease = scope.launch {
            kotlinx.coroutines.delay(IDLE_RELEASE_MS)
            DebugLog.log("phone model idle for ${IDLE_RELEASE_MS / 60000} min, releasing it")
            releaseNow()
        }
    }.flowOn(dispatcher)

    private suspend fun ensureLoaded() {
        // The brain to use can change between replies (Auto turns light when the phone is warm); switching reloads.
        val wanted = if (brain() == "light" && lightFile.exists()) "light" else "full"
        if (engine != null && wanted == loadedBrain) return
        if (engine != null) {
            DebugLog.log("switching her phone brain from $loadedBrain to $wanted")
            conversation?.close(); conversation = null
            engine?.close(); engine = null
        }
        withContext(Dispatchers.Main) { loadedBrain = wanted }
        val file = if (wanted == "light") lightFile else file
        if (!file.exists()) {
            setState(State.MISSING)
            throw IllegalStateException("Her phone model isn't installed yet.")
        }
        setState(State.LOADING)
        val started = System.nanoTime()
        // The GPU is several times faster; the CPU is the fallback if this phone's GPU can't run it.
        val chosen = setup()
        val order = if (chosen == "cpu") listOf<Backend>(Backend.CPU()) else listOf(Backend.GPU(), Backend.CPU())
        for (backend in order) {
            try {
                val precision = if (chosen == "gpu32" && backend is Backend.GPU) com.google.ai.edge.litertlm.ActivationDataType.FLOAT32 else null
                val config = EngineConfig(modelPath = file.absolutePath, backend = backend, maxNumTokens = MAX_TOKENS, cacheDir = context.cacheDir.path, activationDataType = precision)
                engine = Engine(config).also { it.initialize() }
                withContext(Dispatchers.Main) { backendName = if (backend is Backend.GPU) "GPU" else "CPU" }
                DebugLog.log("phone model ($wanted) loaded on ${backend.name}${if (precision != null) " (float32)" else ""} in %.1fs".format((System.nanoTime() - started) / 1e9))
                setState(State.READY)
                return
            } catch (e: Exception) {
                Log.w(TAG, "loading on ${backend.name} failed", e)
                DebugLog.log("phone model failed to load on ${backend.name}: ${e.message}")
            }
        }
        setState(State.FAILED)
        throw IllegalStateException("Her phone model couldn't be loaded.")
    }

    /** Short conversations keep replies quick, and a pause of half an hour means a new day's chat anyway. */
    /** The next reply starts a new conversation, so a change (like her outfit) takes effect. */
    fun newConversation() { lastTurnAt = 0L }

    private fun freshConversationIfStale(): Conversation {
        val stale = turns >= MAX_TURNS || System.currentTimeMillis() - lastTurnAt > IDLE_RESET_MS
        conversation?.takeIf { !stale }?.let { return it }
        conversation?.close()
        conversation = null
        turns = 0
        // What the PC last told her: her memories of the user and how the conversation ended, so she
        // picks up where she left off instead of meeting the user for the first time.
        val offline = store.offline()
        val memories = offline.memories.trim().takeIf { it.isNotEmpty() && it != "(none yet)" }
        // Her outfit, as on the PC: Queen and Evil Alice bring their own manner; the others are only mentioned.
        val wearing = when (val name = outfit()) {
            "" -> ""
            "evil" -> "\n\n" + context.assets.open("offline-evil.txt").bufferedReader().use { it.readText().trim() }
            "queen" -> "\n\n" + context.assets.open("offline-queen.txt").bufferedReader().use { it.readText().trim() }
            else -> "\n\nRight now you are wearing your ${name.replace('_', ' ')} outfit."
        }
        val base = prompt(loadedBrain == "light") + wearing
        val instructions = if (memories == null) base else "$base\n\nWhat you remember about the user:\n$memories"
        val earlier = alternating(offline.context)
        DebugLog.log("new phone conversation: ${earlier.size} earlier messages, ${if (memories == null) "no" else memories.lines().size} memories")
        val config = ConversationConfig(
            systemInstruction = Contents.of(instructions),
            initialMessages = earlier.map { if (it.role == "user") Message.user(it.text) else Message.model(it.text) },
            samplerConfig = SamplerConfig(topK = 40, topP = 0.95, temperature = 0.8, seed = Random.nextInt()),
            thinkingConfig = ThinkingConfig(false),
        )
        return checkNotNull(engine).createConversation(config).also { conversation = it }
    }

    /** Gemma wants turns to alternate, starting with the user and ending with her reply. */
    private fun alternating(turns: List<PhoneStore.Turn>): List<PhoneStore.Turn> {
        val out = mutableListOf<PhoneStore.Turn>()
        for (turn in turns) {
            if (out.isEmpty() && turn.role != "user") continue
            if (out.isNotEmpty() && out.last().role == turn.role) out[out.lastIndex] = turn else out.add(turn)
        }
        if (out.isNotEmpty() && out.last().role == "user") out.removeAt(out.lastIndex)
        return out
    }

    private suspend fun setState(next: State) = withContext(Dispatchers.Main) { state = next }

    /** Frees the memory it holds; it loads again on the next reply. */
    fun release() {
        idleRelease?.cancel()
        scope.launch { releaseNow() }
    }

    private suspend fun releaseNow() = lock.withLock {
        if (engine == null) return@withLock
        conversation?.close()
        conversation = null
        engine?.close()
        engine = null
        DebugLog.log("phone model released")
        setState(if (file.exists()) State.IDLE else State.MISSING)
    }

    companion object {
        const val MODEL_FILE = "alice-phone.litertlm"
        const val LIGHT_FILE = "alice-phone-light.litertlm"
        private const val TAG = "AlicePhoneModel"
        private const val MAX_TOKENS = 4096
        private const val MAX_TURNS = 12
        private const val IDLE_RESET_MS = 30 * 60 * 1000L
        private const val IDLE_RELEASE_MS = 5 * 60 * 1000L
    }
}
