package io.github.hiero.alice

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp

/** The app's one screen: Alice at the top, the conversation under her, and a row to type or talk. All the logic
 * lives in [AliceViewModel]; this file only draws it. */
class MainActivity : ComponentActivity() {
    private val vm: AliceViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        applySetup(intent)
        setContent { AliceTheme { AliceScreen(vm) } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        applySetup(intent)
    }

    override fun onStart() {
        super.onStart()
        vm.setForeground(true)
    }

    override fun onStop() {
        super.onStop()
        vm.setForeground(false)
    }

    /** Set up over adb: am start -n io.github.hiero.alice/.MainActivity --es host ... --ei port ... --es token ... */
    private fun applySetup(intent: Intent?) {
        val extras = intent?.extras ?: return
        if (extras.containsKey("token") || extras.containsKey("host")) {
            vm.configure(extras.getString("host"), extras.getInt("port", 0).takeIf { it > 0 }, extras.getString("token"))
        }
        // Debug builds only, for testing over adb: --es mode PHONE, --es say "hello".
        if (BuildConfig.DEBUG) {
            extras.getString("mode")?.let { runCatching { vm.changeMode(Mode.valueOf(it.uppercase())) } }
            extras.getString("say")?.let { vm.sendMessage(it) }
            extras.getString("outfit")?.let { vm.tryOn(it, extras.getBoolean("hat", false)) }
            extras.getString("backend")?.let { vm.setModelSetup(it) }
            extras.getString("probe")?.let { vm.probe(it) }
            if (extras.getBoolean("talk", false)) vm.talk()
        }
    }
}

private val Ink = Color(0xFF2E2A3A)
private val Paper = Color(0xFFFBF6EC)
private val Ribbon = Color(0xFF4A6FA5)

@Composable
fun AliceTheme(content: @Composable () -> Unit) {
    val colors = if (isSystemInDarkTheme()) {
        darkColorScheme(primary = Color(0xFF9DB8E6), background = Color(0xFF1B1922), surface = Color(0xFF1B1922), surfaceVariant = Color(0xFF2A2734))
    } else {
        lightColorScheme(primary = Ribbon, background = Paper, surface = Paper, surfaceVariant = Color(0xFFEDE4D3), onBackground = Ink, onSurface = Ink)
    }
    MaterialTheme(colorScheme = colors, content = content)
}

@Composable
fun AliceScreen(vm: AliceViewModel) {
    var showSettings by rememberSaveable { mutableStateOf(false) }
    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        BoxWithConstraints(Modifier.fillMaxSize().safeDrawingPadding().imePadding()) {
            val wide = maxWidth
            if (wide >= 600.dp) {
                // Unfolded: her portrait and controls on the left, the conversation on the right.
                Row(Modifier.fillMaxSize()) {
                    Column(Modifier.width(wide * 0.36f).fillMaxHeight().padding(16.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                        AliceRig(vm.stage, vm.speakingUntil, vm.mood, Framing.FIGURE, Modifier.weight(1f).fillMaxWidth(), vm.voice::mouthLevel, vm.dress)
                        Spacer(Modifier.size(12.dp))
                        StatusLine(vm)
                        Spacer(Modifier.size(12.dp))
                        ModeSwitch(vm)
                        Row {
                            VoiceToggle(vm)
                            TextButton(onClick = { showSettings = true }) { Text("Setup") }
                        }
                    }
                    Conversation(vm, Modifier.weight(1f))
                }
            } else {
                Column(Modifier.fillMaxSize()) {
                    Header(vm, onSettings = { showSettings = true })
                    Conversation(vm, Modifier.weight(1f))
                }
            }
        }
    }
    if (showSettings || vm.connection == Connection.NOT_SET_UP) {
        SettingsDialog(vm, onDismiss = { showSettings = false })
    }
}

@Composable
private fun Conversation(vm: AliceViewModel, modifier: Modifier) {
    Column(modifier.fillMaxHeight()) {
        Messages(vm, Modifier.weight(1f))
        vm.confirm?.let { ConfirmPanel(it, vm::answerConfirm) }
        if (vm.status.isNotEmpty()) {
            Text(vm.status, Modifier.padding(horizontal = 16.dp, vertical = 4.dp), style = MaterialTheme.typography.bodySmall, fontStyle = FontStyle.Italic)
        }
        InputRow(vm)
    }
}

