package com.lizongying.mytv

import com.lizongying.mytv.models.ProgramType

object SourceOptions {
    fun name(type: ProgramType, index: Int): String = when {
        type == ProgramType.F && index == 0 -> "官方动态线路"
        type == ProgramType.F -> "备用线路 $index"
        else -> "线路 ${index + 1}"
    }

    fun labels(type: ProgramType, count: Int, selected: Int): Array<String> =
        Array(count) { index -> name(type, index) + if (index == selected) "（当前，可重新连接）" else "" }
}
