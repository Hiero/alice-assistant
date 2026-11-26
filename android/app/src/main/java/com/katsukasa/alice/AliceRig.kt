package com.katsukasa.alice

import android.content.Context
import android.graphics.BitmapFactory
import android.graphics.RenderEffect
import android.graphics.RuntimeShader
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.requiredSize
import androidx.compose.foundation.layout.wrapContentSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asComposeRenderEffect
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt
import kotlin.math.sin
import kotlin.random.Random

/*
 * Her motion on the phone, ported from the desktop shell (shell/shell.qml and shell/shaders/rig.frag) so she
 * moves the same way on both. The flat art is bent by a shader like a Live2D rig: the head tilts, nods and
 * turns over the neck, the chest rises with each breath and the hair tips sway. Faces, blinks and the mouth
 * are painted layers on top, all drawn into the one bent layer.
 *
 * Units follow the shell: the art is drawn 240 × 351 "points" (half its 480 × 702 pixels), inside a rig with
 * margins that leave room for the head tilt. Everything is scaled from points to screen pixels at draw time.
 */

private const val ART_W = 240f
private const val ART_H = 351f
// Room around her for the head tilt and for outfits drawn on a bigger canvas (a witch hat, a crown).
private const val MARGIN_SIDE = 50f
private const val MARGIN_TOP = 100f
private const val RIG_W = ART_W + 2 * MARGIN_SIDE
private const val RIG_H = ART_H + MARGIN_TOP

private const val RIG_SHADER = """
uniform shader source;
uniform float2 rigSize;
uniform float2 neck;
uniform float2 faceCentre;
uniform float2 faceRadius;
uniform float unit;       // screen pixels per point
uniform float headAngle;  // radians, positive leans her head to her left (clockwise on screen)
uniform float headTurn;   // points her features shift sideways
uniform float headNod;    // points her head drops, negative lifts the chin
uniform float breath;     // points her chest rises
uniform float sway;       // points her hair tips swing sideways

half4 main(float2 p) {
    float2 q = p;

    // Breathing lifts everything above the waist, fading out toward the bottom.
    q.y += breath * unit * (1.0 - smoothstep(neck.y + 40.0 * unit, rigSize.y, p.y));

    // The head turns as one piece above the neck, blending into the shoulders.
    float head = 1.0 - smoothstep(neck.y - 12.0 * unit, neck.y + 36.0 * unit, p.y);
    float a = -headAngle * head;
    float2 d = q - neck;
    q = neck + float2(cos(a) * d.x - sin(a) * d.y, sin(a) * d.x + cos(a) * d.y);
    q.y -= headNod * unit * head;

    // Turning: features in the middle of the face shift more than its outline.
    float2 f = (q - faceCentre) / faceRadius;
    float bulge = max(0.0, 1.0 - dot(f, f));
    q.x -= headTurn * unit * bulge * bulge;

    // Hair tips: below the shoulders, more toward the bottom and the outer edges.
    float low = smoothstep(neck.y + 20.0 * unit, rigSize.y, p.y);
    float side = smoothstep(0.22, 0.45, abs(p.x / rigSize.x - 0.5));
    q.x -= sway * unit * low * side;

    return source.eval(q);
}
"""

/** A mood's pose and overlays, as in the shell's moodStyles. Angles and turns lean toward the chat. */
internal data class Style(
    val hop: Float = 0f, val wiggle: Float = 0f, val happyEyes: Boolean = false, val blush: Float = 0f,
    val breath: Float? = null, val nod: Float = 0f, val angle: Float = 0f, val turn: Float = 0f,
    val lids: Float = 0f, val lidsFor: Float? = null,
    val emote: String = "", val emoteX: Float = 176f, val emoteY: Float = -4f, val emoteStays: Boolean = false,
)

