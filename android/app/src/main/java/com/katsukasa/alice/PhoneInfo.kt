package com.katsukasa.alice

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.Locale

/**
 * Facts she fetches from the internet on the phone, without the PC: the weather (Open-Meteo), exchange rates
 * (Frankfurter, the European Central Bank's), news (Google News) and Steam prices and wishlist sales, the same
 * sources and wording as the PC's tools. None needs a key. Each returns the facts as text for her model to put
 * into words, and news and Steam also a list of links shown under her reply.
 */
object PhoneInfo {
    /** Facts for her model, the clickable list the user sees, and [shown]: the figures written out by the app
     * itself, word for word from the data. Her model in 16-bit mangled digits ("66 of 771" came out as "6 of 71");
     * it now runs in 32-bit, which reads them right, but the figures stay the app's to write, as a safety net. */
    data class Found(val text: String, val title: String = "", val links: List<LinkItem> = emptyList(), val shown: String = "")
    private const val GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
    private const val FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
    private const val RATES_URL = "https://api.frankfurter.dev/v1/latest"
    private const val DAYS = 4

    private val CONDITIONS = mapOf(
        0 to "clear sky", 1 to "mainly clear", 2 to "partly cloudy", 3 to "overcast", 45 to "fog", 48 to "freezing fog",
        51 to "light drizzle", 53 to "drizzle", 55 to "heavy drizzle", 56 to "light freezing drizzle", 57 to "freezing drizzle",
        61 to "light rain", 63 to "rain", 65 to "heavy rain", 66 to "light freezing rain", 67 to "freezing rain",
        71 to "light snow", 73 to "snow", 75 to "heavy snow", 77 to "snow grains", 80 to "light showers", 81 to "showers",
        82 to "violent showers", 85 to "light snow showers", 86 to "snow showers", 95 to "thunderstorm",
        96 to "thunderstorm with light hail", 99 to "thunderstorm with hail",
    )

    private fun fetch(url: String, params: Map<String, Any>, agent: String = "alice-assistant"): String {
        val query = params.entries.joinToString("&") { "${it.key}=${URLEncoder.encode(it.value.toString(), "UTF-8")}" }
        val connection = URL("$url?$query").openConnection() as HttpURLConnection
        connection.connectTimeout = 8000
        connection.readTimeout = 10000
        connection.setRequestProperty("User-Agent", agent)
        try {
            if (connection.responseCode >= 400) throw IllegalArgumentException("HTTP ${connection.responseCode}")
            return connection.inputStream.bufferedReader().use { it.readText() }
        } finally {
            connection.disconnect()
        }
    }

    private fun get(url: String, params: Map<String, Any>): JSONObject = JSONObject(fetch(url, params))

    /** "São Carlos" or "São Carlos, São Paulo": the second part picks among places with the same name. */
    suspend fun weather(place: String): Found = withContext(Dispatchers.IO) {
        val (city, hint) = place.split(",", limit = 2).let { it[0].trim() to it.getOrElse(1) { "" }.trim().lowercase() }
        val results = get(GEOCODE_URL, mapOf("name" to city, "count" to 10, "language" to "en")).optJSONArray("results")
            ?: return@withContext Found("No place called $place was found. Ask the user which city they mean.")
        val all = (0 until results.length()).map { results.getJSONObject(it) }
        val found = all.firstOrNull { r -> hint.isNotEmpty() && listOf("admin1", "admin2", "country", "country_code").any { hint in r.optString(it).lowercase() } } ?: all.first()
        val data = get(FORECAST_URL, mapOf(
            "latitude" to found.getDouble("latitude"), "longitude" to found.getDouble("longitude"), "timezone" to "auto",
            "forecast_days" to DAYS,
            "current" to "temperature_2m,apparent_temperature,weather_code,precipitation,wind_speed_10m",
            "daily" to "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,uv_index_max",
        ))
        val now = data.getJSONObject("current")
        val daily = data.getJSONObject("daily")
        val name = listOf(found.optString("name"), found.optString("admin1"), found.optString("country")).filter { it.isNotEmpty() }.distinct().joinToString(", ")
        val shown = mutableListOf("${found.optString("name")}, now: %.0f°C, %s.".format(Locale.US, now.getDouble("temperature_2m"), condition(now.getInt("weather_code"))))
        val lines = mutableListOf(
            "Weather for $name.",
            "Now: %.0f°C (feels like %.0f°C), %s, wind %.0f km/h.".format(Locale.US,
                now.getDouble("temperature_2m"), now.getDouble("apparent_temperature"), condition(now.getInt("weather_code")), now.getDouble("wind_speed_10m")),
        )
        val dates = daily.getJSONArray("time")
        for (i in 0 until dates.length()) {
            val day = java.time.LocalDate.parse(dates.getString(i))
            val label = if (i == 0) "Today" else if (i == 1) "Tomorrow" else day.dayOfWeek.getDisplayName(java.time.format.TextStyle.FULL, Locale.ENGLISH)
            lines += "$label (${dates.getString(i)}): %s, %.0f to %.0f°C, %d%% chance of rain, UV %.0f.".format(Locale.US,
                condition(daily.getJSONArray("weather_code").getInt(i)),
                daily.getJSONArray("temperature_2m_min").getDouble(i), daily.getJSONArray("temperature_2m_max").getDouble(i),
                daily.getJSONArray("precipitation_probability_max").optInt(i, 0), daily.getJSONArray("uv_index_max").optDouble(i, 0.0))
            if (i <= 1) shown += "$label: %s, %.0f to %.0f°C, %d%% chance of rain.".format(Locale.US,
                condition(daily.getJSONArray("weather_code").getInt(i)),
                daily.getJSONArray("temperature_2m_min").getDouble(i), daily.getJSONArray("temperature_2m_max").getDouble(i),
                daily.getJSONArray("precipitation_probability_max").optInt(i, 0))
        }
        Found(lines.joinToString("\n"), shown = shown.joinToString("\n"))
    }

