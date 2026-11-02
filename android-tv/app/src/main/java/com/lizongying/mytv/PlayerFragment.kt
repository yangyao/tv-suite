package com.lizongying.mytv

import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.ViewGroup
import android.view.ViewTreeObserver
import androidx.annotation.OptIn
import androidx.fragment.app.Fragment
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.VideoSize
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import com.google.android.exoplayer2.SimpleExoPlayer
import com.lizongying.mytv.databinding.PlayerBinding
import com.lizongying.mytv.models.TVViewModel
import com.lizongying.mytv.models.ProgramType
import androidx.lifecycle.lifecycleScope
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch


class PlayerFragment : Fragment(), SurfaceHolder.Callback {

    private var _binding: PlayerBinding? = null
    private var playerView: PlayerView? = null
    private var tvViewModel: TVViewModel? = null
    private val aspectRatio = 16f / 9f


    private lateinit var surfaceView: SurfaceView
    private lateinit var surfaceHolder: SurfaceHolder
    private var exoPlayer: SimpleExoPlayer? = null
    private var resolveJob: Job? = null
    private var playGeneration = 0
    private var officialRetried = false

    override fun onCreateView(
        inflater: LayoutInflater, container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        _binding = PlayerBinding.inflate(inflater, container, false)

        if (Utils.isTmallDevice()) {
            _binding!!.playerView.visibility = View.GONE
            surfaceView = _binding!!.surfaceView
            surfaceHolder = surfaceView.holder
            surfaceHolder.addCallback(this)
        } else {
            _binding!!.surfaceView.visibility = View.GONE
            playerView = _binding!!.playerView
        }

        playerView?.viewTreeObserver?.addOnGlobalLayoutListener(object :
            ViewTreeObserver.OnGlobalLayoutListener {
            override fun onGlobalLayout() {
                playerView!!.viewTreeObserver.removeOnGlobalLayoutListener(this)
                playerView!!.player = activity?.let {
                    ExoPlayer.Builder(it)
                        .build()
                }
                playerView!!.player?.playWhenReady = true
                playerView!!.player?.addListener(object : Player.Listener {
                    override fun onVideoSizeChanged(videoSize: VideoSize) {
                        val ratio = playerView?.measuredWidth?.div(playerView?.measuredHeight!!)
                        if (ratio != null) {
                            val layoutParams = playerView?.layoutParams
                            if (ratio < aspectRatio) {
                                layoutParams?.height =
                                    (playerView?.measuredWidth?.div(aspectRatio))?.toInt()
                                playerView?.layoutParams = layoutParams
                            } else if (ratio > aspectRatio) {
                                layoutParams?.width =
                                    (playerView?.measuredHeight?.times(aspectRatio))?.toInt()
                                playerView?.layoutParams = layoutParams
                            }
                        }
                    }

                    override fun onPlayerError(error: PlaybackException) {
                        super.onPlayerError(error)

                        Log.e(TAG, "PlaybackException $error")
                        handlePlaybackError()
                    }

                    override fun onIsPlayingChanged(isPlaying: Boolean) {
                        super.onIsPlayingChanged(isPlaying)
                        if (isPlaying) {
                            Log.i(TAG, "Subscription playback started: ${tvViewModel?.getTV()?.title}")
                            (activity as MainActivity).isPlaying()
                        }
                    }
                })
                (activity as MainActivity).fragmentReady("PlayerFragment")
            }
        })
        return _binding!!.root
    }

    fun play(tvViewModel: TVViewModel) {
        officialRetried = false
        startPlayback(tvViewModel)
    }

    private fun handlePlaybackError() {
        val model = tvViewModel ?: return
        if (model.getTV().programType == ProgramType.F && model.videoIndex.value == 0 && !officialRetried) {
            officialRetried = true
            startPlayback(model)
        } else {
            model.onPlaybackError()
        }
    }