private val STYLES = mapOf(
    "happy" to Style(hop = 3f, wiggle = 1f, happyEyes = true, blush = 0.35f, emote = "✨", emoteX = 22f, emoteY = 6f),
    "excited" to Style(hop = 6f, wiggle = 1.6f, happyEyes = true, blush = 0.5f, breath = 2.4f, emote = "✨", emoteX = 22f, emoteY = 6f),
    "surprised" to Style(hop = 5f, nod = -1.5f, emote = "❗"),
    "curious" to Style(angle = 0.07f, turn = 1.2f, emote = "❓"),
    "worried" to Style(angle = -0.03f, nod = 1.2f, breath = 3.2f, emote = "💦", emoteX = 202f, emoteY = 80f),
    "sad" to Style(angle = -0.02f, nod = 2.6f, lids = 1f, breath = 5.5f),
    "pout" to Style(angle = -0.05f, turn = -3.2f, blush = 0.45f, emote = "💢"),
    "embarrassed" to Style(nod = 1.6f, turn = -2f, blush = 1f, lids = 0.8f, lidsFor = 2.5f, emote = "💦", emoteX = 202f, emoteY = 80f),
    "sleepy" to Style(angle = 0.05f, nod = 2f, lids = 1f, breath = 6f, emote = "💤", emoteStays = true),
)
val MOODS = STYLES.keys

private fun ease(value: Float, target: Float, rate: Float, dt: Float) = value + (target - value) * (1 - exp(-rate * dt))

/** Her pose, stepped every frame. Mirrors animate() and the blink and gesture timers in shell.qml. */
class Motion {
    var frame by mutableLongStateOf(0L); private set

    var headAngle = 0f; private set
    var headTurn = 0f; private set
    var headNod = 0f; private set
    var breath = 0f; private set
    var hairSway = 0f; private set
    var blink = 0f; private set
    var mouthLevel = 0f; private set
    var talking = 0f; private set
    var happyEyes = 0f; private set
    var halfLids = 0f; private set
    var blushLevel = 0f; private set
    var hop = 0f; private set
    var emoteScale = 0f; private set
    var emoteOpacity = 0f; private set
    var emoteFloat = 0f; private set
    var thinkingFace = 0f; private set
    var listeningFace = 0f; private set
    val moodShown = HashMap<String, Float>()

    var mood = "neutral"; private set
    internal val style: Style get() = STYLES[mood] ?: Style()

    private var clock = 0f
    private var moodAge = 0f
    private var breathPhase = 0f
    private var hairSwing = 0f
    private var hairVelocity = 0f
    private var gestureAngle = 0f
    private var gestureTurn = 0f
    private var nextGesture = 8f
    private var gestureEnds = Float.MAX_VALUE
    private var nextBlink = 2.5f
    private var blinkStart = -10f
    private var blinkLoops = 1
    private var lastState = ""

