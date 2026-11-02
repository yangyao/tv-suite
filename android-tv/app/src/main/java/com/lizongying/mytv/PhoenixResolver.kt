package com.lizongying.mytv

import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import com.google.gson.JsonParser
import java.io.IOException
import java.net.Proxy
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

object PhoenixResolver {
    private val client = OkHttpClient.Builder()
        .proxy(Proxy.NO_PROXY)
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(8, TimeUnit.SECONDS)
        .callTimeout(12, TimeUnit.SECONDS)
        .build()

    internal fun playbackUrl(json: String): String {
        val raw = JsonParser.parseString(json).asJsonObject.getAsJsonObject("data")
            ?.get("live_url")?.asString.orEmpty()
        val url = HttpUrl.parse(raw) ?: throw IOException("凤凰播放地址为空或无效")
        if (!(url.host().endsWith(".fengshows.cn") || url.host().endsWith(".fengshows.com")) ||
            url.username().isNotEmpty() || url.password().isNotEmpty()) {
            throw IOException("凤凰播放地址无效")
        }
        return url.toString()
    }

    suspend fun resolve(liveId: String): String {
        require(PhoenixChannels.channels.any { it.liveId == liveId })
        val endpoint = HttpUrl.parse("https://m.fengshows.com/api/v3/hub/live/auth-url")!!
            .newBuilder().addQueryParameter("live_id", liveId)
            .addQueryParameter("live_qa", "HD").build()
        return suspendCancellableCoroutine { continuation ->
            val call = client.newCall(Request.Builder().url(endpoint)
                .header("User-Agent", "Mozilla/5.0")
                .header("Referer", "https://m.fengshows.com/").build())
            continuation.invokeOnCancellation { call.cancel() }
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    if (continuation.isActive) continuation.resumeWithException(e)
                }

                override fun onResponse(call: Call, response: Response) {
                    response.use {
                        if (!continuation.isActive) return
                        try {
                            if (!it.isSuccessful) throw IOException("凤凰取源失败：HTTP ${it.code()}")
                            val text = it.body()?.string() ?: throw IOException("凤凰取源返回为空")
                            continuation.resume(playbackUrl(text))
                        } catch (e: Exception) {
                            if (continuation.isActive) continuation.resumeWithException(e)
                        }
                    }
                }
            })
        }
    }
}