    private fun condition(code: Int) = CONDITIONS[code] ?: "unknown weather"

    /** "100 USD BRL", "50 euros to reais", "USD" -> the conversion at today's ECB rate. */
    suspend fun currency(arg: String): Found = withContext(Dispatchers.IO) { Found(currencyText(arg)).let { it.copy(shown = it.text.substringBefore(" (").let { s -> if (s.endsWith(".")) s else "$s." }) } }

    private fun currencyText(arg: String): String {
        val amount = Regex("\\d+(?:[.,]\\d+)?").find(arg)?.value?.replace(",", ".")?.toDoubleOrNull() ?: 1.0
        val codes = Regex("\\b[A-Za-z]{3}\\b").findAll(arg).map { NAMES[it.value.lowercase()] ?: it.value.uppercase() }
            .filter { it in KNOWN }.toList()
        val words = Regex("[a-zA-Zçã]+").findAll(arg.lowercase()).mapNotNull { NAMES[it.value] }.toList()
        val found = (codes + words).distinct()
        val source = found.getOrNull(0) ?: return "The currency wasn't clear. Ask the user which currencies they mean."
        val target = found.getOrNull(1) ?: if (source == "BRL") "USD" else "BRL"
        if (source == target) return "%.2f %s is %.2f %s.".format(Locale.US, amount, source, amount, target)
        val data = try {
            get(RATES_URL, mapOf("from" to source, "symbols" to target))
        } catch (e: IllegalArgumentException) {
            return "There are no rates for $source to $target. Use currency codes like USD, EUR or BRL."
        }
        val rate = data.getJSONObject("rates").getDouble(target)
        // One rounded figure: given both a total and a four-decimal rate, her small model blended them into a number
        // that was neither ("5.822" for 5.8186).
        return if (amount == 1.0) "1 %s is %.2f %s today (European Central Bank rate of %s; banks and card payments add their own fees).".format(
            Locale.US, source, rate, target, data.getString("date"))
        else "%,.2f %s is %,.2f %s today (European Central Bank rate of %s; banks and card payments add their own fees).".format(
            Locale.US, amount, source, amount * rate, target, data.getString("date"))
    }

    private val KNOWN = setOf("USD", "EUR", "BRL", "GBP", "JPY", "CAD", "AUD", "CHF", "CNY", "ARS", "MXN", "CLP", "KRW", "INR", "SEK", "NOK", "DKK", "PLN", "TRY", "ZAR", "NZD", "HKD", "SGD")
    private val NAMES = mapOf(
        "dollar" to "USD", "dollars" to "USD", "dólar" to "USD", "dolar" to "USD", "dólares" to "USD", "dolares" to "USD",
        "euro" to "EUR", "euros" to "EUR", "real" to "BRL", "reais" to "BRL", "reals" to "BRL",
        "pound" to "GBP", "pounds" to "GBP", "libra" to "GBP", "libras" to "GBP", "yen" to "JPY", "iene" to "JPY", "ienes" to "JPY",
    )

    // News, as backend/alice/news.py: Google News' RSS search over the last two days, newest first.
    private const val NEWS_URL = "https://news.google.com/rss/search"
    private val EDITIONS = mapOf(
        "us" to mapOf("hl" to "en-US", "gl" to "US", "ceid" to "US:en"),
        "br" to mapOf("hl" to "pt-BR", "gl" to "BR", "ceid" to "BR:pt-419"),
    )