@Composable
private fun Header(vm: AliceViewModel, onSettings: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, top = 12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(88.dp).clip(CircleShape).background(MaterialTheme.colorScheme.surfaceVariant)) {
                AliceRig(vm.stage, vm.speakingUntil, vm.mood, Framing.HEAD, Modifier.fillMaxSize(), vm.voice::mouthLevel, vm.dress)
            }
            Spacer(Modifier.size(12.dp))
            Column(Modifier.weight(1f)) {
                Text("Alice", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)
                StatusLine(vm)
            }
            VoiceToggle(vm)
            TextButton(onClick = onSettings) { Text("Setup") }
        }
        Spacer(Modifier.size(8.dp))
        ModeSwitch(vm, Modifier.fillMaxWidth())
    }
}

@Composable
private fun StatusLine(vm: AliceViewModel) {
    val (label, color) = when {
        vm.mode == Mode.PHONE -> "on your phone" to Color(0xFF9575CD)
        vm.connection == Connection.ONLINE -> (if (vm.thinking) "thinking…" else "on your PC") to Color(0xFF4CAF50)
        vm.connection == Connection.NOT_SET_UP -> "not set up" to Color.Gray
        vm.connection == Connection.CONNECTING -> "reaching your PC…" to Color(0xFFFFB300)
        vm.mode == Mode.AUTO -> "on your phone · PC out of reach" to Color(0xFF9575CD)
        else -> "PC out of reach" to Color(0xFFE57373)
    }
    // On the phone's side, say how her own model is doing.
    val onPhone = vm.mode == Mode.PHONE || (vm.mode == Mode.AUTO && vm.connection != Connection.ONLINE && vm.connection != Connection.CONNECTING)
    val light = if (vm.phoneModel.loadedBrain == "light" || (vm.phoneModel.loadedBrain.isEmpty() && vm.currentBrain() == "light")) " · light" else ""
    val model = light + when (vm.phoneModel.state) {
        PhoneModel.State.LOADING -> " · waking up…"
        PhoneModel.State.MISSING -> " · no model"
        PhoneModel.State.FAILED -> " · model failed"
        else -> ""
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(color))
        Spacer(Modifier.size(6.dp))
        Text(label + if (onPhone) model else "", style = MaterialTheme.typography.bodyMedium)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun BrainSwitch(vm: AliceViewModel) {
    val choices = listOf("auto" to "Auto", "full" to "Full", "light" to "Light")
    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth().padding(top = 4.dp)) {
        choices.forEachIndexed { index, (key, label) ->
            SegmentedButton(selected = vm.brainChoice == key, onClick = { vm.chooseBrain(key) }, shape = SegmentedButtonDefaults.itemShape(index, choices.size)) { Text(label) }
        }
    }
}

/** Her voice on or off. Text always comes through either way. */
@Composable
private fun VoiceToggle(vm: AliceViewModel) {
    TextButton(onClick = { vm.setVoice(!vm.voiceOn) }) { Text(if (vm.voiceOn) "🔊" else "🔇", style = MaterialTheme.typography.titleLarge) }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ModeSwitch(vm: AliceViewModel, modifier: Modifier = Modifier) {
    val modes = listOf(Mode.AUTO to "Auto", Mode.PC to "PC", Mode.PHONE to "Phone")
    SingleChoiceSegmentedButtonRow(modifier) {
        modes.forEachIndexed { index, (mode, label) ->
            SegmentedButton(
                selected = vm.mode == mode,
                onClick = { vm.changeMode(mode) },
                shape = SegmentedButtonDefaults.itemShape(index, modes.size),
            ) { Text(label) }
        }
    }
}

@Composable
private fun Messages(vm: AliceViewModel, modifier: Modifier) {
    val list = rememberLazyListState()
    val last = vm.items.lastOrNull()
    LaunchedEffect(vm.items.size, last?.text?.length) {
        if (vm.items.isNotEmpty()) list.animateScrollToItem(vm.items.lastIndex)
    }
    LazyColumn(modifier.fillMaxWidth(), state = list, verticalArrangement = Arrangement.spacedBy(8.dp), contentPadding = PaddingValues(12.dp)) {
        itemsIndexed(vm.items) { _, item -> Bubble(item, vm::openLink) }
    }
}

@Composable
private fun Bubble(item: ChatItem, openLink: (String) -> Unit) {
    Box(Modifier.fillMaxWidth(), contentAlignment = if (item.fromUser) Alignment.CenterEnd else Alignment.CenterStart) {
        val colors = when {
            item.isError -> CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer)
            item.fromUser -> CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primary.copy(alpha = 0.18f))
            else -> CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)
        }
        Card(Modifier.widthIn(max = 560.dp), shape = RoundedCornerShape(16.dp), colors = colors) {
            Column(Modifier.padding(12.dp)) {
                if (item.text.isNotEmpty()) Text(item.text, style = MaterialTheme.typography.bodyLarge)
                if (item.links.isNotEmpty()) {
                    if (item.linksTitle.isNotEmpty()) Text(item.linksTitle, Modifier.padding(top = 8.dp), style = MaterialTheme.typography.labelLarge)
                    item.links.forEach { link ->
                        Column(Modifier.fillMaxWidth().clickable { openLink(link.url) }.padding(vertical = 6.dp)) {
                            Text(link.name, color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.bodyMedium)
                            if (link.detail.isNotEmpty()) Text(link.detail, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                }
                if (item.onPhone && !item.fromUser) {
                    Text("on phone", Modifier.padding(top = 4.dp), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurface.copy(alpha = 0.5f))
                }
            }
        }
    }
}

@Composable
private fun ConfirmPanel(card: ConfirmCard, answer: (Boolean) -> Unit) {
    Card(Modifier.fillMaxWidth().padding(12.dp), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.tertiaryContainer)) {
        Column(Modifier.padding(12.dp)) {
            Text(card.title, style = MaterialTheme.typography.titleMedium)
            if (card.detail.isNotEmpty()) Text(card.detail, Modifier.padding(top = 4.dp), style = MaterialTheme.typography.bodyMedium)
            Row(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { answer(true) }) { Text("Allow") }
                OutlinedButton(onClick = { answer(false) }) { Text("Deny") }
            }
        }
    }
}

