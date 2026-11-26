plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// Her faces are copied from the shell at build time: your own painted art in shell/assets (not in git) when it's
// there, or else her default art from shell/default-art. Never a mix, since the faces must line up pixel for pixel.
val avatarDir = layout.buildDirectory.dir("generated/avatar")
val copyAvatar by tasks.registering(Copy::class) {
    val painted = rootProject.file("../shell/assets")
    from(if (painted.resolve("alice-neutral.png").exists()) painted else rootProject.file("../shell/default-art")) {
        include("alice-*.png", "party-hat.png", "outfits/*/alice-*.png")
    }
    into(avatarDir.map { it.dir("avatar") })
}

// Same for the launcher icon (tools/make_phone_icon.py): your own in android/icon, or else the default one.
val iconDir = layout.buildDirectory.dir("generated/icon")
val copyIcon by tasks.registering(Sync::class) {
    val painted = rootProject.file("icon")
    from(if (painted.isDirectory) painted else rootProject.file("default-icon"))
    into(iconDir)
}

val sherpaVersion = "2.0.1"
val sherpaAar = file("libs/sherpa-onnx-static-link-onnxruntime-$sherpaVersion.aar")
if (!sherpaAar.exists()) {
    sherpaAar.parentFile.mkdirs()
    uri("https://github.com/k2-fsa/sherpa-onnx/releases/download/v$sherpaVersion/${sherpaAar.name}").toURL().openStream().use { input ->
        sherpaAar.outputStream().use { input.copyTo(it) }
    }
}

android {
    namespace = "io.github.hiero.alice"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.hiero.alice"
        minSdk = 33
        targetSdk = 35
        versionCode = 2
        versionName = "0.1.1"
        ndk { abiFilters += "arm64-v8a" }  // The model runtime's native code, for phones only.
        // The PC's address the app tries before one is set in the app or over adb. Empty in public builds;
        // set aliceDefaultHost in ~/.gradle/gradle.properties to bake in your own.
        val defaultHost = providers.gradleProperty("aliceDefaultHost").getOrElse("")
        buildConfigField("String", "DEFAULT_HOST", "\"$defaultHost\"")
    }
    // Release builds are signed with the key in ALICE_KEYSTORE (a .jks file) and ALICE_KEYSTORE_PASSWORD, taken from
    // the environment or ~/.gradle/gradle.properties. One key for every release lets a new APK install over the old one.
    // Without it they fall back to this computer's debug key, which is fine for trying the app out.
    val keystore = providers.environmentVariable("ALICE_KEYSTORE").orElse(providers.gradleProperty("ALICE_KEYSTORE")).orNull
    val keystorePassword = providers.environmentVariable("ALICE_KEYSTORE_PASSWORD")
        .orElse(providers.gradleProperty("ALICE_KEYSTORE_PASSWORD")).orNull
    if (keystore != null && keystorePassword != null) {
        signingConfigs.create("release") {
            storeFile = file(keystore)
            storePassword = keystorePassword
            keyAlias = "alice"
            keyPassword = keystorePassword
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }
    sourceSets.getByName("main").assets.srcDir(avatarDir)
    sourceSets.getByName("main").res.srcDir(iconDir)
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures { compose = true; buildConfig = true }
}

kotlin { compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }

tasks.named("preBuild").configure { dependsOn(copyAvatar, copyIcon) }

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.12.01"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("com.google.ai.edge.litertlm:litertlm-android:0.18.0")
    // Kokoro on the phone (sherpa-onnx, with ONNX Runtime built in). Not on Maven, so it's fetched once into
    // app/libs, which git ignores: https://github.com/k2-fsa/sherpa-onnx/releases
    implementation(files(sherpaAar))
    testImplementation("junit:junit:4.13.2")
}