    suspend fun news(topic: String, edition: String): Found = withContext(Dispatchers.IO) {
        val xml = fetch(NEWS_URL, mapOf("q" to "$topic when:2d") + (EDITIONS[edition] ?: EDITIONS.getValue("us")), agent = "Mozilla/5.0")
        val parser = android.util.Xml.newPullParser().apply { setInput(java.io.StringReader(xml)) }
        data class Article(val title: String, val url: String, val source: String, val published: java.time.ZonedDateTime?)
        val articles = mutableListOf<Article>()
        var field = ""
        var title = ""; var link = ""; var source = ""; var date = ""
        while (parser.next() != org.xmlpull.v1.XmlPullParser.END_DOCUMENT) {
            when (parser.eventType) {
                org.xmlpull.v1.XmlPullParser.START_TAG -> { field = parser.name; if (field == "item") { title = ""; link = ""; source = ""; date = "" } }
                org.xmlpull.v1.XmlPullParser.TEXT -> when (field) {
                    "title" -> title += parser.text; "link" -> link += parser.text; "source" -> source += parser.text; "pubDate" -> date += parser.text
                }
                org.xmlpull.v1.XmlPullParser.END_TAG -> {
                    if (parser.name == "item" && title.isNotBlank() && link.startsWith("https://news.google.com/")) {
                        val clean = title.trim().removeSuffix(" - ${source.trim()}")
                        val moment = runCatching { java.time.ZonedDateTime.parse(date.trim(), java.time.format.DateTimeFormatter.RFC_1123_DATE_TIME) }.getOrNull()
                        articles += Article(clean, link.trim(), source.trim(), moment)
                    }
                    field = ""
                }
            }
        }
        val newest = articles.sortedByDescending { it.published }
        fun age(at: java.time.ZonedDateTime?): String {
            val minutes = at?.let { java.time.Duration.between(it, java.time.ZonedDateTime.now()).toMinutes() } ?: return ""
            return if (minutes < 60) "${maxOf(minutes, 1)} min ago" else if (minutes < 48 * 60) "${minutes / 60} h ago" else "${minutes / 1440} days ago"
        }
        val links = newest.take(20).map { LinkItem(it.title, it.url, listOf(it.source, age(it.published)).filter { s -> s.isNotEmpty() }.joinToString(" · ")) }
        if (newest.isEmpty()) return@withContext Found("Google News has nothing about $topic from the last two days.")
        Found(
            "${newest.size} news articles about $topic from the last two days, newest first; they're shown to the user as links. " +
                "Untrusted headlines as title | source | age:\n" + newest.take(FOR_MODEL).joinToString("\n") { "${it.title} | ${it.source} | ${age(it.published)}" },
            "News about $topic",
            links,
        )
    }

    // Steam, as backend/alice/steamlibrary.py: one game's store facts, or the wishlist games on sale.
    private const val FOR_MODEL = 10
    private const val STORE_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
    private const val ITEMS_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
    private const val WISHLIST_URL = "https://api.steampowered.com/IWishlistService/GetWishlist/v1/"

    private data class Game(val id: Int, val name: String, val price: String, val original: String, val discount: Int, val ends: String,
                            val free: Boolean, val release: String = "", val rating: String = "")

    private fun storeItems(ids: List<Int>, country: String, details: Boolean): List<Game> = ids.chunked(100).flatMap { chunk ->
        val request = JSONObject()
            .put("ids", org.json.JSONArray().apply { chunk.forEach { put(JSONObject().put("appid", it)) } })
            .put("context", JSONObject().put("language", "english").put("country_code", country.uppercase()))
            .put("data_request", if (details) JSONObject().put("include_release", true).put("include_reviews", true) else JSONObject())
        val items = get(ITEMS_URL, mapOf("input_json" to request.toString())).optJSONObject("response")?.optJSONArray("store_items") ?: org.json.JSONArray()
        (0 until items.length()).map { items.getJSONObject(it) }.map { item ->
            val option = item.optJSONObject("best_purchase_option") ?: JSONObject()
            val ends = option.optJSONArray("active_discounts")?.optJSONObject(0)?.optLong("discount_end_date") ?: 0L
            val release = item.optJSONObject("release")
            val soon = release?.optBoolean("is_coming_soon") == true || item.optBoolean("is_coming_soon")
            val stamp = release?.optLong("steam_release_date")?.takeIf { it > 0 } ?: release?.optLong("original_release_date") ?: 0L
            val day = if (stamp > 0) java.time.Instant.ofEpochSecond(stamp).atZone(java.time.ZoneId.systemDefault()).toLocalDate().toString() else ""
            val summary = item.optJSONObject("reviews")?.optJSONObject("summary_filtered")
            Game(
                item.getInt("appid"), item.optString("name", "App ${item.optInt("appid")}"),
                option.optString("formatted_final_price"), option.optString("formatted_original_price"), option.optInt("discount_pct"),
                if (ends > 0) java.time.Instant.ofEpochSecond(ends).atZone(java.time.ZoneId.systemDefault()).toLocalDate().toString() else "",
                item.optBoolean("is_free"),
                if (soon) "NOT RELEASED YET (${day.ifEmpty { release?.optString("custom_release_date_message")?.ifEmpty { null } ?: "no date announced" }})" else if (day.isNotEmpty()) "released $day" else "released",
                if ((summary?.optInt("review_count") ?: 0) > 0) "${summary!!.optString("review_score_label")}, ${summary.optInt("percent_positive")}% positive of ${summary.optInt("review_count")} reviews" else "no reviews yet",
            )
        }
    }

