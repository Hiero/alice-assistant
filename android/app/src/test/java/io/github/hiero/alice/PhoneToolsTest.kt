package io.github.hiero.alice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

class PhoneToolsTest {
    @Test fun readsAlarmTimes() {
        assertEquals(Triple(7, 0, "wake up"), PhoneTools.parseAlarm("07:00 wake up"))
        assertEquals(Triple(18, 30, ""), PhoneTools.parseAlarm("18:30"))
        assertEquals(Triple(15, 0, "call the dentist"), PhoneTools.parseAlarm("3pm call the dentist"))
        assertEquals(Triple(6, 30, "acordar"), PhoneTools.parseAlarm("6h30 acordar"))
        assertEquals(Triple(0, 15, ""), PhoneTools.parseAlarm("12:15 am"))
        assertNull(PhoneTools.parseAlarm("25:00"))
        assertNull(PhoneTools.parseAlarm("tomorrow morning"))
    }

    @Test fun readsTimerLengths() {
        assertEquals(600 to "tea", PhoneTools.parseTimer("10 minutes tea"))
        assertEquals(5400 to "laundry", PhoneTools.parseTimer("1 hour 30 minutes laundry"))
        assertEquals(5400 to "", PhoneTools.parseTimer("1h30"))
        assertEquals(90 to "", PhoneTools.parseTimer("90 seconds"))
        assertEquals(300 to "pro miojo", PhoneTools.parseTimer("5 minutos pro miojo"))
        assertEquals(1200 to "take the pizza out", PhoneTools.parseTimer("20 min to take the pizza out"))
        assertNull(PhoneTools.parseTimer("a while"))
    }

    @Test fun knowsTheHolidays() {
        assertEquals(LocalDate.of(2026, 4, 5), Occasions.easter(2026))
        assertEquals(LocalDate.of(2027, 3, 28), Occasions.easter(2027))
        val days = Occasions.holidays(2026)
        assertEquals("carnival", days[LocalDate.of(2026, 2, 17)])
        assertEquals("mothers", days[LocalDate.of(2026, 5, 10)])
        assertEquals("fathers", days[LocalDate.of(2026, 8, 9)])
        assertEquals("christmas", Occasions.outfit(LocalDate.of(2026, 12, 25), ""))
        assertEquals("queen", Occasions.outfit(LocalDate.of(2026, 12, 25), "queen"))
        assertEquals("", Occasions.outfit(LocalDate.of(2026, 11, 18), ""))
    }

    @Test fun greetsBirthdays() {
        val words = mapOf("user_birthday" to "Happy birthday!", "other_birthday" to "Today is {names}'s birthday!", "christmas" to "Merry Christmas!")
        val birthdays = listOf(Occasions.Birthday("user", 12, 25), Occasions.Birthday("Ana", 2, 29))
        val (text, hat) = Occasions.greeting(LocalDate.of(2026, 12, 25), birthdays, words)
        assertEquals("Happy birthday! Merry Christmas!", text)
        assertTrue(hat)
        assertEquals("Today is Ana's birthday!" to true, Occasions.greeting(LocalDate.of(2027, 2, 28), birthdays, words))
        assertFalse(Occasions.greeting(LocalDate.of(2026, 3, 3), birthdays, words).second)
    }

    @Test fun readsNewTags() {
        val p = PhoneAlice.parse("[sleepy] Seven o'clock it is. [alarm: 07:00 wake up]")
        assertEquals("alarm", p.tag)
        assertEquals("07:00 wake up", p.arg)
        assertEquals("Seven o'clock it is.", p.text)
        assertEquals("setting" to "vibrate", PhoneAlice.parse("[neutral] Shh. [setting: vibrate]").let { it.tag to it.arg })
        // Every tag her prompt teaches must be read here too.
        val prompt = java.io.File("src/main/assets/offline-prompt.txt").readText()
        val taught = Regex("\\[(\\w+): ").findAll(prompt).map { it.groupValues[1] }.toSet()
        assertEquals(taught, PhoneAlice.TAGS.toSet())
        for (tag in taught) assertEquals(tag, PhoneAlice.parse("[happy] Hi. [$tag: something]").tag)
    }

    @Test fun readsTimesFromTheUserWhenHerTagIsGarbled() {
        // What happened on the Fold6: "Remind me in 10 minutes to drink water" became [timer: 101:100 drink water].
        assertNull(PhoneTools.parseTimer("101:100 drink water"))
        val fixed = PhoneTools.timerFrom("Remind me in 10 minutes to drink water.", "101:100 drink water")
        assertEquals(600 to "drink water", PhoneTools.parseTimer(fixed))
        assertEquals(Triple(6, 30, "wake up"), PhoneTools.parseAlarm(PhoneTools.alarmFrom("wake me up at 6:30 please", "99:99 wake up")))
    }

    @Test fun setsPlainTimersWithoutTheModel() {
        val remind = PhoneAlice.shortcut("Remind me in 10 minutes to drink water.", emptyList())!!
        assertEquals(PhoneAlice.Action.Tool("timer", "10 minutes drink water"), remind.action)
        assertEquals(PhoneAlice.Action.Tool("timer", "5 minutos pro miojo"), PhoneAlice.shortcut("põe um timer de 5 minutos pro miojo", emptyList())!!.action)
        assertEquals(PhoneAlice.Action.Tool("timer", "20 minutes"), PhoneAlice.shortcut("set a timer for 20 minutes", emptyList())!!.action)
        assertEquals(PhoneAlice.Action.Tool("timer", "1 hora tirar a roupa"), PhoneAlice.shortcut("me lembra daqui a 1 hora de tirar a roupa", emptyList())!!.action.let { PhoneAlice.Action.Tool("timer", (it as PhoneAlice.Action.Tool).arg) })
        assertNull(PhoneAlice.shortcut("remind me later to call mom", emptyList()))
    }

