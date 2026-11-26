package com.katsukasa.alice

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import java.util.concurrent.LinkedBlockingQueue
import kotlin.concurrent.thread

/**
 * Plays her voice: sentences of 16-bit mono sound, one after another, as they arrive. Music and other
 * sounds are turned down while she talks, as the PC does with Jellyfin.
 *
 * [mouthLevel] says how open her mouth should be right now, from the loudness the sound came with, at the
 * spot the speaker has actually reached, so her lips follow what you hear.
 */
class VoicePlayer(context: Context) {
    private class Sentence(val pcm: ByteArray, val rate: Int, val fps: Int, val levels: FloatArray)
    private class Playing(val startFrame: Long, val frames: Long, val fps: Int, val rate: Int, val levels: FloatArray)

    private val audio = context.getSystemService(AudioManager::class.java)
    private val attributes = AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_ASSISTANT)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
        .build()
    private val focus = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK).setAudioAttributes(attributes).build()

    private val queue = LinkedBlockingQueue<Sentence>()
    private val playing = ArrayDeque<Playing>()
    @Volatile private var track: AudioTrack? = null
    @Volatile private var trackRate = 0
    @Volatile private var written = 0L  // Frames given to the track since it was made.
    @Volatile private var generation = 0

    init {
        thread(name = "alice-voice", isDaemon = true) { loop() }
    }

    fun play(pcm: ByteArray, rate: Int, fps: Int, levels: FloatArray) {
        queue.put(Sentence(pcm, rate, fps, levels))
    }

    /** Stops at once, dropping what hasn't been heard yet. */
    fun stop() {
        generation++
        queue.clear()
        synchronized(playing) { playing.clear() }
        track?.let { runCatching { it.pause(); it.flush() } }
        written = 0
        track?.let { runCatching { it.release() } }
        track = null
        audio.abandonAudioFocusRequest(focus)
    }

    /** Whether there is sound playing or waiting to. */
    val speaking: Boolean get() = queue.isNotEmpty() || mouthLevel() >= 0f

    /** How open her mouth is at the moment you're hearing, or -1 when she isn't talking. */
    fun mouthLevel(): Float {
        val current = track ?: return -1f
        val heard = runCatching { current.playbackHeadPosition.toLong() and 0xFFFFFFFFL }.getOrDefault(0L)
        synchronized(playing) {
            while (playing.isNotEmpty() && heard >= playing.first().startFrame + playing.first().frames) playing.removeFirst()
            val now = playing.firstOrNull() ?: return -1f
            if (heard < now.startFrame) return 0f
            val index = ((heard - now.startFrame) * now.fps / now.rate).toInt()
            return now.levels.getOrElse(index) { 0f }
        }
    }

    private fun loop() {
        while (true) {
            val sentence = queue.take()
            // stop() can release the track mid-write; that sentence is simply dropped.
            runCatching { playOne(sentence) }
        }
    }

    private fun playOne(sentence: Sentence) {
        run {
            val mine = generation
            val out = trackFor(sentence.rate)
            if (playing.isEmpty()) audio.requestAudioFocus(focus)
            val frames = sentence.pcm.size / 2L
            synchronized(playing) { playing.addLast(Playing(written, frames, sentence.fps, sentence.rate, sentence.levels)) }
            written += frames
            var offset = 0
            // Blocking writes keep only a little sound queued ahead, so stop() takes effect quickly.
            while (offset < sentence.pcm.size && mine == generation) {
                val n = out.write(sentence.pcm, offset, minOf(8192, sentence.pcm.size - offset))
                if (n <= 0) break
                offset += n
            }
            if (queue.isEmpty() && mine == generation) releaseFocusWhenDone(mine)
        }
    }

    /** Gives the sound back to other apps once the last sentence has been heard. */
    private fun releaseFocusWhenDone(mine: Int) = thread(isDaemon = true) {
        while (mine == generation && queue.isEmpty() && mouthLevel() >= 0f) Thread.sleep(100)
        if (mine == generation && queue.isEmpty()) audio.abandonAudioFocusRequest(focus)
    }

    private fun trackFor(rate: Int): AudioTrack {
        track?.takeIf { trackRate == rate }?.let { return it }
        track?.release()
        val size = AudioTrack.getMinBufferSize(rate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT) * 2
        val made = AudioTrack.Builder()
            .setAudioAttributes(attributes)
            .setAudioFormat(AudioFormat.Builder().setSampleRate(rate).setChannelMask(AudioFormat.CHANNEL_OUT_MONO).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
            .setBufferSizeInBytes(size)
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        made.play()
        track = made
        trackRate = rate
        written = 0
        return made
    }
}
