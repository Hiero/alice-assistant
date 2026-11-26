package io.github.hiero.alice

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * What the phone keeps between runs:
 * - the turns said to her on the phone that the PC hasn't heard about yet, sent with the next connection;
 * - what the PC last told her (her memories of the user and the latest turns), for her model offline.
 * Both live in the app's private files and are written whole, so a crash can't leave half a file.
 */
class PhoneStore(context: Context) {
    data class Turn(val role: String, val text: String, val time: Long)
    data class Offline(
        val memories: String, val context: List<Turn>, val birthdays: List<Occasions.Birthday> = emptyList(),
        val worn: String = "", val greetings: Map<String, String> = emptyMap(),
    )

    private val pendingFile = File(context.filesDir, "phone-turns.json")
    private val offlineFile = File(context.filesDir, "offline-context.json")
    private val actionsFile = File(context.filesDir, "phone-actions.json")

    /** Notes and memories made on the phone, for the PC to keep: kind "note" or "remember". */
    data class Action(val kind: String, val text: String, val time: Long)

    @Synchronized fun actions(): List<Action> = runCatching {
        val array = JSONArray(actionsFile.readText())
        (0 until array.length()).map { array.getJSONObject(it) }.map { Action(it.getString("kind"), it.getString("text"), it.getLong("time")) }
    }.getOrDefault(emptyList())

    @Synchronized fun addAction(kind: String, text: String) = writeActions(actions() + Action(kind, text, System.currentTimeMillis()))

    @Synchronized fun dropActions(count: Int) = writeActions(actions().drop(count))

    private fun writeActions(list: List<Action>) {
        val array = JSONArray()
        list.takeLast(MAX_PENDING).forEach { array.put(JSONObject().put("kind", it.kind).put("text", it.text).put("time", it.time)) }
        write(actionsFile, array.toString())
    }

    fun actionsJson(list: List<Action>): JSONArray = JSONArray().apply {
        list.forEach { put(JSONObject().put("kind", it.kind).put("text", it.text).put("time", it.time)) }
    }

    /** Her memories as the PC now has them, after a sync merged the phone's. */
    @Synchronized fun setMemories(memories: String) {
        val json = runCatching { JSONObject(offlineFile.readText()) }.getOrDefault(JSONObject())
        write(offlineFile, json.put("memories", memories).toString())
    }

    /** A memory made offline counts at once, before the PC has it. */
    @Synchronized fun addMemory(fact: String) {
        val now = offline()
        val memories = (now.memories.trim().takeIf { it != "(none yet)" }.orEmpty() + "\n- $fact").trim()
        val json = runCatching { JSONObject(offlineFile.readText()) }.getOrDefault(JSONObject())
        write(offlineFile, json.put("memories", memories).toString())
    }

    @Synchronized fun pending(): List<Turn> = runCatching {
        val array = JSONArray(pendingFile.readText())
        (0 until array.length()).map { array.getJSONObject(it) }.map { Turn(it.getString("role"), it.getString("text"), it.getLong("time")) }
    }.getOrDefault(emptyList())

    @Synchronized fun add(role: String, text: String) {
        val turns = pending() + Turn(role, text, System.currentTimeMillis())
        writePending(turns.takeLast(MAX_PENDING))
    }

    /** Drops the first [count] turns, the ones the PC confirmed; any added since stay for the next sync. */
    @Synchronized fun dropSynced(count: Int) = writePending(pending().drop(count))

    private fun writePending(turns: List<Turn>) {
        val array = JSONArray()
        turns.forEach { array.put(JSONObject().put("role", it.role).put("text", it.text).put("time", it.time)) }
        write(pendingFile, array.toString())
    }

    fun pendingJson(turns: List<Turn>): JSONArray = JSONArray().apply {
        turns.forEach { put(JSONObject().put("role", it.role).put("text", it.text).put("time", it.time)) }
    }

    @Synchronized fun saveOffline(memories: String, context: JSONArray?, birthdays: JSONArray? = null, worn: String = "", greetings: JSONObject? = null) {
        val json = JSONObject().put("memories", memories).put("context", context ?: JSONArray())
            .put("birthdays", birthdays ?: JSONArray()).put("worn", worn).put("greetings", greetings ?: JSONObject())
        write(offlineFile, json.toString())
    }

    @Synchronized fun offline(): Offline = runCatching {
        val json = JSONObject(offlineFile.readText())
        val array = json.optJSONArray("context") ?: JSONArray()
        val turns = (0 until array.length()).map { array.getJSONObject(it) }.map { Turn(it.getString("role"), it.getString("text"), 0) }
        val days = json.optJSONArray("birthdays") ?: JSONArray()
        val birthdays = (0 until days.length()).mapNotNull { days.optJSONObject(it) }.map {
            Occasions.Birthday(it.optString("person"), it.optInt("month"), it.optInt("day"))
        }
        val words = json.optJSONObject("greetings") ?: JSONObject()
        Offline(json.optString("memories"), turns, birthdays, json.optString("worn"), words.keys().asSequence().associateWith { words.optString(it) })
    }.getOrDefault(Offline("", emptyList()))

    private fun write(file: File, text: String) {
        val temporary = File(file.parentFile, file.name + ".tmp")
        temporary.writeText(text)
        temporary.renameTo(file)
    }

    companion object {
        private const val MAX_PENDING = 80
    }
}
