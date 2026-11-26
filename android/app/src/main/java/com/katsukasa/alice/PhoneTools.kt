package com.katsukasa.alice

import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioManager
import android.os.SystemClock
import android.provider.AlarmClock
import android.provider.Settings
import android.view.KeyEvent

/**
 * What her action tags do on the phone, besides opening apps and searches: the clock, notes and memories (kept
 * for the PC, see PhoneStore), settings and the music keys. Android lets an app switch some settings itself;
 * Wi-Fi, mobile data, Bluetooth and airplane mode it doesn't, so for those she opens the switch for the user.
 */
class PhoneTools(private val context: Context, private val store: PhoneStore, private val launch: (Intent) -> Boolean) {
    /** What happened: whether it worked, and a few words to add to her reply when the user has to do something. */
    data class Outcome(val done: Boolean, val note: String = "", val did: String = "")

    /** Whether the flashlight is on, as the camera service reports it. */
    @Volatile private var torchOn = false

    init {
        runCatching {
            context.getSystemService(CameraManager::class.java).registerTorchCallback(object : CameraManager.TorchCallback() {
                override fun onTorchModeChanged(cameraId: String, enabled: Boolean) { torchOn = enabled }
            }, null)
        }
    }

    /** The phone's settings as they really are, told to her with each message, so she never has to guess. */
    fun stateLine(): String {
        val ringer = when (audio.ringerMode) { AudioManager.RINGER_MODE_SILENT -> "silent"; AudioManager.RINGER_MODE_VIBRATE -> "vibrate"; else -> "sound on" }
        val dnd = if (notifications.currentInterruptionFilter > NotificationManager.INTERRUPTION_FILTER_ALL) "on" else "off"
        return "ringer $ringer, Do Not Disturb $dnd, flashlight ${if (torchOn) "on" else "off"}"
    }

    /** [said] is what the user wrote: when her tag's time can't be read (a small model sometimes garbles it,
     * like "101:100" for "10 minutes"), the time is read from the user's own words instead. */
    fun run(tag: String, arg: String, said: String = ""): Outcome = runCatching {
        when (tag) {
            // The user's own words win when they hold a clear time: the model's digits can be mangled yet still readable.
            "alarm" -> alarm(if (clearClock.containsMatchIn(said) || parseAlarm(arg) == null) alarmFrom(said, arg) else arg)
            "timer" -> timer(if (durationWords.containsMatchIn(said) || parseTimer(arg) == null) timerFrom(said, arg) else arg)
            "note" -> { store.addAction("note", arg); Outcome(true, did = "Saved a note: $arg") }
            "remember" -> { store.addAction("remember", arg); store.addMemory(arg); Outcome(true, did = "Remembered: $arg") }
            "setting" -> setting(arg.lowercase().trim())
            "music" -> music(arg.lowercase().trim())
            else -> Outcome(false)
        }
    }.getOrElse {
        DebugLog.log("$tag failed: ${it.message}")
        Outcome(false, "(That didn't work on the phone: ${it.message})")
    }

    private fun alarm(arg: String): Outcome {
        val (hour, minute, label) = parseAlarm(arg) ?: return Outcome(false, "(I couldn't read the time in that.)")
        val intent = Intent(AlarmClock.ACTION_SET_ALARM)
            .putExtra(AlarmClock.EXTRA_HOUR, hour)
            .putExtra(AlarmClock.EXTRA_MINUTES, minute)
            .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
            .apply { if (label.isNotEmpty()) putExtra(AlarmClock.EXTRA_MESSAGE, label) }
        return Outcome(launch(intent), did = "Set an alarm for %02d:%02d %s".format(hour, minute, label).trim())
    }

    private fun timer(arg: String): Outcome {
        val (seconds, label) = parseTimer(arg) ?: return Outcome(false, "(I couldn't read how long that was.)")
        val intent = Intent(AlarmClock.ACTION_SET_TIMER)
            .putExtra(AlarmClock.EXTRA_LENGTH, seconds)
            .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
            .apply { if (label.isNotEmpty()) putExtra(AlarmClock.EXTRA_MESSAGE, label) }
        return Outcome(launch(intent), did = "Started a ${seconds / 60}-minute timer $label".trim())
    }

    private val audio get() = context.getSystemService(AudioManager::class.java)
    private val notifications get() = context.getSystemService(NotificationManager::class.java)

