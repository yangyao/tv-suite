package com.lizongying.mytv

import org.junit.Assert.*
import org.junit.Test

class PlaylistRepositoryTest {
    @Test fun groupsAndMergesInterleavedChannelsWithContiguousIds() {
        val result = PlaylistRepository.parse("""
            #EXTM3U
            #EXTINF:-1 group-title="央视" tvg-logo="https://example.com/logo.png",CCTV-9
            https://example.com/9.m3u8
            #EXTINF:-1 group-title="卫视",河北卫视
            https://example.com/hebei1.m3u8
            #EXTINF:-1 group-title="央视",CCTV-11
            https://example.com/11.m3u8
            #EXTINF:-1 group-title="卫视",河北卫视
            https://example.com/hebei2.m3u8
            #EXTINF:-1 group-title="卫视",河北卫视
            https://example.com/hebei2.m3u8
        """.trimIndent())
        assertEquals(listOf("央视", "卫视"), result.keys.toList())
        assertEquals(listOf(0, 1, 2), result.values.flatten().map { it.id })
        assertEquals(2, result.getValue("卫视").single().videoUrl.size)
        assertEquals("https://example.com/logo.png", result.getValue("央视").first().logo)
        assertTrue(result.values.flatten().all { it.pid.isEmpty() })
    }

    @Test fun handlesBomCrLfAndCommasInsideAttributes() {
        val text = "\uFEFF#EXTM3U\r\n#EXTINF:-1 group-title=\"News, Live\" tvg-name=\"News\",\r\nhttps://example.com/news.m3u8\r\n"
        assertEquals("News", PlaylistRepository.parse(text).getValue("News, Live").single().title)
    }

    @Test fun ignoresEmptyOrNonPlaylistResponses() {
        assertTrue(PlaylistRepository.parse("<html>not ready</html>").isEmpty())
        assertTrue(PlaylistRepository.parse("#EXTM3U\n#EXTINF:-1,No URL").isEmpty())
    }
}