@Composable
private fun InputRow(vm: AliceViewModel) {
    var text by rememberSaveable { mutableStateOf("") }
    val submit = { if (!vm.thinking) { vm.sendMessage(text); text = "" } }
    val context = LocalContext.current
    val askMic = androidx.activity.compose.rememberLauncherForActivityResult(androidx.activity.result.contract.ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) vm.talk()
    }
    val talk = {
        val allowed = androidx.core.content.ContextCompat.checkSelfPermission(context, android.Manifest.permission.RECORD_AUDIO) == android.content.pm.PackageManager.PERMISSION_GRANTED
        if (allowed) vm.talk() else askMic.launch(android.Manifest.permission.RECORD_AUDIO)
    }
    Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
        OutlinedTextField(
            text, { text = it }, Modifier.weight(1f),
            placeholder = { Text("Talk to Alice…") },
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
            keyboardActions = KeyboardActions(onSend = { submit() }),
            maxLines = 4,
        )
        Spacer(Modifier.size(4.dp))
        if (text.isBlank()) {
            // Empty box: the button talks instead, as the shell's talk key does.
            Button(onClick = talk, enabled = !vm.thinking || vm.listening) { Text(if (vm.listening) "⏹" else "🎤") }
        } else {
            Button(onClick = submit, enabled = !vm.thinking) { Text("Send") }
        }
    }
}

@Composable
private fun SettingsDialog(vm: AliceViewModel, onDismiss: () -> Unit) {
    var host by remember { mutableStateOf(vm.host) }
    var port by remember { mutableStateOf(vm.port.toString()) }
    var token by remember { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Reach your PC") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Your PC's Tailscale address and the token in ~/.config/alice/phone-token.", style = MaterialTheme.typography.bodySmall)
                OutlinedTextField(host, { host = it }, label = { Text("Address") }, singleLine = true)
                OutlinedTextField(port, { port = it }, label = { Text("Port") }, singleLine = true)
                OutlinedTextField(token, { token = it }, label = { Text(if (vm.token.isEmpty()) "Token" else "Token (leave empty to keep)") }, singleLine = true)
                if (vm.lightInstalled) Column {
                    Text("Phone brain", style = MaterialTheme.typography.bodyMedium)
                    Text("Light only chats and does plain commands, but spares the phone. Auto turns light when the phone is warm or the battery low.", style = MaterialTheme.typography.bodySmall)
                    BrainSwitch(vm)
                }
                if (vm.phoneVoiceInstalled) Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Voice when offline", style = MaterialTheme.typography.bodyMedium)
                        Text("Her voice made on the phone. Slower, warmer and heavier on memory.", style = MaterialTheme.typography.bodySmall)
                    }
                    androidx.compose.material3.Switch(vm.phoneVoiceOn, vm::setPhoneVoice)
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text("Debug log", style = MaterialTheme.typography.bodyMedium)
                        Text("Keeps what she was asked and did in the app's files, for tracking down problems.", style = MaterialTheme.typography.bodySmall)
                    }
                    androidx.compose.material3.Switch(vm.debugLog, vm::setDebugLogging)
                }
            }
        },
        confirmButton = {
            TextButton(onClick = { vm.configure(host, port.toIntOrNull(), token); onDismiss() }) { Text("Save") }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } },
    )
}
