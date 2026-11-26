package com.katsukasa.alice

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import kotlin.concurrent.thread
import kotlin.math.max
import kotlin.math.sqrt

/**
 * Talking to her. With the PC in reach, the phone only records, and the PC's Whisper writes it down, as for the
 * desktop's voice turns ([record], the same stopping rules as record() in backend/alice/voice.py). Without it,
 * Android's own on-device recogniser does it ([recognise]), choosing between English and Portuguese.
 */
class VoiceInput(private val context: Context) {
    @Volatile private var stopRequested = false
    private var askedForPacks = false
    private var recogniser: SpeechRecognizer? = null

    /** Records until the speaker goes quiet, [stop] is called, or 30 s pass. Calls [done] on a worker thread
     * with 16 kHz mono 16-bit sound, or an empty array when nothing was said. */
    @SuppressLint("MissingPermission")  // Asked for before this is called.
    fun record(done: (ByteArray) -> Unit) {
        stopRequested = false
        thread(name = "alice-mic") {
            val chunk = RATE / 10  // 100 ms
            val recorder = AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, max(AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT), chunk * 4))
            val out = java.io.ByteArrayOutputStream()
            val levels = mutableListOf<Float>()
            var heardSpeech = false
            var quiet = 0
            var chunks = 0
            val buffer = ShortArray(chunk)
            try {
                recorder.startRecording()
                while (!stopRequested && chunks < MAX_CHUNKS) {
                    val n = recorder.read(buffer, 0, chunk)
                    if (n <= 0) break
                    chunks++
                    for (i in 0 until n) { out.write(buffer[i].toInt() and 0xFF); out.write(buffer[i].toInt() shr 8 and 0xFF) }
                    var sum = 0.0
                    for (i in 0 until n) { val v = buffer[i] / 32768.0; sum += v * v }
                    val level = sqrt(sum / n).toFloat()
                    // The first half second measures the room; speech is anything well above it.
                    if (levels.size < NOISE_CHUNKS) { levels.add(level); continue }
                    if (level > max(levels.sorted()[levels.size / 2] * 3, MIN_SPEECH_LEVEL)) { heardSpeech = true; quiet = 0 } else quiet++
                    if (heardSpeech && quiet >= END_SILENCE_CHUNKS) break
                    if (!heardSpeech && chunks >= NO_SPEECH_CHUNKS) { out.reset(); break }
                }
            } catch (e: Exception) {
                DebugLog.log("microphone failed: ${e.message}")
                out.reset()
            } finally {
                runCatching { recorder.stop() }
                recorder.release()
            }
            DebugLog.log("recorded %.1fs, speech heard: $heardSpeech".format(out.size() / 2.0 / RATE))
            done(if (heardSpeech) out.toByteArray() else ByteArray(0))
        }
    }

    fun stop() {
        stopRequested = true
        recogniser?.stopListening()
    }

    val canRecogniseOffline: Boolean get() = SpeechRecognizer.isOnDeviceRecognitionAvailable(context)

    /** Android's on-device recogniser, for when the PC is out of reach. Must be called on the main thread;
     * [done] gets the text, or "" when nothing was understood, and [failed] a reason it couldn't listen. */
    fun recognise(done: (String) -> Unit, failed: (String) -> Unit) {
        if (!canRecogniseOffline) { failed("This phone can't recognise speech offline."); return }
        recogniser?.destroy()
        val r = SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
        recogniser = r
        // Which offline speech packs are on the phone decides the language: asking for one that isn't downloaded
        // fails with error 13 (the Fold6, set to UK English, had no US English pack).
        val probe = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
        r.checkRecognitionSupport(probe, context.mainExecutor, object : android.speech.RecognitionSupportCallback {
            override fun onSupportResult(support: android.speech.RecognitionSupport) {
                val installed = support.installedOnDeviceLanguages
                DebugLog.log("offline speech packs installed: $installed; downloading: ${support.pendingOnDeviceLanguages}; available: ${support.supportedOnDeviceLanguages.size}")
                val usable = WANTED.filter { want -> installed.any { it.equals(want, ignoreCase = true) } }
                if (usable.isEmpty()) {
                    // None yet: ask Android to fetch English and Portuguese, which it does in the background.
                    for (language in DOWNLOAD) {
                        if (support.supportedOnDeviceLanguages.any { it.equals(language, true) } && support.pendingOnDeviceLanguages.none { it.equals(language, true) })
                            runCatching { r.triggerModelDownload(Intent(probe).putExtra(RecognizerIntent.EXTRA_LANGUAGE, language)) }
                    }
                    r.destroy(); if (recogniser === r) recogniser = null
                    failed("I'm fetching the offline speech packs for English and Portuguese now. Try again in a minute or two.")
                    return
                }
                // Some installed, but not Portuguese or English: fetch the missing one for next time, once per run.
                if (!askedForPacks) {
                    askedForPacks = true
                    for (language in DOWNLOAD) {
                        val have = installed.any { it.equals(language, true) || (language == "en-US" && it.equals("en-GB", true)) }
                        if (!have && support.supportedOnDeviceLanguages.any { it.equals(language, true) } && support.pendingOnDeviceLanguages.none { it.equals(language, true) }) {
                            DebugLog.log("asking Android to download the offline speech pack for $language")
                            runCatching { r.triggerModelDownload(Intent(probe).putExtra(RecognizerIntent.EXTRA_LANGUAGE, language)) }
                        }
                    }
                }
                listen(r, usable, done, failed)
            }
            override fun onError(error: Int) {
                DebugLog.log("couldn't check offline speech packs (error $error); trying anyway")
                listen(r, listOf(java.util.Locale.getDefault().toLanguageTag()), done, failed)
            }
        })
    }

    private fun listen(r: SpeechRecognizer, languages: List<String>, done: (String) -> Unit, failed: (String) -> Unit) {
        r.setRecognitionListener(object : RecognitionListener {
            override fun onResults(results: Bundle?) {
                val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty()
                DebugLog.log("on-device recogniser heard: \"$text\"")
                done(text)
                r.destroy(); if (recogniser === r) recogniser = null
            }
            override fun onError(error: Int) {
                DebugLog.log("on-device recogniser error $error")
                if (error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT) done("")
                else failed(if (error == SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE || error == SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED)
                    "The offline speech pack for this language isn't installed." else "I couldn't listen just now (error $error).")
                r.destroy(); if (recogniser === r) recogniser = null
            }
            override fun onReadyForSpeech(params: Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}
            override fun onPartialResults(partialResults: Bundle?) {}
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })
        DebugLog.log("listening offline in ${languages.joinToString()}")
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE, languages.first())
        // With English and Portuguese both installed, the recogniser picks between them as you speak.
        if (languages.size > 1) intent.putExtra(RecognizerIntent.EXTRA_ENABLE_LANGUAGE_DETECTION, true)
            .putStringArrayListExtra(RecognizerIntent.EXTRA_LANGUAGE_DETECTION_ALLOWED_LANGUAGES, ArrayList(languages))
        r.startListening(intent)
    }

    companion object {
        const val RATE = 16000
        /** Languages to listen in, best first, among the installed offline packs; and the ones to fetch if none is. */
        private val WANTED = listOf("en-US", "en-GB", "pt-BR")
        private val DOWNLOAD = listOf("en-US", "pt-BR")
        // From backend/alice/voice.py's record().
        private const val NOISE_CHUNKS = 5
        private const val MIN_SPEECH_LEVEL = 0.012f
        private const val END_SILENCE_CHUNKS = 8
        private const val NO_SPEECH_CHUNKS = 60
        private const val MAX_CHUNKS = 300
    }
}
