package io.github.hiero.alice

import java.text.Normalizer

/**
 * Alice when the PC can't be reached. Plain requests to open an app or start a search are matched here, in
 * English or Portuguese, and done at once. Everything else goes to her small model on the phone, which
 * writes a mood tag first and may end with one action tag, [open: App] or [search: terms]
 * (see assets/offline-prompt.txt); [parse] reads those out of its text.
 */
object PhoneAlice {
    data class App(val label: String, val pkg: String)

    sealed interface Action {
        data class OpenApp(val app: App) : Action
        /** A kind of app, like music or maps, opened as whichever app the phone uses for it. */
        data class OpenRole(val role: Role) : Action
        data class Search(val query: String) : Action
        /** One of her phone tools, as her model's tags name them, like "timer" and "10 minutes tea". */
        data class Tool(val tag: String, val arg: String) : Action
    }

    /** Kinds of app Android can open as "the default one", with the words people use for them. */
    enum class Role(val label: String, vararg val words: String) {
        MUSIC("your music app", "music", "musica", "music app", "music player"),
        MAPS("Maps", "maps", "map", "mapa", "mapas", "directions", "navigation", "gps"),
        BROWSER("the browser", "browser", "navegador", "internet", "web browser"),
        EMAIL("your email", "email", "e mail", "mail", "gmail"),
        GALLERY("the gallery", "gallery", "galeria", "photos", "fotos", "pictures"),
        CALENDAR("the calendar", "calendar", "calendario", "agenda"),
        CONTACTS("your contacts", "contacts", "contatos", "contactos"),
        MESSAGES("your messages", "messages", "mensagens", "sms", "texts"),
        CALCULATOR("the calculator", "calculator", "calculadora"),
        CAMERA("the camera", "camera", "camara"),
        CLOCK("the clock", "clock", "alarm", "alarms", "alarme", "relogio", "timer"),
        SETTINGS("Settings", "settings", "configuracoes", "configuracao", "ajustes"),
    }

    /** What [name] means on this phone: a kind of app, or an installed app by its label. Roles come
     * first, so "music" opens the phone's own music app rather than whichever label contains "music". */
    fun resolve(name: String, apps: List<App>): Action? {
        val target = plain(name).removeSuffix(" app").trim()
        Role.entries.firstOrNull { role -> role.words.any { plain(it) == target } }?.let { return Action.OpenRole(it) }
        return findApp(name, apps)?.let { Action.OpenApp(it) }
    }

    data class Answer(val text: String, val mood: String, val action: Action? = null)

    private val politeness = Regex(
        "^(?:alice[,!]?\\s+|hey\\s+|please\\s+|could you\\s+|can you\\s+|would you\\s+|will you\\s+|" +
            "por favor[,]?\\s+|pode\\s+|voc[eê] pode\\s+|consegue\\s+)",
        RegexOption.IGNORE_CASE,
    )
    private val trailingPoliteness = Regex("[,\\s]+(?:please|por favor|pls|thanks|obrigad[oa])$", RegexOption.IGNORE_CASE)
    // Timers said plainly never need the model: "remind me in 10 minutes to drink water", "set a timer for 5 minutes".
    private val remindIn = Regex(
        "^(?:remind me|me lembr[ae]|lembr[ae]-me|lembra)\\s+(?:in|em|daqui a|daqui)\\s+(.+?)\\s+(?:to|that|of|de|pra|para|que)\\s+(.+)$",
        RegexOption.IGNORE_CASE,
    )
    private val timerFor = Regex(
        "^(?:set|start|put|p[oõ]e|coloca|bota)?\\s*(?:a|an|um)?\\s*timer\\s+(?:for|of|de)?\\s*(.+)$",
        RegexOption.IGNORE_CASE,
    )
    // Clear Steam and news requests go straight to the lookup; her model only puts the result into words.
    private val steamGame = listOf(
        Regex("^how much (?:is|does|for) (.+?)(?: cost)? on steam$", RegexOption.IGNORE_CASE),
        Regex("^(?:what's|what is) the price of (.+?) on steam$", RegexOption.IGNORE_CASE),
        Regex("^is (.+?) on sale(?: on steam)?$", RegexOption.IGNORE_CASE),
        Regex("^quanto (?:custa|tá|ta|está|esta) (.+?) na steam$", RegexOption.IGNORE_CASE),
        Regex("^(.+?) (?:tá|ta|está|esta) em promoção(?: na steam)?$", RegexOption.IGNORE_CASE),
    )
    private val steamWishlist = Regex("^(?:is )?(?:there )?(?:anything|any games?|something|what's) (?:on|in) my (?:steam )?wishlist (?:on sale|in sale|discounted)(?: on steam)?$|^(?:tem )?(?:algum jogo|algo) (?:da|na) minha lista de desejos em promoção(?: na steam)?$", RegexOption.IGNORE_CASE)
    private val newsAbout = Regex("^(?:any |the |what's the |latest )?news (?:about|on) (.+)$|^(?:quais as |as )?not[ií]cias (?:sobre|de) (.+)$", RegexOption.IGNORE_CASE)

