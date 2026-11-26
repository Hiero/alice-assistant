package com.katsukasa.alice

import java.time.DayOfWeek
import java.time.LocalDate
import java.time.temporal.TemporalAdjusters

/**
 * Brazilian holidays and birthdays, worked out on the phone so she greets and dresses for the day even when
 * the PC is out of reach. A port of backend/alice/occasions.py; the greetings themselves and the birthdays come
 * from the PC with each connection, so the words live in one place.
 */
object Occasions {
    data class Birthday(val person: String, val month: Int, val day: Int)

    const val USER = "user"

    /** Holiday key to the outfit folder she wears that day, as outfit() on the PC. */
    private val OUTFITS = mapOf(
        "halloween" to "halloween", "christmas_eve" to "christmas", "christmas" to "christmas",
        "new_years_eve" to "new_year", "new_year" to "new_year", "valentines" to "sweethearts",
        "sweethearts" to "sweethearts", "easter" to "easter",
    )

    /** Gregorian Easter Sunday (anonymous Gregorian algorithm). */
    fun easter(year: Int): LocalDate {
        val a = year % 19; val b = year / 100; val c = year % 100
        val d = b / 4; val e = b % 4
        val f = (b + 8) / 25
        val g = (b - f + 1) / 3
        val h = (19 * a + b - d - g + 15) % 30
        val i = c / 4; val k = c % 4
        val l = (32 + 2 * e + 2 * i - h - k) % 7
        val m = (a + 11 * h + 22 * l) / 451
        val month = (h + l - 7 * m + 114) / 31
        val day = (h + l - 7 * m + 114) % 31 + 1
        return LocalDate.of(year, month, day)
    }

    private fun secondSunday(year: Int, month: Int): LocalDate =
        LocalDate.of(year, month, 1).with(TemporalAdjusters.dayOfWeekInMonth(2, DayOfWeek.SUNDAY))

    fun holidays(year: Int): Map<LocalDate, String> {
        val sunday = easter(year)
        return linkedMapOf(
            LocalDate.of(year, 1, 1) to "new_year",
            sunday.minusDays(48) to "carnival",
            sunday.minusDays(47) to "carnival",
            sunday.minusDays(2) to "good_friday",
            sunday to "easter",
            LocalDate.of(year, 2, 14) to "valentines",
            LocalDate.of(year, 4, 21) to "tiradentes",
            LocalDate.of(year, 5, 1) to "labour",
            secondSunday(year, 5) to "mothers",
            LocalDate.of(year, 6, 12) to "sweethearts",
            LocalDate.of(year, 6, 24) to "sao_joao",
            secondSunday(year, 8) to "fathers",
            LocalDate.of(year, 9, 7) to "independence",
            LocalDate.of(year, 10, 12) to "childrens",
            LocalDate.of(year, 10, 31) to "halloween",
            LocalDate.of(year, 11, 2) to "finados",
            LocalDate.of(year, 11, 15) to "republic",
            LocalDate.of(year, 11, 20) to "black_consciousness",
            LocalDate.of(year, 12, 24) to "christmas_eve",
            LocalDate.of(year, 12, 25) to "christmas",
            LocalDate.of(year, 12, 31) to "new_years_eve",
        )
    }

    fun fallsOn(b: Birthday, day: LocalDate): Boolean {
        var dom = b.day
        if (b.month == 2 && b.day == 29 && !day.isLeapYear) dom = 28
        return day.monthValue == b.month && day.dayOfMonth == dom
    }

    /** What she wears on [day]: the outfit she was asked to put on wins, else the day's, else her usual dress. */
    fun outfit(day: LocalDate, worn: String): String = worn.ifEmpty { OUTFITS[holidays(day.year)[day]].orEmpty() }

    /** Today's greeting from the PC's words ([messages] by holiday key, plus the two birthday ones), and the hat. */
    fun greeting(day: LocalDate, birthdays: List<Birthday>, messages: Map<String, String>): Pair<String, Boolean> {
        val todays = birthdays.filter { fallsOn(it, day) }
        val parts = mutableListOf<String>()
        if (todays.any { it.person == USER }) messages["user_birthday"]?.let { parts.add(it) }
        val others = todays.filter { it.person != USER }.map { it.person }
        if (others.isNotEmpty()) messages["other_birthday"]?.let { parts.add(it.replace("{names}", others.joinToString(" and "))) }
        holidays(day.year)[day]?.let { key -> messages[key]?.let { parts.add(it) } }
        return parts.joinToString(" ") to todays.isNotEmpty()
    }
}