    /** [voice] is how loud her voice is right now, 0 to 1, or -1 when there's no voice and she mimes along. */
    fun step(dt: Float, state: String, speaking: Boolean, newMood: String, hasMoodArt: Boolean, voice: Float = -1f) {
        clock += dt
        val t = clock
        if (newMood != mood) { mood = newMood; moodAge = 0f }
        if (state == "listening" && lastState != "listening") nextBlink = t + 0.3f  // blinkSoon()
        lastState = state
        val side = 1f  // The chat is to her right on the phone.

        // No voice on the phone yet, so she flaps along while she's speaking, as the shell does without lip sync.
        val mouth = when {
            voice >= 0f -> voice
            speaking -> 0.55f + 0.45f * sin(t * 13f) * sin(t * 5.3f)
            else -> 0f
        }
        mouthLevel = ease(mouthLevel, mouth, if (mouth > mouthLevel) 30f else 14f, dt)

        // Occasional idle gestures: a curious tilt, or a glance to one side.
        if (t >= nextGesture) {
            nextGesture = t + 5f + Random.nextFloat() * 9f
            val way = if (Random.nextBoolean()) -1f else 1f
            val pick = Random.nextFloat()
            gestureAngle = if (pick < 0.4f) way * (0.03f + Random.nextFloat() * 0.04f) else 0f
            gestureTurn = if (pick >= 0.4f && pick < 0.75f) way * (1.5f + Random.nextFloat() * 1.5f) else 0f
            gestureEnds = t + 1.5f + Random.nextFloat() * 2f
        }
        if (t >= gestureEnds) { gestureAngle = 0f; gestureTurn = 0f; gestureEnds = Float.MAX_VALUE }

        // Never quite still: slow, out-of-step drifts, plus whatever gesture is under way.
        var angle = 0.018f * sin(t * 0.53f) + 0.012f * sin(t * 1.27f + 1.3f) + gestureAngle
        var turn = 1.0f * sin(t * 0.31f + 2.0f) + gestureTurn
        var nod = 0.4f * sin(t * 0.71f)
        when (state) {
            "listening" -> { angle += 0.045f * side; turn += 2.2f * side; nod += 0.8f }
            "thinking" -> { angle -= 0.06f * side; turn -= 2.5f * side; nod -= 1.2f }
            "speaking" -> { angle += 0.012f * sin(t * 2.3f); turn += 1.4f * side; nod += 1.6f * mouthLevel }
        }

        // Her mood leans on top of all that, most strongly just after it arrives.
        moodAge += dt
        val s = style
        angle += s.angle * side + s.wiggle * 0.03f * sin(t * 6f) * exp(-moodAge * 1.5f)
        turn += s.turn * side
        nod += s.nod
        hop = s.hop * abs(sin(moodAge * 9f)) * exp(-moodAge * 2.2f)
        // A painted face has its own eyes and blush. Only happy keeps the smiling ^^ eyes over it.
        val drawn = !hasMoodArt
        happyEyes = ease(happyEyes, if (s.happyEyes && moodAge < 1.8f && (drawn || mood == "happy")) 1f else 0f, 14f, dt)
        val lidsNow = s.lidsFor == null || moodAge < s.lidsFor
        halfLids = ease(halfLids, if (drawn && lidsNow) s.lids else 0f, 6f, dt)
        blushLevel = ease(blushLevel, if (drawn) s.blush else 0f, 3f, dt)
        talking = ease(talking, if (state == "speaking" || speaking || mouthLevel > 0.03f) 1f else 0f, 10f, dt)
        // Emotes pop in with a little overshoot, bob, and fade after 3 seconds unless they stay.
        emoteScale = min(1f, moodAge / 0.18f) * (1 + 0.25f * exp(-moodAge * 8f) * sin(moodAge * 30f))
        emoteOpacity = if (s.emote.isNotEmpty()) ((if (s.emoteStays) 1e9f else 3f) - moodAge).div(0.4f).coerceIn(0f, 1f) else 0f
        emoteFloat = 2f * sin(t * 2.2f)
        val previousAngle = headAngle
        headAngle = ease(headAngle, angle, 3.5f, dt)
        headTurn = ease(headTurn, turn, 3.5f, dt)
        headNod = ease(headNod, nod, 5f, dt)

        breathPhase += dt * 2f * PI.toFloat() / (s.breath ?: if (state == "speaking" || speaking) 3.0f else 4.2f)
        breath = 0.9f * (0.5f - 0.5f * cos(breathPhase))

        // Hair is a spring: it trails behind her head when it moves, then settles.
        hairVelocity += (-60f * hairSwing - 6f * hairVelocity) * dt - (headAngle - previousAngle) * 400f
        hairSwing = (hairSwing + hairVelocity * dt).coerceIn(-4f, 4f)
        hairSway = hairSwing + 0.5f * sin(t * 0.83f)

        // Blinks every few seconds, now and then twice: 60 ms shut, 50 ms closed, 100 ms open.
        if (t >= nextBlink) {
            blinkStart = t
            blinkLoops = if (Random.nextFloat() < 0.15f) 2 else 1
            nextBlink = t + 2.2f + Random.nextFloat() * 4.3f
        }
        val since = t - blinkStart
        blink = if (since < 0 || since >= 0.21f * blinkLoops) 0f else {
            val x = since % 0.21f
            when {
                x < 0.06f -> (x / 0.06f).let { it * it }
                x < 0.11f -> 1f
                else -> (1f - (x - 0.11f) / 0.1f).let { it * it }
            }
        }

        // The base faces fade in 140 ms, mood faces in 160 ms, as in the shell.
        thinkingFace = ease(thinkingFace, if (state == "thinking") 1f else 0f, 22f, dt)
        listeningFace = ease(listeningFace, if (state == "listening") 1f else 0f, 22f, dt)
        for (m in MOODS) {
            val now = moodShown[m] ?: 0f
            val next = ease(now, if (m == mood) 1f else 0f, 19f, dt)
            if (next < 0.005f && now < 0.005f) moodShown.remove(m) else moodShown[m] = next
        }
        frame++
    }
}

