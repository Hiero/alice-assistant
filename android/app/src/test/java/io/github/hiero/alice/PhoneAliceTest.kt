package io.github.hiero.alice

import io.github.hiero.alice.PhoneAlice.Action
import io.github.hiero.alice.PhoneAlice.App
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PhoneAliceTest {
    private val apps = listOf(
        App("WhatsApp", "com.whatsapp"),
        App("Spotify", "com.spotify.music"),
        App("Google Maps", "com.google.android.apps.maps"),
        App("Câmera", "com.sec.android.app.camera"),
        App("Chrome", "com.android.chrome"),
        App("YouTube Music", "com.google.android.apps.youtube.music"),
        App("YouTube", "com.google.android.youtube"),
    )

    private fun opened(message: String) = when (val action = PhoneAlice.answer(message, apps).action) {
        is Action.OpenApp -> action.app.pkg
        is Action.OpenRole -> "role:${action.role}"
        else -> null
    }
    private fun searched(message: String) = (PhoneAlice.answer(message, apps).action as? Action.Search)?.query

    @Test fun opensApps() {
        assertEquals("com.whatsapp", opened("open whatsapp"))
        assertEquals("com.whatsapp", opened("Alice, can you open WhatsApp please?"))
        assertEquals("com.spotify.music", opened("launch the Spotify app"))
        assertEquals("com.google.android.apps.maps", opened("open google maps"))
        assertEquals("role:MAPS", opened("open maps"))
        assertEquals("role:CAMERA", opened("abre a camera"))
        assertEquals("role:MUSIC", opened("open music"))
        assertEquals("role:MUSIC", opened("abre o app de música".replace("o app de ", "")))
        assertEquals("role:SETTINGS", opened("abrir configurações"))
        assertEquals("com.google.android.youtube", opened("open youtube"))
        assertEquals("com.google.android.apps.youtube.music", opened("open youtube music"))
        assertEquals("com.android.chrome", opened("por favor, abrir o chrome"))
    }

    @Test fun searches() {
        assertEquals("best ramen in São Paulo", searched("search for best ramen in São Paulo"))
        assertEquals("train times", searched("can you look up train times?"))
        assertEquals("cotação do dólar", searched("pesquisa cotação do dólar"))
        assertEquals("pastel near me", searched("start a search for pastel near me"))
        assertEquals("Legião Urbana", searched("procure sobre Legião Urbana pra mim"))
    }

    @Test fun admitsWhatItCantDo() {
        assertNull(PhoneAlice.answer("how are you today?", apps).action)
        assertNull(PhoneAlice.answer("open the pod bay doors", apps).action)
        assertEquals("worried", PhoneAlice.answer("open the pod bay doors", apps).mood)
    }

    @Test fun resolvesWhatHerModelAsksFor() {
        assertEquals(Action.OpenRole(PhoneAlice.Role.MUSIC), PhoneAlice.resolve("Music", apps))
        assertEquals(Action.OpenRole(PhoneAlice.Role.MUSIC), PhoneAlice.resolve("music app", apps))
        assertEquals("com.google.android.apps.youtube.music", (PhoneAlice.resolve("YouTube Music", apps) as Action.OpenApp).app.pkg)
        assertEquals("com.whatsapp", (PhoneAlice.resolve("WhatsApp", apps) as Action.OpenApp).app.pkg)
        assertNull(PhoneAlice.resolve("Spotify Premium Deluxe", apps))
    }

    @Test fun readsTagsFromHerModel() {
        val full = PhoneAlice.parse("[happy] Oh, music! Here's Spotify for you. [open: Spotify]")
        assertEquals("happy", full.mood)
        assertEquals("Oh, music! Here's Spotify for you.", full.text)
        assertEquals("Spotify", full.open)
        assertNull(full.search)

        val search = PhoneAlice.parse("[curious] Let's look it up. [search: weather São Paulo tomorrow]")
        assertEquals("weather São Paulo tomorrow", search.search)
        assertEquals("Let's look it up.", search.text)
    }

    @Test fun hidesTagsStillBeingWritten() {
        assertEquals("", PhoneAlice.parse("[hap").text)
        assertNull(PhoneAlice.parse("[hap").mood)
        assertEquals("Here you go.", PhoneAlice.parse("[happy] Here you go. [open: Spo").text)
        assertNull(PhoneAlice.parse("[happy] Here you go. [open: Spo").open)
    }

    @Test fun ignoresMadeUpMoods() {
        val odd = PhoneAlice.parse("[disappointed] Oh, really?")
        assertNull(odd.mood)
        assertEquals("Oh, really?", odd.text)
    }
}