    private val weatherAsk = listOf(
        Regex("^(?:will it|is it going to|is it gonna|does it look like it'?ll) (?:rain|be (?:hot|cold|sunny))(?: (?:today|tomorrow|tonight|this week|later|now))?(?: (?:in|at) (.+?))?(?: (?:today|tomorrow|tonight|this week|later|now))?$", RegexOption.IGNORE_CASE),
        Regex("^(?:what's|what is|how's|how is|whats) the (?:weather|forecast)(?: like)?(?: (?:today|tomorrow|tonight|this week|later|now))?(?: (?:in|at|for) (.+?))?(?: (?:today|tomorrow|tonight|this week|now))?$", RegexOption.IGNORE_CASE),
        Regex("^(?:do i need|should i (?:take|bring)) an umbrella(?: (?:in|at) (.+?))?(?: (?:today|tomorrow|tonight))?$", RegexOption.IGNORE_CASE),
        Regex("^(?:vai chover|vai fazer (?:frio|calor|sol)|como (?:tá|ta|está|esta|vai estar) o tempo|qual a previsão(?: do tempo)?)(?: (?:hoje|amanhã|amanha|agora|essa semana))?(?: (?:em|no|na) (.+?))?(?: (?:hoje|amanhã|amanha|agora|essa semana))?$", RegexOption.IGNORE_CASE),
    )
    private val currencyAsk = listOf(
        Regex("^(?:quanto (?:tá|ta|está|esta) o|cotação do|cotacao do) (d[oó]lar|euro|libra|iene)(?: hoje)?$", RegexOption.IGNORE_CASE) to { m: MatchResult -> "1 ${m.groupValues[1]} BRL" },
        Regex("^how much is (\\d+(?:[.,]\\d+)?\\s*[a-z]+) in ([a-z]+)$", RegexOption.IGNORE_CASE) to { m: MatchResult -> "${m.groupValues[1]} ${m.groupValues[2]}" },
        Regex("^quanto (?:é|e|dá|da|são|sao) (\\d+(?:[.,]\\d+)?\\s*[a-zçã]+) em ([a-zçã]+)$", RegexOption.IGNORE_CASE) to { m: MatchResult -> "${m.groupValues[1]} ${m.groupValues[2]}" },
    )

    /** A Steam, news, weather or currency request to look up at once: the tag and its argument, or null.
     * A weather question without a place uses [home], the home city she remembers, or isn't looked up at once. */
    fun lookup(message: String, home: String = ""): Pair<String, String>? {
        val text = clean(message)
        weatherAsk.firstNotNullOfOrNull { it.matchEntire(text) }?.let { m ->
            val place = m.groupValues[1].trim().ifEmpty { home }
            if (place.isNotEmpty()) return "weather" to place
        }
        currencyAsk.firstNotNullOfOrNull { (regex, arg) -> regex.matchEntire(text)?.let(arg) }?.let { return "currency" to it }
        if (steamWishlist.matches(text)) return "steam" to "wishlist"
        steamGame.firstNotNullOfOrNull { it.matchEntire(text) }?.let { return "steam" to it.groupValues[1].trim() }
        newsAbout.matchEntire(text)?.let { m -> return "news" to (m.groupValues[1].ifEmpty { m.groupValues[2] }).trim() }
        return null
    }

