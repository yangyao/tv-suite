package com.lizongying.mytv

class ConfirmKeyGesture {
    enum class Action { NONE, SHORT_PRESS, LONG_PRESS }

    var isPressed = false
        private set
    private var longPressSent = false

    fun down(repeat: Boolean): Boolean {
        if (isPressed || repeat) return false
        isPressed = true
        longPressSent = false
        return true
    }

    fun timeout(): Action {
        if (!isPressed || longPressSent) return Action.NONE
        longPressSent = true
        return Action.LONG_PRESS
    }

    fun up(cancelled: Boolean): Action {
        val action = if (isPressed && !longPressSent && !cancelled) Action.SHORT_PRESS else Action.NONE
        cancel()
        return action
    }

    fun cancel() {
        isPressed = false
        longPressSent = false
    }
}
