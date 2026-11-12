package com.lizongying.mytv

import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.analytics.AnalyticsListener
import androidx.media3.decoder.ffmpeg.FfmpegLibrary
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
    private val healthHandler = Handler(Looper.getMainLooper())
    private val playbackHealth = object : Runnable {
        override fun run() {
            (playerView?.player as? ExoPlayer)?.let { player ->
                val counters = player.videoDecoderCounters
                counters?.ensureUpdated()
                Log.i(TAG, "Playback health: channel=${tvViewModel?.getTV()?.title}, source=${tvViewModel?.videoIndex?.value}, state=${player.playbackState}, playing=${player.isPlaying}, position=${player.currentPosition}, buffered=${player.bufferedPosition}, frames=${counters?.renderedOutputBufferCount}, dropped=${counters?.droppedBufferCount}")
            }
            healthHandler.postDelayed(this, 10_000)
        }
    }

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
                    val renderers = DefaultRenderersFactory(it)
                        .setExtensionRendererMode(DefaultRenderersFactory.EXTENSION_RENDERER_MODE_PREFER)
                        .setEnableDecoderFallback(true)
                    Log.i(TAG, "FFmpeg extension available: ${FfmpegLibrary.isAvailable()}")
                    ExoPlayer.Builder(it)
                        .setRenderersFactory(renderers)
                        .build().also { player ->
                            player.addAnalyticsListener(object : AnalyticsListener {
                                override fun onAudioDecoderInitialized(eventTime: AnalyticsListener.EventTime, decoderName: String, initializedTimestampMs: Long, initializationDurationMs: Long) {
                                    Log.i(TAG, "Audio decoder: $decoderName")
                                }
                                override fun onVideoDecoderInitialized(eventTime: AnalyticsListener.EventTime, decoderName: String, initializedTimestampMs: Long, initializationDurationMs: Long) {
                                    Log.i(TAG, "Video decoder: $decoderName")
                                }
                            })
                        }
                }
                playerView!!.player?.playWhenReady = true
                playerView!!.player?.addListener(object : Player.Listener {
                    override fun onVideoSizeChanged(videoSize: VideoSize) {
                        val view = playerView ?: return
                        val params = view.layoutParams ?: return
                        if (view.measuredHeight == 0) return
                        val ratio = view.measuredWidth.toFloat() / view.measuredHeight
                        if (ratio < aspectRatio) {
                            params.height = (view.measuredWidth / aspectRatio).toInt()
                        } else if (ratio > aspectRatio) {
                            params.width = (view.measuredHeight * aspectRatio).toInt()
                        }
                        view.layoutParams = params
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
            playResolved(model.getVideoUrlCurrent())
            return
        }
        resolveJob = viewLifecycleOwner.lifecycleScope.launch {
            try {
                val url = PhoenixResolver.resolve(model.getTV().pid)
                if (generation == playGeneration) playResolved(url)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (generation != playGeneration) return@launch
                Log.w(TAG, "Phoenix resolution failed for ${model.getTV().title}: ${e.javaClass.simpleName}")
                handlePlaybackError()
            }
        }
    }

    private fun playResolved(url: String) {
        playerView?.player?.run {
            setMediaItem(MediaItem.fromUri(url))
            prepare()
            playWhenReady = true
        }
        exoPlayer?.run {
            setMediaItem(com.google.android.exoplayer2.MediaItem.fromUri(url))
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
        healthHandler.removeCallbacks(playbackHealth)
        healthHandler.post(playbackHealth)
    }

    override fun onPause() {
        super.onPause()
        healthHandler.removeCallbacks(playbackHealth)
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
        healthHandler.removeCallbacks(playbackHealth)
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