/** What she has on, as the PC says (backend occasions.py): an outfit folder under shell/assets/outfits or ""
 * for her usual dress, whether that outfit brings its own faces (Evil Alice's red eyes), and the birthday hat. */
data class Dress(val outfit: String = "", val faces: Boolean = false, val hat: Boolean = false)

/** Her art, decoded once and shared. A missing file just means that layer isn't drawn. */
private class Art(context: Context) {
    private val assets = context.assets
    private val cache = HashMap<String, ImageBitmap?>()
    private fun load(path: String): ImageBitmap? = cache.getOrPut(path) {
        runCatching { assets.open("avatar/$path").use { BitmapFactory.decodeStream(it).asImageBitmap() } }.getOrNull()
    }
    /** Her figure (neutral, thinking, listening, speaking), from the outfit when it has one. */
    fun body(name: String, dress: Dress): ImageBitmap? =
        dress.outfit.takeIf { it.isNotEmpty() }?.let { load("outfits/$it/alice-$name.png") } ?: load("alice-$name.png")
    /** A face layer, from the outfit only when it brings its own faces. */
    fun face(name: String, dress: Dress): ImageBitmap? =
        if (dress.outfit.isNotEmpty() && dress.faces) load("outfits/${dress.outfit}/alice-$name.png") else load("alice-$name.png")
    fun partyHat(): ImageBitmap? = load("party-hat.png")
    val ready: Boolean get() = load("alice-neutral.png") != null
}

/** How much of her the rig shows: her whole figure, or head and shoulders filling a circle. */
enum class Framing { FIGURE, HEAD }

/**
 * Alice, moving. [state] is "listening", "thinking" or "speaking"; her mouth moves until [speakingUntil]
 * (uptime ms); [mood] is one of [MOODS] or "neutral".
 */
@Composable
fun AliceRig(
    state: String, speakingUntil: Long, mood: String, framing: Framing, modifier: Modifier = Modifier,
    voiceLevel: () -> Float = { -1f }, dress: Dress = Dress(),
) {
    val context = LocalContext.current
    val art = remember { Art(context.applicationContext) }
    val motion = remember { Motion() }
    val shader = remember { RuntimeShader(RIG_SHADER) }
    val currentState by rememberUpdatedState(state)
    val currentSpeakingUntil by rememberUpdatedState(speakingUntil)
    val currentMood by rememberUpdatedState(if (mood in MOODS) mood else "neutral")
    val currentDress by rememberUpdatedState(dress)

    // The frame clock only ticks while the app is on screen, so she rests (and so does the battery) otherwise.
    LaunchedEffect(Unit) {
        var last = 0L
        while (true) {
            withFrameNanos { now ->
                val dt = if (last == 0L) 0f else min((now - last) / 1e9f, 0.1f)
                last = now
                val m = currentMood
                val voice = voiceLevel()
                val speaking = voice >= 0f || android.os.SystemClock.uptimeMillis() < currentSpeakingUntil
                motion.step(dt, currentState, speaking, m, m != "neutral" && art.face("mood-$m", currentDress) != null, voice)
            }
        }
    }

    if (!art.ready) {
        Box(modifier)
        return
    }
    when (framing) {
        Framing.FIGURE -> FittedRig(modifier) { RigLayer(motion, art, shader, it, dress) }
        Framing.HEAD -> HeadRig(modifier) { RigLayer(motion, art, shader, it, dress) }
    }
}