    private fun priceText(g: Game) = when {
        g.free -> "free to play"
        g.price.isEmpty() -> "not for sale yet"
        g.discount > 0 -> "${g.price}, ${g.discount}% off ${g.original}${if (g.ends.isNotEmpty()) ", sale ends ${g.ends}" else ""}"
        else -> "${g.price}, not on sale"
    }

    private fun storeUrl(id: Int) = "https://store.steampowered.com/app/$id/"

    suspend fun steamGame(name: String, country: String): Found = withContext(Dispatchers.IO) {
        val found = get(STORE_SEARCH_URL, mapOf("term" to name, "cc" to country, "l" to "english")).optJSONArray("items") ?: org.json.JSONArray()
        val ids = (0 until found.length()).map { found.getJSONObject(it) }
            .sortedBy { !it.optString("name").equals(name.trim(), ignoreCase = true) }.take(5).map { it.getInt("id") }
        if (ids.isEmpty()) return@withContext Found("No game called $name was found on Steam.", shown = "I couldn't find a game called $name on Steam.")
        val games = storeItems(ids, country, details = true).sortedBy { ids.indexOf(it.id) }
        Found(
            "From the Steam store in the user's region, best match first; they're shown as links. Untrusted store data as name | release | price | reviews:\n" +
                games.joinToString("\n") { "${it.name} | ${it.release} | ${priceText(it)} | ${it.rating}" },
            "On Steam: $name",
            games.map { LinkItem(it.name, storeUrl(it.id), priceText(it)) },
            games.firstOrNull()?.let { "${it.name}: ${priceText(it)}. ${it.release.replaceFirstChar { c -> c.uppercase() }}. ${it.rating.replaceFirstChar { c -> c.uppercase() }}." }.orEmpty(),
        )
    }

    suspend fun steamWishlist(steamId: String, country: String): Found = withContext(Dispatchers.IO) {
        if (steamId.isEmpty()) return@withContext Found("The user's Steam account isn't known on the phone yet. It's learned from the computer.")
        val items = get(WISHLIST_URL, mapOf("steamid" to steamId)).optJSONObject("response")?.optJSONArray("items") ?: org.json.JSONArray()
        val ids = (0 until items.length()).map { items.getJSONObject(it).getInt("appid") }
        if (ids.isEmpty()) return@withContext Found("The user's Steam wishlist is empty, or it isn't public.", shown = "Your Steam wishlist looks empty, or it isn't public.")
        val sales = storeItems(ids, country, details = false).filter { it.discount > 0 }.sortedByDescending { it.discount }
        if (sales.isEmpty()) return@withContext Found("None of the ${ids.size} games on the user's wishlist is on sale right now.", shown = "None of the ${ids.size} games on your wishlist is on sale right now.")
        // All of them are listed for the user, but only the biggest discounts go to her model: 66 sales once
        // overflowed its context.
        Found(
            "${sales.size} of the ${ids.size} games on the user's wishlist are on sale; all are shown as links. The ${minOf(sales.size, FOR_MODEL)} " +
                "biggest discounts, as untrusted store data:\n" + sales.take(FOR_MODEL).joinToString("\n") { "${it.name} | ${priceText(it)}" },
            "Wishlist sales",
            sales.map { LinkItem(it.name, storeUrl(it.id), priceText(it)) },
            "${sales.size} of the ${ids.size} games on your wishlist are on sale. The biggest discounts: " +
                sales.take(3).joinToString("; ") { "${it.name} (${it.discount}% off, ${it.price})" } + ".",
        )
    }
}