    private fun startPlayback(model: TVViewModel) {
        resolveJob?.cancel()
        val generation = ++playGeneration
        this.tvViewModel = model
        playerView?.player?.stop()
        exoPlayer?.stop()
        val official = model.getTV().programType == ProgramType.F && model.videoIndex.value == 0
        if (!official) {
            playResolved(model.getVideoUrlCurrent(), false)
            return
        }
        resolveJob = viewLifecycleOwner.lifecycleScope.launch {
            try {
                val url = PhoenixResolver.resolve(model.getTV().pid)
                if (generation == playGeneration) playResolved(url, true)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (generation != playGeneration) return@launch
                Log.w(TAG, "Phoenix resolution failed for ${model.getTV().title}: ${e.javaClass.simpleName}")
                handlePlaybackError()
            }
        }
    }

    @OptIn(UnstableApi::class)
    private fun playResolved(url: String, official: Boolean) {
        (playerView?.player as? ExoPlayer)?.run {
            if (official) {
                val http = DefaultHttpDataSource.Factory().setUserAgent("Mozilla/5.0")
                    .setAllowCrossProtocolRedirects(true)
                    .setDefaultRequestProperties(mapOf("Referer" to "https://m.fengshows.com/"))
                val factory = DefaultMediaSourceFactory(DefaultDataSource.Factory(requireContext(), http))
                setMediaSource(factory.createMediaSource(MediaItem.fromUri(url)))
            } else {
                setMediaItem(MediaItem.fromUri(url))
            }
            prepare()
            playWhenReady = true
        }
        exoPlayer?.run {
            if (official) {
                val http = com.google.android.exoplayer2.upstream.DefaultHttpDataSourceFactory("Mozilla/5.0")
                http.setDefaultRequestProperties(mapOf("Referer" to "https://m.fengshows.com/"))
                val factory = com.google.android.exoplayer2.source.DefaultMediaSourceFactory(http)
                setMediaSource(factory.createMediaSource(com.google.android.exoplayer2.MediaItem.fromUri(url)))
            } else {
                setMediaItem(com.google.android.exoplayer2.MediaItem.fromUri(url))
            }
            prepare()
            playWhenReady = true
        }
    }

    override fun onStart() {
        Log.i(TAG, "onStart")
        super.onStart()
        val current = tvViewModel
        if (current?.getTV()?.programType == ProgramType.F && current.videoIndex.value == 0) {
            play(current)
            return
        }
        if (playerView != null && playerView!!.player?.isPlaying == false) {
            Log.i(TAG, "replay")
            playerView!!.player?.prepare()
            playerView!!.player?.play()
        }
        if (exoPlayer?.isPlaying == false) {
            Log.i(TAG, "replay")
            exoPlayer?.prepare()
            exoPlayer?.play()
        }
    }

    override fun onResume() {
        Log.i(TAG, "onResume")
        super.onResume()
    }

    override fun onPause() {
        super.onPause()
        playGeneration++
        resolveJob?.cancel()
        if (playerView != null && playerView!!.player?.isPlaying == true) {
            playerView!!.player?.stop()
        }
        if (exoPlayer?.isPlaying == true) {
            exoPlayer?.stop()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        if (playerView != null) {
            playerView!!.player?.release()
        }
        exoPlayer?.release()
    }

    override fun onDestroyView() {
        super.onDestroyView()
        playGeneration++
        resolveJob?.cancel()
        _binding = null
    }

    companion object {
        private const val TAG = "PlaybackVideoFragment"
    }

    override fun surfaceCreated(holder: SurfaceHolder) {
        exoPlayer = SimpleExoPlayer.Builder(requireContext()).build()
        exoPlayer?.setVideoSurfaceHolder(surfaceHolder)
        exoPlayer?.playWhenReady = true
        (activity as MainActivity).fragmentReady("PlayerFragment")
    }

    override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {
    }

    override fun surfaceDestroyed(holder: SurfaceHolder) {
    }
}