    private fun setting(what: String): Outcome = when {
        "flash" in what || "torch" in what || "lanterna" in what -> torch(!("off" in what || "desliga" in what))
        "wi" in what && "fi" in what -> panel(Settings.Panel.ACTION_WIFI, "Wi-Fi")
        "data" in what || "dados" in what || "internet" in what -> panel(Settings.Panel.ACTION_INTERNET_CONNECTIVITY, "mobile data")
        "bluetooth" in what -> panel(Settings.ACTION_BLUETOOTH_SETTINGS, "Bluetooth")
        "airplane" in what || "avião" in what || "aviao" in what -> panel(Settings.ACTION_AIRPLANE_MODE_SETTINGS, "airplane mode")
        "vibra" in what -> ringer(AudioManager.RINGER_MODE_VIBRATE, "vibrate")
        "silent" in what || "silencio" in what || "silêncio" in what -> ringer(AudioManager.RINGER_MODE_SILENT, "silent")
        "sound" in what || "normal" in what || "ring" in what -> ringer(AudioManager.RINGER_MODE_NORMAL, "sound on")
        "disturb" in what || "dnd" in what || "perturbe" in what -> doNotDisturb(!("off" in what))
        "volume" in what -> volume(if ("down" in what || "lower" in what) AudioManager.ADJUST_LOWER else AudioManager.ADJUST_RAISE)
        "bright" in what || "brilho" in what -> brightness(!("down" in what || "lower" in what))
        else -> Outcome(false, "(I don't know that setting.)")
    }

    private fun torch(on: Boolean): Outcome {
        val camera = context.getSystemService(CameraManager::class.java)
        val id = camera.cameraIdList.firstOrNull { camera.getCameraCharacteristics(it).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true }
            ?: return Outcome(false, "(This phone has no flashlight I can reach.)")
        camera.setTorchMode(id, on)
        return Outcome(true, did = "Flashlight ${if (on) "on" else "off"}")
    }

    /** Android only lets the user flip these, so she opens the switch. */
    private fun panel(action: String, name: String): Outcome =
        Outcome(launch(Intent(action)), "(Android only lets you flip $name yourself, so here's the switch.)", "Opened the $name switch")

    private fun ringer(mode: Int, name: String): Outcome {
        // Silent, and leaving Do Not Disturb, need the user's permission once.
        if (mode == AudioManager.RINGER_MODE_SILENT && !notifications.isNotificationPolicyAccessGranted) return askForQuietPermission()
        return try {
            audio.ringerMode = mode
            Outcome(true, did = "Phone set to $name")
        } catch (e: SecurityException) {
            askForQuietPermission()
        }
    }

    private fun doNotDisturb(on: Boolean): Outcome {
        if (!notifications.isNotificationPolicyAccessGranted) return askForQuietPermission()
        notifications.setInterruptionFilter(if (on) NotificationManager.INTERRUPTION_FILTER_PRIORITY else NotificationManager.INTERRUPTION_FILTER_ALL)
        return Outcome(true, did = "Do Not Disturb ${if (on) "on" else "off"}")
    }

