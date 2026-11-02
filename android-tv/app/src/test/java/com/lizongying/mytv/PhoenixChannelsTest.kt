package com.lizongying.mytv

import com.lizongying.mytv.models.ProgramType
import org.junit.Assert.*
import org.junit.Test

class PhoenixChannelsTest {
    @Test fun phoenixRemainsAvailableWithoutSubscriptionAndKeepsStableIds() {
        val result = PhoenixChannels.merge(emptyMap())
        assertEquals(listOf("凤凰卫视"), result.keys.toList())
        assertEquals(listOf("凤凰资讯", "凤凰中文", "凤凰香港"), result.values.flatten().map { it.title })
        assertEquals(listOf(0, 1, 2), result.values.flatten().map { it.id })
        assertTrue(result.values.flatten().all { it.programType == ProgramType.F && it.pid.isNotEmpty() })
    }

    @Test fun mergesTraditionalAliasesAndRetainsPublicFallbacksWithoutMutatingSubscription() {
        val original = PlaylistRepository.parse("""
            #EXTM3U
            #EXTINF:-1 group-title="新闻",鳳凰衛視香港台
            https://example.com/hk.m3u8
            #EXTINF:-1 group-title="新闻",凤凰香港
            https://example.com/hk.m3u8
            #EXTINF:-1 group-title="新闻",新闻台
            https://example.com/news.m3u8
        """.trimIndent())
        val result = PhoenixChannels.merge(original)
        assertEquals(listOf("凤凰卫视", "新闻"), result.keys.toList())
        assertEquals(2, result.getValue("凤凰卫视")[2].videoUrl.size)
        assertEquals("https://example.com/hk.m3u8", result.getValue("凤凰卫视")[2].videoUrl.last())
        assertEquals(listOf(0, 1, 2, 3), result.values.flatten().map { it.id })
        assertEquals("新闻台", result.getValue("新闻").single().title)
        assertEquals(2, original.getValue("新闻").last().id)
    }

    @Test fun validatesOfficialResponsesAndPreservesExpiringSignature() {
        val url = "http://dispatch.fengshows.cn/live/test.flv?txSecret=test&txTime=FFFFFFFF"
        assertEquals(url, PhoenixResolver.playbackUrl("""{"data":{"live_url":"$url"}}"""))
        for (invalid in listOf("https://example.com/live.flv", "https://fengshows.cn.example.com/live.flv", "https://user:pass@dispatch.fengshows.cn/live.flv", "file:///test")) {
            assertThrows(Exception::class.java) {
                PhoenixResolver.playbackUrl("""{"data":{"live_url":"$invalid"}}""")
            }
        }
        assertThrows(Exception::class.java) { PhoenixResolver.playbackUrl("""{"data":{}}""") }
    }
}
