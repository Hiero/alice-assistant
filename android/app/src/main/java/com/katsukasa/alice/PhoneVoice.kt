package com.katsukasa.alice

import android.content.Context
import com.k2fsa.sherpa.onnx.OfflineTts
import com.k2fsa.sherpa.onnx.OfflineTtsConfig
import com.k2fsa.sherpa.onnx.OfflineTtsKokoroModelConfig
import com.k2fsa.sherpa.onnx.OfflineTtsModelConfig
import java.io.File
import java.util.concurrent.Executors
import kotlin.math.max
import kotlin.math.pow
import kotlin.math.sqrt

/**
 * Her voice without the PC: Kokoro v1.0 on the phone through sherpa-onnx, with the same voice blend, pace
 * and pitch as backend/alice/voice.py, so she sounds the same offline. The model files are copied into the
 * app's files over adb (android/README.md); voices.bin there has her blend as speaker 0.
 * Sentences are made one at a time on their own thread and handed to [VoicePlayer] as they're ready.
 */
class PhoneVoice(context: Context, private val player: VoicePlayer) {
    private val dir = File(context.filesDir, "kokoro")
    private val thread = Executors.newSingleThreadExecutor { Thread(it, "alice-phone-voice") }
    private var tts: OfflineTts? = null
    @Volatile private var generation = 0

    val available: Boolean get() = File(dir, "model.onnx").exists() && File(dir, "voices.bin").exists()

    fun say(sentence: String) {
        val text = speakable(sentence)
        if (!Regex("\\w").containsMatchIn(text) || !available) return
        val mine = generation
        thread.execute {
            if (mine != generation) return@execute
            runCatching { speakNow(text, mine) }.onFailure { DebugLog.log("phone voice failed: ${it.message}") }
        }
    }

    /** Drops sentences not yet made; the player is stopped separately. */
    fun stop() { generation++ }

    /** Frees the model's memory; it loads again for the next sentence. */
    fun release() = thread.execute { tts?.release(); tts = null }

    private fun speakNow(text: String, mine: Int) {
        val engine = tts ?: load()
        val started = System.nanoTime()
        val audio = engine.generate(text, sid = 0, speed = PACE / PITCH)
        if (mine != generation) return
        val rate = audio.sampleRate
        val samples = raisePitch(audio.samples)
        val pcm = toPcm(samples)
        val seconds = samples.size.toFloat() / rate
        DebugLog.log("phone voice: %.1fs of speech made in %.2fs".format(seconds, (System.nanoTime() - started) / 1e9))
        player.play(pcm, rate, MOUTH_FPS, mouthLevels(samples, rate))
    }

    private fun load(): OfflineTts {
        val started = System.nanoTime()
        val kokoro = OfflineTtsKokoroModelConfig(
            model = File(dir, "model.onnx").path,
            voices = File(dir, "voices.bin").path,
            tokens = File(dir, "tokens.txt").path,
            dataDir = File(dir, "espeak-ng-data").path,
            lexicon = File(dir, "lexicon-us-en.txt").path,
        )
        val config = OfflineTtsConfig(model = OfflineTtsModelConfig(kokoro = kokoro, numThreads = THREADS, debug = false, provider = "cpu"))
        return OfflineTts(null, config).also {
            tts = it
            DebugLog.log("phone voice loaded in %.1fs".format((System.nanoTime() - started) / 1e9))
        }
    }

    companion object {
        // From backend/alice/voice.py: playing the sound back faster raises pitch and resonance, and the slower
        // speaking speed offsets it. The blend (ALICE_VOICE) is baked into voices.bin.
        const val PITCH = 1.2f
        const val PACE = 1.03f
        const val MOUTH_FPS = 30
        private const val THREADS = 4  // 6 was slower: more threads fight over the cores

        fun speakable(text: String): String =
            text.replace(Regex("https?://\\S+"), "").replace(Regex("[*_#`~\\[\\]<>|]"), "").trim()

        /** Samples read [PITCH] times faster, by linear interpolation, then scaled to a peak of 0.9. */
        fun raisePitch(samples: FloatArray): FloatArray {
            val count = (samples.size / PITCH).toInt()
            val out = FloatArray(count)
            var peak = 1e-6f
            for (i in 0 until count) {
                val at = i * PITCH
                val j = at.toInt()
                val frac = at - j
                val a = samples[j]
                val b = if (j + 1 < samples.size) samples[j + 1] else a
                out[i] = a + (b - a) * frac
                peak = max(peak, kotlin.math.abs(out[i]))
            }
            val scale = 0.9f / peak
            for (i in out.indices) out[i] *= scale
            return out
        }

        fun toPcm(samples: FloatArray): ByteArray {
            val bytes = ByteArray(samples.size * 2)
            for (i in samples.indices) {
                val v = (samples[i].coerceIn(-1f, 1f) * 32767).toInt()
                bytes[2 * i] = (v and 0xFF).toByte()
                bytes[2 * i + 1] = (v shr 8 and 0xFF).toByte()
            }
            return bytes
        }

        /** How open her mouth is for each 1/30 s, the same formula as mouth_levels() on the PC. */
        fun mouthLevels(samples: FloatArray, rate: Int): FloatArray {
            val frame = rate / MOUTH_FPS
            val count = samples.size / frame
            return FloatArray(count) { k ->
                var sum = 0f
                for (i in k * frame until (k + 1) * frame) sum += samples[i] * samples[i]
                val rms = sqrt(sum / frame)
                ((rms - 0.015f) / 0.17f).coerceIn(0f, 1f).pow(0.7f)
            }
        }
    }
}