    private fun askForQuietPermission(): Outcome {
        launch(Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS))
        return Outcome(false, "(First I need your permission: allow Alice under Do Not Disturb access, then ask me again.)")
    }

    private fun volume(direction: Int): Outcome {
        repeat(2) { audio.adjustStreamVolume(AudioManager.STREAM_MUSIC, direction, AudioManager.FLAG_SHOW_UI) }
        return Outcome(true, did = "Volume ${if (direction == AudioManager.ADJUST_LOWER) "down" else "up"}")
    }

    private fun brightness(up: Boolean): Outcome {
        if (!Settings.System.canWrite(context)) {
            launch(Intent(Settings.ACTION_MANAGE_WRITE_SETTINGS, android.net.Uri.parse("package:${context.packageName}")))
            return Outcome(false, "(First I need your permission: allow Alice to modify system settings, then ask me again.)")
        }
        val resolver = context.contentResolver
        Settings.System.putInt(resolver, Settings.System.SCREEN_BRIGHTNESS_MODE, Settings.System.SCREEN_BRIGHTNESS_MODE_MANUAL)
        val now = Settings.System.getInt(resolver, Settings.System.SCREEN_BRIGHTNESS, 128)
        Settings.System.putInt(resolver, Settings.System.SCREEN_BRIGHTNESS, (now + if (up) 64 else -64).coerceIn(10, 255))
        return Outcome(true, did = "Brightness ${if (up) "up" else "down"}")
    }

    private fun music(what: String): Outcome {
        val key = when {
            "pause" in what || "stop" in what || "pausa" in what -> KeyEvent.KEYCODE_MEDIA_PAUSE
            "next" in what || "skip" in what || "próxima" in what || "proxima" in what -> KeyEvent.KEYCODE_MEDIA_NEXT
            "prev" in what || "back" in what || "anterior" in what -> KeyEvent.KEYCODE_MEDIA_PREVIOUS
            else -> KeyEvent.KEYCODE_MEDIA_PLAY
        }
        val now = SystemClock.uptimeMillis()
        audio.dispatchMediaKeyEvent(KeyEvent(now, now, KeyEvent.ACTION_DOWN, key, 0))
        audio.dispatchMediaKeyEvent(KeyEvent(now, now, KeyEvent.ACTION_UP, key, 0))
        return Outcome(true, did = "Music: $what")
    }

    companion object {
        private val durationWords = Regex("(\\d+(?:[.,]\\d+)?)\\s*(h|hrs?|hours?|horas?|m|mins?|minutes?|minutos?|s|secs?|seconds?|segundos?)\\b", RegexOption.IGNORE_CASE)
        private val clockWords = Regex("(\\d{1,2})(?:[:h](\\d{2}))?\\s*(am|pm|a\\.m\\.|p\\.m\\.)?", RegexOption.IGNORE_CASE)
        private val garbledTime = Regex("^[\\d:.,\\s]+(?:hours?|horas?|hrs?|h|minutes?|minutos?|mins?|m|seconds?|segundos?|secs?|s)?\\b\\s*", RegexOption.IGNORE_CASE)
        /** A time written so it can't be misread: "6:30", "18h30", "7am", "7 pm". */
        private val clearClock = Regex("\\b\\d{1,2}(?:[:h]\\d{2}(?:\\s*(?:am|pm|a\\.m\\.|p\\.m\\.))?|\\s*(?:am|pm|a\\.m\\.|p\\.m\\.))", RegexOption.IGNORE_CASE)

        /** "remind me in 10 minutes to drink water" -> "10 minutes drink water", keeping her label. */
        fun timerFrom(said: String, arg: String): String {
            val length = durationWords.findAll(said).joinToString(" ") { it.value }
            return "$length ${arg.replace(garbledTime, "").trim()}".trim()
        }

        /** "wake me up at 6:30" -> "6:30 wake me up", keeping her label. */
        fun alarmFrom(said: String, arg: String): String {
            val time = (clearClock.find(said) ?: clockWords.find(said))?.value ?: return arg
            return "$time ${arg.replace(garbledTime, "").trim()}".trim()
        }

        /** "07:00 wake up", "7:30", "18h30 dentist" -> hour, minute, label. */
        fun parseAlarm(arg: String): Triple<Int, Int, String>? {
            val m = Regex("^\\s*(\\d{1,2})(?:[:h.](\\d{2}))?\\s*(am|pm|a\\.m\\.|p\\.m\\.)?\\s*(.*)$", RegexOption.IGNORE_CASE).find(arg) ?: return null
            var hour = m.groupValues[1].toInt()
            val minute = m.groupValues[2].ifEmpty { "0" }.toInt()
            val half = m.groupValues[3].lowercase()
            if (half.startsWith("p") && hour < 12) hour += 12
            if (half.startsWith("a") && hour == 12) hour = 0
            if (hour > 23 || minute > 59) return null
            return Triple(hour, minute, m.groupValues[4].trim())
        }

        private val units = listOf(
            Regex("^(h|hr|hrs|hour|hours|hora|horas)$") to 3600,
            Regex("^(m|min|mins|minute|minutes|minuto|minutos)$") to 60,
            Regex("^(s|sec|secs|second|seconds|segundo|segundos)$") to 1,
        )

        /** "10 minutes tea", "1 hour 30 minutes laundry", "90 seconds", "1h30" -> seconds, label. */
        fun parseTimer(arg: String): Pair<Int, String>? {
            val words = arg.trim().replace(Regex("(\\d)([a-zA-Z])"), "$1 $2").replace(Regex("([a-zA-Z])(\\d)"), "$1 $2").split(Regex("\\s+"))
            var seconds = 0
            var i = 0
            while (i < words.size) {
                val number = words[i].replace(",", ".").toDoubleOrNull() ?: break
                val unit = units.firstOrNull { it.first.matches(words.getOrElse(i + 1) { "" }.lowercase()) }?.second
                if (unit != null) { seconds += (number * unit).toInt(); i += 2 }
                else { seconds += (number * 60).toInt(); i += 1; break }  // A bare number is minutes, as in "1h30".
            }
            if (seconds <= 0) return null
            val label = words.drop(i).joinToString(" ").removePrefix("for ").removePrefix("to ").trim()
            return seconds to label
        }
    }
}