    private fun clean(message: String): String {
        var text = message.trim().trimEnd('.', '!', '?', ' ')
        while (true) {
            val stripped = politeness.replaceFirst(text, "").replace(trailingPoliteness, "")
            if (stripped == text) break
            text = stripped
        }
        return text
    }

    private val openRequest = Regex(
        "^(?:open|launch|start|run|abr[ae]|abrir|inici[ae]|iniciar)\\s+(?:up\\s+)?(?:the\\s+|my\\s+|o\\s+|a\\s+|meu\\s+|minha\\s+)?(.+?)(?:\\s+app)?(?:\\s+for me|\\s+pra mim|\\s+para mim)?$",
        RegexOption.IGNORE_CASE,
    )
    private val searchRequest = Regex(
        "^(?:(?:start|open|do|run)\\s+a\\s+(?:web\\s+)?search\\s+(?:for|about|on)|search(?:\\s+the web)?(?:\\s+(?:for|about))?|look\\s+up|google|find\\s+(?:out\\s+)?about|" +
            "pesquis[ae]r?(?:\\s+(?:sobre|por))?|procur[ae]r?(?:\\s+(?:sobre|por))?|busc[ae]r?(?:\\s+(?:sobre|por))?)\\s+(.+?)(?:\\s+for me|\\s+pra mim|\\s+para mim)?$",
        RegexOption.IGNORE_CASE,
    )

    // Commands the phone does itself, in English or Portuguese, without her model: instant in both modes, and the
    // only way to act in light mode, where her model just talks.
    private fun cmd(pattern: String, make: (MatchResult) -> Pair<String, String>?) = Regex("^(?:$pattern)$", RegexOption.IGNORE_CASE) to make
    private val commands = listOf(
        cmd("(?:turn|switch|put) (on|off) (?:the |my )?(?:flashlight|torch)|(?:turn|switch) (?:the |my )?(?:flashlight|torch) (on|off)") { m ->
            "setting" to "flashlight ${m.groupValues[1].ifEmpty { m.groupValues[2] }.lowercase()}" },
        cmd("(liga|desliga) a lanterna") { m -> "setting" to if (m.groupValues[1].lowercase() == "liga") "flashlight on" else "flashlight off" },
        cmd("(?:put|set|switch|turn) (?:my |the )?(?:phone|cell(?:phone)?) (?:on|to) vibrat(?:e|ion)(?: mode)?|vibrate mode|(?:deixa|coloca|bota|põe|poe) (?:o |meu )?(?:celular|telefone) no vibrar") { "setting" to "vibrate" },
        cmd("(?:put|set|switch|turn) (?:my |the )?(?:phone|cell(?:phone)?) (?:on|to) silent(?: mode)?|silent mode|(?:deixa|coloca|bota|põe|poe) (?:o |meu )?(?:celular|telefone) no silencioso") { "setting" to "silent" },
        cmd("(?:turn|put) (?:the )?sound (?:back )?on|unmute(?: (?:my |the )?phone)?|tira (?:o celular )?do (?:silencioso|vibrar)") { "setting" to "sound on" },
        cmd("(?:turn|switch) (on|off) (?:the )?(?:do not disturb|dnd)|(?:turn |switch )?(?:the )?(?:do not disturb|dnd) (on|off)|(ativa|desativa|liga|desliga) o não perturbe") { m ->
            val on = m.groupValues.drop(1).firstOrNull { it.isNotEmpty() }!!.lowercase() in setOf("on", "ativa", "liga")
            "setting" to if (on) "do not disturb on" else "do not disturb off" },
        cmd("(?:turn (?:the )?volume|volume|turn it) (up|down)|(aumenta|abaixa|diminui) o volume") { m ->
            "setting" to if (m.groupValues[1].lowercase() == "up" || m.groupValues[2].lowercase() == "aumenta") "volume up" else "volume down" },
        cmd("(?:turn (?:the )?brightness|brightness) (up|down)|(aumenta|abaixa|diminui) o brilho") { m ->
            "setting" to if (m.groupValues[1].lowercase() == "up" || m.groupValues[2].lowercase() == "aumenta") "brightness up" else "brightness down" },
        cmd("(?:pause|stop) (?:the )?(?:music|song)|(?:pausa|para) a música") { "music" to "pause" },
        cmd("(?:play|resume) (?:the )?music|(?:continua|volta) a música") { "music" to "play" },
        cmd("(?:next|skip)(?: (?:song|track|this song|this one))?|skip (?:this )?(?:song|track)|próxima(?: música)?|pula (?:essa )?música") { "music" to "next" },
        cmd("previous (?:song|track)|(?:go )?back a (?:song|track)|música anterior|volta uma música") { "music" to "previous" },
        cmd("(?:please )?remember (?:that )?(.+)|(?:lembra|lembre-se|lembre) (?:que |de que )?(.+)") { m ->
            "remember" to aboutTheUser(m.groupValues[1].ifEmpty { m.groupValues[2] }) },
        cmd("(?:make|take|write|add) (?:a )?note(?: that)?:? (.+)|note:? (.+)|anota(?: aí)?(?: que)?:? (.+)") { m ->
            "note" to m.groupValues.drop(1).first { it.isNotEmpty() }.trim() },
        cmd("(?:wake me up|set (?:an |my )?alarm|alarm)(?: (?:for|at))? (.+?)(?: (?:tomorrow|today|please))?|me acorda(?: amanhã)?(?: (?:às|as))? (.+?)(?: amanhã)?") { m ->
            val time = m.groupValues[1].ifEmpty { m.groupValues[2] }.replace(Regex("\\s+e meia$"), ":30").replace(Regex("\\s+horas?$"), "")
            PhoneTools.parseAlarm(time)?.let { (h, min, _) -> "alarm" to "%02d:%02d".format(h, min) } },
    )

