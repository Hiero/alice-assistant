package com.katsukasa.alice

import android.content.Context
import android.util.Log
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

/**
 * What she was asked, what her model wrote, what she did and how long it took, like ALICE_DEBUG on the PC.
 * It holds whole conversations, so it stays in the app's private files, rotated to stay small, and is
 * switched off in Setup. Read it over adb:
 *
 *     adb shell run-as com.katsukasa.alice cat files/debug.log
 */
object DebugLog {
    private const val TAG = "Alice"
    private const val MAX_BYTES = 512 * 1024
    private val writer = Executors.newSingleThreadExecutor { Thread(it, "alice-debug-log") }
    private val stamp = SimpleDateFormat("MM-dd HH:mm:ss.SSS", Locale.US)
    private var file: File? = null
    @Volatile var enabled = false

    fun init(context: Context, on: Boolean) {
        file = File(context.filesDir, "debug.log")
        enabled = on
    }

    fun log(message: String) {
        if (!enabled) return
        Log.d(TAG, message)
        val line = "${stamp.format(Date())} $message\n"
        val target = file ?: return
        writer.execute {
            runCatching {
                if (target.length() > MAX_BYTES) target.renameTo(File(target.parentFile, "debug.log.1"))
                target.appendText(line)
            }
        }
    }

    fun clear() {
        val target = file ?: return
        writer.execute { target.delete(); File(target.parentFile, "debug.log.1").delete() }
    }
}