/** The rig scaled to fit its space, standing on the bottom edge. */
@Composable
private fun FittedRig(modifier: Modifier, content: @Composable (unit: Float) -> Unit) {
    val density = LocalDensity.current
    BoxWithConstraints(modifier) {
        val unit = with(density) { min(maxWidth.toPx() / RIG_W, maxHeight.toPx() / RIG_H) }
        val w = with(density) { (RIG_W * unit).toDp() }
        val h = with(density) { (RIG_H * unit).toDp() }
        Box(Modifier.fillMaxSize().wrapContentSize(Alignment.BottomCenter).requiredSize(w, h)) { content(unit) }
    }
}

/** Her head and shoulders, zoomed so her face fills a circle about a fifth of the way down. */
@Composable
private fun HeadRig(modifier: Modifier, content: @Composable (unit: Float) -> Unit) {
    val density = LocalDensity.current
    BoxWithConstraints(modifier) {
        val size = with(density) { min(maxWidth.toPx(), maxHeight.toPx()) }
        val unit = size / 172f  // Her head and bow, hair included.
        // Her face's centre, in rig points, goes to the middle of the circle, a little low so the bow shows.
        val faceX = MARGIN_SIDE + 120f
        val faceY = MARGIN_TOP + 112f
        val x = (size / 2 - faceX * unit).roundToInt()
        val y = (size * 0.6f - faceY * unit).roundToInt()
        val w = with(density) { (RIG_W * unit).toDp() }
        val h = with(density) { (RIG_H * unit).toDp() }
        Box(Modifier.wrapContentSize(Alignment.TopStart, unbounded = true).offset { IntOffset(x, y) }.requiredSize(w, h)) { content(unit) }
    }
}

@Composable
private fun RigLayer(motion: Motion, art: Art, shader: RuntimeShader, unit: Float, dress: Dress) {
    Canvas(
        Modifier.fillMaxSize().graphicsLayer {
            motion.frame  // Redrawn every frame she moves.
            translationY = -motion.hop * unit
            shader.setFloatUniform("rigSize", size.width, size.height)
            shader.setFloatUniform("neck", (MARGIN_SIDE + 120f) * unit, (MARGIN_TOP + 172f) * unit)
            shader.setFloatUniform("faceCentre", (MARGIN_SIDE + 120f) * unit, (MARGIN_TOP + 118f) * unit)
            shader.setFloatUniform("faceRadius", 46f * unit, 52f * unit)
            shader.setFloatUniform("unit", unit)
            shader.setFloatUniform("headAngle", motion.headAngle)
            shader.setFloatUniform("headTurn", motion.headTurn)
            shader.setFloatUniform("headNod", motion.headNod)
            shader.setFloatUniform("breath", motion.breath)
            shader.setFloatUniform("sway", motion.hairSway)
            renderEffect = RenderEffect.createRuntimeShaderEffect(shader, "source").asComposeRenderEffect()
        },
    ) {
        motion.frame
        drawFigure(motion, art, unit, dress)
    }
}