    @Test fun looksUpClearSteamAndNewsRequestsAtOnce() {
        assertEquals("steam" to "Hades 2", PhoneAlice.lookup("how much is Hades 2 on Steam?"))
        assertEquals("steam" to "Hollow Knight Silksong", PhoneAlice.lookup("is Hollow Knight Silksong on sale?"))
        assertEquals("steam" to "Hades 2", PhoneAlice.lookup("quanto custa Hades 2 na steam?"))
        assertEquals("steam" to "wishlist", PhoneAlice.lookup("is anything on my Steam wishlist on sale?"))
        assertEquals("steam" to "wishlist", PhoneAlice.lookup("tem algum jogo da minha lista de desejos em promoção na steam?"))
        assertEquals("news" to "Nintendo", PhoneAlice.lookup("any news about Nintendo?"))
        assertEquals("news" to "a economia do Brasil", PhoneAlice.lookup("notícias sobre a economia do Brasil"))
        assertNull(PhoneAlice.lookup("I love Hades 2"))
    }

    @Test fun keepsHerLabelWhenTheUserGivesTheTime() {
        // Her tag looked fine but its digits were mangled; the user's "10 minutes" wins.
        assertEquals(600 to "drink water", PhoneTools.parseTimer(PhoneTools.timerFrom("remind me in 10 minutes to drink water", "1 minutes drink water")))
        assertEquals(Triple(18, 30, "call mom"), PhoneTools.parseAlarm(PhoneTools.alarmFrom("set an alarm at 6:30 pm to call mom", "18:03 call mom")))
    }

    @Test fun looksUpClearWeatherAndCurrencyQuestionsAtOnce() {
        assertEquals("weather" to "Curitiba", PhoneAlice.lookup("will it rain in Curitiba tomorrow?"))
        assertEquals("weather" to "São Carlos, São Paulo", PhoneAlice.lookup("what's the weather like today?", "São Carlos, São Paulo"))
        assertNull(PhoneAlice.lookup("what's the weather like today?"))  // No place and no home city: her model asks.
        assertEquals("weather" to "São Paulo", PhoneAlice.lookup("vai chover amanhã em São Paulo?"))
        assertEquals("weather" to "São Paulo", PhoneAlice.lookup("vai chover em São Paulo amanhã?"))
        assertEquals("weather" to "Curitiba", PhoneAlice.lookup("will it rain tomorrow in Curitiba?"))
        assertEquals("weather" to "Curitiba", PhoneAlice.lookup("do I need an umbrella tomorrow?", "Curitiba"))
        assertEquals("currency" to "1 dólar BRL", PhoneAlice.lookup("quanto tá o dólar hoje?"))
        assertEquals("currency" to "50 euros reais", PhoneAlice.lookup("how much is 50 euros in reais?"))
    }

    @Test fun doesPlainCommandsItself() {
        fun tool(m: String) = (PhoneAlice.shortcut(m, emptyList())?.action as? PhoneAlice.Action.Tool)?.let { it.tag to it.arg }
        assertEquals("setting" to "flashlight on", tool("turn on the flashlight"))
        assertEquals("setting" to "flashlight off", tool("turn the flashlight off please"))
        assertEquals("setting" to "flashlight on", tool("liga a lanterna"))
        assertEquals("setting" to "vibrate", tool("put my phone on vibrate"))
        assertEquals("setting" to "silent", tool("deixa o celular no silencioso"))
        assertEquals("setting" to "do not disturb on", tool("turn on do not disturb"))
        assertEquals("setting" to "do not disturb off", tool("desativa o não perturbe"))
        assertEquals("setting" to "volume down", tool("abaixa o volume"))
        assertEquals("music" to "pause", tool("pause the music"))
        assertEquals("music" to "next", tool("skip this song"))
        assertEquals("alarm" to "07:00", tool("wake me up at 7am"))
        assertEquals("alarm" to "06:30", tool("me acorda às 6 e meia"))
        assertEquals("alarm" to "18:45", tool("set an alarm for 6:45 pm"))
        assertNull(tool("my flashlight is broken"))
        assertNull(tool("wake me up early"))
    }

    @Test fun saysWhatSheCanDoForEachBrain() {
        val light = PhoneAlice.shortcut("what can you do?", emptyList(), light = true)!!
        assertTrue("running light" in light.text)
        assertNull(light.action)
        val full = PhoneAlice.shortcut("o que você pode fazer?", emptyList(), light = false)!!
        assertTrue("alarms and timers" in full.text)
    }

    @Test fun lightPromptTeachesNoTags() {
        val prompt = java.io.File("src/main/assets/offline-prompt-light.txt").readText()
        assertTrue(Regex("\\[(\\w+): ").findAll(prompt).none())
    }

    @Test fun remembersAndNotesWithoutTheModel() {
        fun tool(m: String) = (PhoneAlice.shortcut(m, emptyList(), light = true)?.action as? PhoneAlice.Action.Tool)?.let { it.tag to it.arg }
        assertEquals("remember" to "The user's sister is called Ana.", tool("can you remember that my sister is called Ana?"))
        assertEquals("remember" to "The user is allergic to shrimp.", tool("remember that I am allergic to shrimp"))
        assertEquals("remember" to "The user said (in Portuguese): eu sou alérgico a camarão.", tool("lembra que eu sou alérgico a camarão"))
        assertEquals("note" to "buy milk and eggs", tool("make a note: buy milk and eggs"))
        assertEquals("note" to "comprar pão", tool("anota aí: comprar pão"))
        assertNull(tool("do you remember my sister?"))
    }
}