    /** "my sister is called Ana" -> "The user's sister is called Ana.", as her memories on the PC are written. */
    fun aboutTheUser(said: String): String {
        var fact = said.trim().trimEnd('.', '!')
        fact = fact.replace(Regex("^(?:I am|I'm)\\b", RegexOption.IGNORE_CASE), "The user is")
            .replace(Regex("^I\\b"), "The user")
            .replace(Regex("^my\\b", RegexOption.IGNORE_CASE), "The user's")
        // Portuguese is kept as said, labelled; her model on the PC reads it fine.
        if (Regex("^(?:eu|meu|minha|meus|minhas|sou|tenho|moro|gosto|odeio)\\b", RegexOption.IGNORE_CASE).containsMatchIn(fact)) return "The user said (in Portuguese): $fact."
        return fact.replaceFirstChar { it.uppercase() } + "."
    }

    private fun commandReply(tag: String, arg: String): String = when (tag) {
        "remember" -> "I'll remember that: ${arg.removeSuffix(".")}."
        "note" -> "Noted: $arg"
        "alarm" -> "An alarm for $arg. Sweet dreams!"
        "setting" -> when {
            arg.startsWith("flashlight") -> if (arg.endsWith("on")) "There you are, a little light." else "Flashlight off."
            arg == "vibrate" -> "Shh, on vibrate now."
            arg == "silent" -> "Hush, silent now."
            arg == "sound on" -> "Sound's back on."
            arg.startsWith("do not disturb") -> if (arg.endsWith("on")) "Do Not Disturb is on. Nobody shall bother you." else "Do Not Disturb is off."
            arg.startsWith("volume") -> if (arg.endsWith("up")) "A little louder." else "A little quieter."
            else -> if (arg.endsWith("up")) "A little brighter." else "A little dimmer."
        }
        else -> when (arg) { "pause" -> "Paused."; "play" -> "Playing."; "next" -> "On to the next one!"; else -> "Back one." }
    }