private fun DrawScope.drawFigure(motion: Motion, art: Art, unit: Float, dress: Dress) {
    val left = MARGIN_SIDE * unit
    val top = MARGIN_TOP * unit

    // Art is drawn at half its pixel size, centred and standing on the same spot, as in the shell, so outfits
    // on a taller or wider canvas keep her in place.
    fun origin(image: ImageBitmap) = Pair((ART_W - image.width / 2f) / 2f, ART_H - image.height / 2f)

    fun draw(image: ImageBitmap?, alpha: Float) {
        if (alpha <= 0.01f || image == null) return
        val (ox, oy) = origin(image)
        drawImage(
            image, IntOffset.Zero, IntSize(image.width, image.height),
            IntOffset((left + ox * unit).roundToInt(), (top + oy * unit).roundToInt()),
            IntSize((image.width / 2f * unit).roundToInt(), (image.height / 2f * unit).roundToInt()),
            alpha = alpha.coerceIn(0f, 1f),
        )
    }
    fun body(name: String, alpha: Float) = draw(art.body(name, dress), alpha)
    fun layer(name: String, alpha: Float) = draw(art.face(name, dress), alpha)

    // The neutral face is always drawn; thinking and listening fade in over it, so her outline never thins.
    body("neutral", 1f)
    body("thinking", motion.thinkingFace)
    body("listening", motion.listeningFace)

    // A painted face for her mood: the eyes stay while it lasts, the mouth gives way when hers moves.
    for ((m, shown) in motion.moodShown) {
        layer("mood-$m-eyes", shown)
        layer("mood-$m", shown * (1 - motion.talking))
    }

    // Drawn overlays, used where a mood has no painted face.
    layer("blush", motion.blushLevel)
    layer("halflid", motion.halfLids * (1 - motion.happyEyes))
    layer("happy", motion.happyEyes)

    // Her open mouth, cut from the speaking art and opened as wide as she talks. Every face shares the spot.
    val open = min(1f, motion.mouthLevel * 4)
    val speakingArt = art.body("speaking", dress)
    if (open > 0.01f && speakingArt != null) {
        val (ox, oy) = origin(speakingArt)
        val yScale = 0.35f + 0.65f * motion.mouthLevel
        val mx = 104f; val my = 137f; val mw = 33f; val mh = 14f
        val pivot = 2.5f  // It opens downward from her upper lip.
        val dstTop = my + pivot - pivot * yScale
        drawImage(
            speakingArt,
            srcOffset = IntOffset(((mx - ox) * 2).roundToInt(), ((my - oy) * 2).roundToInt()),
            srcSize = IntSize((mw * 2).roundToInt(), (mh * 2).roundToInt()),
            dstOffset = IntOffset((left + mx * unit).roundToInt(), (top + dstTop * unit).roundToInt()),
            dstSize = IntSize((mw * unit).roundToInt(), max(1, (mh * yScale * unit).roundToInt())),
            alpha = open,
        )
    }

    // Closed eyes. Smiling ^^ eyes are already shut.
    layer("blink", motion.blink * (1 - motion.happyEyes))

    // Her birthday hat, tilted over the right side of her head, unless the outfit has a hat or crown already.
    val hat = art.partyHat()
    if (dress.hat && hat != null && dress.outfit != "halloween" && dress.outfit != "queen") {
        val cx = left + (150f + 31f) * unit
        val cy = top + (-14f + 39f) * unit
        rotate(18f, androidx.compose.ui.geometry.Offset(cx, cy)) {
            drawImage(hat, IntOffset.Zero, IntSize(hat.width, hat.height),
                IntOffset((cx - 31f * unit).roundToInt(), (cy - 39f * unit).roundToInt()), IntSize((62f * unit).roundToInt(), (78f * unit).roundToInt()))
        }
    }

    // A little mark by her head for the mood: sparkles, a sweat drop, a "?".
    val s = motion.style
    if (s.emote.isNotEmpty() && motion.emoteOpacity > 0.01f) {
        val paint = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply {
            textSize = 28f * unit * motion.emoteScale
            alpha = (255 * motion.emoteOpacity).roundToInt()
            textAlign = android.graphics.Paint.Align.CENTER
        }
        val x = left + s.emoteX * unit
        val y = top + (s.emoteY + motion.emoteFloat) * unit + paint.textSize * 0.35f
        drawContext.canvas.nativeCanvas.drawText(s.emote, x, y, paint)
    }
}
