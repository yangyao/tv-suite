package com.lizongying.mytv

import com.lizongying.mytv.models.ProgramType
import org.junit.Assert.*
import org.junit.Test

class ConfirmKeyGestureTest {
    @Test fun shortPressWaitsForReleaseAndDoesNotRepeat() {
        val gesture = ConfirmKeyGesture()
        assertTrue(gesture.down(false))
        assertEquals(ConfirmKeyGesture.Action.SHORT_PRESS, gesture.up(false))
        assertEquals(ConfirmKeyGesture.Action.NONE, gesture.up(false))
        assertEquals(ConfirmKeyGesture.Action.NONE, gesture.timeout())
    }

    @Test fun longPressOpensOnceAndNeverAlsoTriggersShortPress() {
        val gesture = ConfirmKeyGesture()
        gesture.down(false)
        assertFalse(gesture.down(true))
        assertEquals(ConfirmKeyGesture.Action.LONG_PRESS, gesture.timeout())
        assertEquals(ConfirmKeyGesture.Action.NONE, gesture.timeout())
        assertEquals(ConfirmKeyGesture.Action.NONE, gesture.up(false))
    }

    @Test fun cancelledOrInterruptedPressDoesNotOpenAnything() {
        val gesture = ConfirmKeyGesture()
        assertFalse(gesture.down(true))
        assertEquals(ConfirmKeyGesture.Action.NONE, gesture.timeout())
        gesture.down(false)
        assertEquals(ConfirmKeyGesture.Action.NONE, gesture.up(true))
        gesture.down(false)
        gesture.cancel()
        assertEquals(ConfirmKeyGesture.Action.NONE, gesture.timeout())
        assertEquals(ConfirmKeyGesture.Action.NONE, gesture.up(false))
        assertTrue(gesture.down(false))
        assertEquals(ConfirmKeyGesture.Action.SHORT_PRESS, gesture.up(false))
    }

    @Test fun officialAndBackupLabelsDoNotExposePlaybackUrls() {
        val labels = SourceOptions.labels(ProgramType.F, 3, 1)
        assertEquals("官方动态线路", labels[0])
        assertEquals("备用线路 1（当前，可重新连接）", labels[1])
        assertEquals("备用线路 2", labels[2])
        assertEquals("线路 1（当前，可重新连接）", SourceOptions.labels(ProgramType.DIRECT, 1, 0).single())
    }
}