    private val askCapabilities = Regex("^(?:what (?:can|do) you do(?: right now| now| here| offline)?|what are your (?:capabilities|abilities|skills)|help|o que (?:você|vc) (?:pode|consegue|sabe) fazer(?: agora)?|quais (?:são )?(?:as )?suas habilidades)$", RegexOption.IGNORE_CASE)

    /** What she can do on the phone right now, written by the app so it's always true: [light] is the small model. */
    fun capabilities(light: Boolean): String = if (light) """
        I'm running light right now, to spare your phone, so I can chat but not do things myself. The phone still does these when you ask plainly:
        • open an app: "open WhatsApp", "open music"
        • search the web: "search for train times"
        • timers and alarms: "set a timer for 10 minutes", "remind me in 20 minutes to call mom", "wake me up at 7"
        • the weather and money: "what's the weather in Curitiba?", "how much is 50 euros in reais?"
        • Steam and news: "how much is Hades 2 on Steam?", "news about Nintendo"
        • settings: "turn on the flashlight", "put my phone on vibrate", "turn on do not disturb", "volume up"
        • music: "pause the music", "next song"
        • notes and memories: "make a note: buy milk", "remember that my sister is called Ana"
        Anything trickier waits until I'm fully awake or back on your computer.
    """.trimIndent() else """
        Away from your computer, on the phone itself, I can:
        • open apps and start web searches
        • set alarms and timers, and save notes and things to remember (they reach your computer later)
        • look up the weather, exchange rates, news, and Steam prices and wishlist sales
        • switch the flashlight, vibrate, silent, Do Not Disturb, volume and brightness, and control the music
        Wi-Fi, mobile data and Bluetooth I can only open the switch for. Anything on your computer waits until I can reach it.
    """.trimIndent()

    /** Without her model, the shortcut is all she has. */
    fun answer(message: String, apps: List<App>): Answer = shortcut(message, apps) ?: Answer(
        "I'm only half awake without your computer, so all I can do right now is open apps or start a search. " +
            "Try “open WhatsApp” or “search for train times”.",
        "sleepy",
    )

    /** A plain request answered without the model ("open X", "search for X", a timer or alarm, a setting, the
     * music, or what she can do); null for anything else. */
    fun shortcut(message: String, apps: List<App>, light: Boolean = false): Answer? {
        var text = message.trim().trimEnd('.', '!', '?', ' ')
        while (true) {
            val stripped = politeness.replaceFirst(text, "").replace(trailingPoliteness, "")
            if (stripped == text) break
            text = stripped
        }
        if (askCapabilities.matches(text)) return Answer(capabilities(light), if (light) "sleepy" else "happy")
        commands.firstNotNullOfOrNull { (regex, make) -> regex.matchEntire(text)?.let(make) }?.let { (tag, arg) ->
            return Answer(commandReply(tag, arg), "happy", Action.Tool(tag, arg))
        }
        remindIn.matchEntire(text)?.let { match ->
            val length = PhoneTools.parseTimer(match.groupValues[1])?.first ?: return@let
            val what = match.groupValues[2].trim()
            return Answer("A timer for ${describe(length)}, then I'll remind you to $what.", "happy", Action.Tool("timer", "${match.groupValues[1]} $what"))
        }
        timerFor.matchEntire(text)?.let { match ->
            val (length, label) = PhoneTools.parseTimer(match.groupValues[1]) ?: return@let
            return Answer("A timer for ${describe(length)}${if (label.isNotEmpty()) ": $label" else ""}. Off it goes!", "happy", Action.Tool("timer", match.groupValues[1]))
        }
        searchRequest.matchEntire(text)?.let { match ->
            val query = match.groupValues[1].trim()
            return Answer(pick("I've opened a search for “$query”.", "Off you go: a search for “$query”.", "Here's a search for “$query”, all yours to read."), "happy", Action.Search(query))
        }
        openRequest.matchEntire(text)?.let { match ->
            val wanted = match.groupValues[1].trim()
            val action = resolve(wanted, apps)
                ?: return Answer("I looked all through your phone, but I couldn't find an app called “$wanted”.", "worried")
            val label = when (action) {
                is Action.OpenApp -> action.app.label
                is Action.OpenRole -> action.role.label
                else -> wanted
            }
            return Answer(pick("Opening $label for you!", "Here's $label.", "${label.replaceFirstChar { it.uppercase() }}, coming right up!"), "happy", action)
        }
        return null
    }

