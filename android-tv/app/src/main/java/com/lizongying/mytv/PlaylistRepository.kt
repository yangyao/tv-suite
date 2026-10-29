package com.lizongying.mytv

import android.content.Context
import android.util.AtomicFile
import android.util.Log
import com.lizongying.mytv.models.ProgramType
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.util.concurrent.TimeUnit

object PlaylistRepository {
    const val DEFAULT_URL = BuildConfig.PLAYLIST_URL
    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .callTimeout(15, TimeUnit.SECONDS)
        .build()

    data class Result(val channels: Map<String, List<TV>>, val cached: Boolean)

    suspend fun load(context: Context): Result = withContext(Dispatchers.IO) {
        val cache = AtomicFile(File(context.filesDir, "subscription.m3u"))
        try {
            val text = client.newCall(Request.Builder().url(DEFAULT_URL).build()).execute().use {
                check(it.isSuccessful && it.code() != 202) { "订阅尚未生成或服务不可用" }
                it.body()?.string().orEmpty()
            }
            val channels = parse(text)
            check(channels.isNotEmpty()) { "订阅中没有可播放频道" }
            try {
                val stream = cache.startWrite()
                try {
                    stream.write(text.toByteArray(Charsets.UTF_8))
                    cache.finishWrite(stream)
                } catch (e: Exception) {
                    cache.failWrite(stream)
                    throw e
                }
            } catch (e: Exception) {
                Log.w("PlaylistRepository", "Unable to cache subscription", e)
            }
            Log.i("PlaylistRepository", "Loaded ${channels.values.sumOf { it.size }} channels from $DEFAULT_URL")
            Result(channels, false)
        } catch (e: Exception) {
            Log.w("PlaylistRepository", "Subscription unavailable; trying cache", e)
            val channels = runCatching { parse(cache.openRead().bufferedReader().use { it.readText() }) }
                .getOrDefault(emptyMap())
            check(channels.isNotEmpty()) { "无法加载频道，请确认路由器订阅服务可用" }
            Result(channels, true)
        }
    }

    fun parse(text: String): Map<String, List<TV>> {
        val groups = linkedMapOf<String, LinkedHashMap<String, TV>>()
        val attributes = Regex("""([\w-]+)="([^"]*)""" + "\"")
        var title = ""
        var group = "订阅频道"
        var logo = ""
        var id = 0
        for (raw in text.lineSequence()) {
            val line = raw.trim().removePrefix("\uFEFF")
            if (line.startsWith("#EXTINF:", true)) {
                val attrs = attributes.findAll(line).associate { it.groupValues[1] to it.groupValues[2] }
                // The title separator is the first comma outside a quoted attribute.
                var quoted = false
                val separator = line.indexOfFirst { c ->
                    if (c == '"') quoted = !quoted
                    c == ',' && !quoted
                }
                title = if (separator >= 0) line.substring(separator + 1).trim() else ""
                if (title.isBlank()) title = attrs["tvg-name"].orEmpty()
                group = attrs["group-title"]?.takeIf { it.isNotBlank() } ?: "订阅频道"
                logo = attrs["tvg-logo"].orEmpty()
            } else if (line.startsWith("http://", true) || line.startsWith("https://", true)) {
                if (title.isNotBlank()) {
                    val channels = groups.getOrPut(group) { linkedMapOf() }
                    val existing = channels[title]
                    if (existing == null) {
                        channels[title] = TV(id++, title, videoUrl = listOf(line), channel = group,
                            logo = if (logo.isBlank()) R.drawable.logo else logo,
                            programType = ProgramType.DIRECT)
                    } else if (line !in existing.videoUrl) {
                        existing.videoUrl = existing.videoUrl + line
                    }
                }
                title = ""
            }
        }
        // IDs must follow the same order as the grouped rows.
        id = 0
        return groups.mapValues { (_, channels) -> channels.values.onEach { it.id = id++ }.toList() }
    }
}
