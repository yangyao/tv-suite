package com.lizongying.mytv

import com.lizongying.mytv.models.ProgramType

object PhoenixChannels {
    data class Channel(val title: String, val liveId: String, val aliases: Set<String>)

    val channels = listOf(
        Channel("凤凰资讯", "7c96b084-60e1-40a9-89c5-682b994fb680", setOf("凤凰资讯", "凤凰资讯台", "凤凰卫视资讯台", "鳳凰資訊", "鳳凰資訊台", "鳳凰衛視資訊台")),
        Channel("凤凰中文", "f7f48462-9b13-485b-8101-7b54716411ec", setOf("凤凰中文", "凤凰中文台", "凤凰卫视中文台", "鳳凰中文", "鳳凰中文台", "鳳凰衛視中文台")),
        Channel("凤凰香港", "15e02d92-1698-416c-af2f-3e9a872b4d78", setOf("凤凰香港", "凤凰香港台", "凤凰卫视香港台", "鳳凰香港", "鳳凰香港台", "鳳凰衛視香港台")),
    )

    fun merge(subscription: Map<String, List<TV>>): Map<String, List<TV>> {
        val rows = subscription.values.flatten()
        val result = linkedMapOf<String, List<TV>>()
        result["凤凰卫视"] = channels.map { channel ->
            val matches = rows.filter { it.title in channel.aliases }
            TV(
                title = channel.title,
                alias = channel.title,
                videoUrl = listOf("https://m.fengshows.com/api/v3/hub/live/auth-url?live_id=${channel.liveId}&live_qa=HD") +
                    matches.flatMap { it.videoUrl }.distinct(),
                channel = "凤凰卫视",
                logo = matches.firstOrNull()?.logo ?: R.drawable.logo,
                pid = channel.liveId,
                programType = ProgramType.F,
            )
        }
        subscription.forEach { (group, items) ->
            val remaining = items.filter { tv -> channels.none { tv.title in it.aliases } }
            if (remaining.isNotEmpty()) result[group] = result[group].orEmpty() + remaining
        }
        var id = 0
        return result.mapValues { (_, items) -> items.map { it.copy(id = id++) } }
    }
}