    /** [tag] and [arg] are her action, like "alarm" and "07:00 wake up"; [open] and [search] are kept for those two. */
    data class Parsed(val mood: String?, val text: String, val open: String?, val search: String?, val tag: String? = null, val arg: String? = null)

    val TAGS = listOf("open", "search", "alarm", "timer", "note", "remember", "setting", "music", "weather", "currency", "news", "steam")

    private val moodTag = Regex("^\\s*\\[\\s*([A-Za-z]+)\\s*]")
    private val actionTag = Regex("\\[\\s*(open|search|alarm|timer|note|remember|setting|music|weather|currency|news|steam)\\s*:\\s*([^\\]]+)]", RegexOption.IGNORE_CASE)
    /** Her message's time stamp, sometimes echoed back by the small model; never shown. */
    private val echoedStamp = Regex("\\s*\\(?\\b(?:when it was )?sent [A-Z][a-z]+day,[^\\n]*|\\s*\\(?\\bPhone: ringer[^\\n]*", RegexOption.IGNORE_CASE)
    private val anyTag = Regex("\\[[^\\]]*]")
    private val unfinishedTag = Regex("\\[[^\\]]*$")

    /** Her mood, the words to show and her action, from the model's text so far. Tags are never shown,
     * including one still being written at the end. */
    fun parse(raw: String): Parsed {
        val mood = moodTag.find(raw)?.groupValues?.get(1)?.lowercase()?.takeIf { it in MOODS || it == "neutral" }
        val action = actionTag.find(raw)
        val text = raw.replace(echoedStamp, "").replace(anyTag, "").replace(unfinishedTag, "").replace(Regex("[ \\t]{2,}"), " ").trim()
        return Parsed(
            mood = mood,
            text = text,
            open = action?.takeIf { it.groupValues[1].equals("open", true) }?.groupValues?.get(2)?.trim(),
            search = action?.takeIf { it.groupValues[1].equals("search", true) }?.groupValues?.get(2)?.trim(),
            tag = action?.groupValues?.get(1)?.lowercase(),
            arg = action?.groupValues?.get(2)?.trim(),
        )
    }

    /** The closest launcher label: exact, then starting with it, then containing every word. */
    fun findApp(wanted: String, apps: List<App>): App? {
        val target = plain(wanted)
        if (target.isEmpty()) return null
        val labelled = apps.map { it to plain(it.label) }
        labelled.firstOrNull { it.second == target }?.let { return it.first }
        labelled.filter { it.second.startsWith(target) }.minByOrNull { it.second.length }?.let { return it.first }
        val words = target.split(" ")
        labelled.filter { (_, label) -> words.all { it in label } }.minByOrNull { it.second.length }?.let { return it.first }
        return apps.firstOrNull { plain(it.pkg).contains(target.replace(" ", "")) }
    }

    private fun plain(text: String): String =
        Normalizer.normalize(text, Normalizer.Form.NFD).replace(Regex("\\p{Mn}+"), "").lowercase().replace(Regex("[^a-z0-9]+"), " ").trim()

    private fun pick(vararg options: String) = options.random()

    fun describe(seconds: Int): String = when {
        seconds % 3600 == 0 -> "${seconds / 3600} hour${if (seconds == 3600) "" else "s"}"
        seconds >= 3600 -> "${seconds / 3600} h ${seconds % 3600 / 60} min"
        seconds % 60 == 0 -> "${seconds / 60} minute${if (seconds == 60) "" else "s"}"
        else -> "$seconds seconds"
    }
}
