package com.lizongying.mytv

import android.os.Bundle
import android.util.Log
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.leanback.app.BrowseSupportFragment
import androidx.leanback.widget.ArrayObjectAdapter
import androidx.leanback.widget.HeaderItem
import androidx.leanback.widget.ListRow
import androidx.leanback.widget.ListRowPresenter
import androidx.leanback.widget.ListRowPresenter.SelectItemViewHolderTask
import androidx.leanback.widget.OnItemViewClickedListener
import androidx.leanback.widget.OnItemViewSelectedListener
import androidx.leanback.widget.Presenter
import androidx.leanback.widget.Row
import androidx.leanback.widget.RowPresenter
import androidx.lifecycle.lifecycleScope
import com.lizongying.mytv.models.TVListViewModel
import com.lizongying.mytv.models.TVViewModel
import kotlinx.coroutines.launch
import kotlinx.coroutines.CancellationException

class MainFragment : BrowseSupportFragment() {

    private var itemPosition = 0

    private var rowsAdapter: ArrayObjectAdapter? = null

    var tvListViewModel = TVListViewModel()

    private var lastVideoUrl = ""

    override fun onCreate(savedInstanceState: Bundle?) {
        Log.i(TAG, "onCreate")
        super.onCreate(savedInstanceState)
        headersState = HEADERS_DISABLED
    }

//    override fun onCreateView(
//        inflater: LayoutInflater,
//        container: ViewGroup?,
//        savedInstanceState: Bundle?
//    ): View? {
//        val rootView = super.onCreateView(inflater, container, savedInstanceState)
//        rootView?.setOnClickListener {
//            Log.i(TAG, "main on click")
//            fragmentManager!!.beginTransaction().hide(this).commit()
//        }
//        mainFragment.view?.setOnClickListener {
//            Log.i(TAG, "mainFragment on click")
//            fragmentManager!!.beginTransaction().hide(this).commit()
//        }
//        getRowsSupportFragment().view?.setOnClickListener {
//            Log.i(TAG, "getRowsSupportFragment on click")
//            fragmentManager!!.beginTransaction().hide(this).commit()
//        }
//
//
//        return rootView
//    }

    override fun onStart() {
        Log.i(TAG, "onStart")
        super.onStart()
    }

    override fun onActivityCreated(savedInstanceState: Bundle?) {
        super.onActivityCreated(savedInstanceState)

        setupEventListeners()
        viewLifecycleOwner.lifecycleScope.launch {
            try {
                val result = PlaylistRepository.load(requireContext().applicationContext)
                loadRows(result.channels)
                observeChannels()
                if (result.cached) {
                    Toast.makeText(context, "订阅暂时不可用，使用已缓存频道", Toast.LENGTH_LONG).show()
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.e(TAG, "Unable to load subscription", e)
                Toast.makeText(context, e.message, Toast.LENGTH_LONG).show()
            }
            (activity as? MainActivity)?.fragmentReady("MainFragment")
        }
    }

    private fun observeChannels() {
        tvListViewModel.tvListViewModel.value?.forEach { model ->
            model.errInfo.observe(viewLifecycleOwner) { message ->
                if (model.getTV().id == itemPosition && !message.isNullOrBlank()) {
                    Toast.makeText(context, message, Toast.LENGTH_LONG).show()
                }
            }
            model.change.observe(viewLifecycleOwner) {
                if (model.getTV().id == itemPosition && check(model)) {
                    Log.i(TAG, "Playing subscription channel ${model.getTV().title}, source ${model.videoIndex.value}")
                    (activity as? MainActivity)?.play(model)
                    (activity as? MainActivity)?.showInfoFragment(model)
                    setSelectedPosition(model.getRowPosition(), true,
                        SelectItemViewHolderTask(model.getItemPosition()))
                }
            }
        }
    }

    fun toLastPosition() {
        setSelectedPosition(
            selectedPosition, false,
            SelectItemViewHolderTask(tvListViewModel.maxNum[selectedPosition] - 1)
        )
    }

    fun toFirstPosition() {
        setSelectedPosition(
            selectedPosition, false,
            SelectItemViewHolderTask(0)
        )
    }

    override fun startHeadersTransition(withHeaders: Boolean) {
    }

    private fun loadRows(channels: Map<String, List<TV>>) {
        rowsAdapter = ArrayObjectAdapter(ListRowPresenter())

        val cardPresenter = CardPresenter(context!!)

        var idx: Long = 0
        for ((k, v) in channels) {
            val listRowAdapter = ArrayObjectAdapter(cardPresenter)
            for ((idx2, v1) in v.withIndex()) {
                val tvViewModel = TVViewModel(v1)
                tvViewModel.setRowPosition(idx.toInt())
                tvViewModel.setItemPosition(idx2)
                tvListViewModel.addTVViewModel(tvViewModel)
                listRowAdapter.add(tvViewModel)
            }
            tvListViewModel.maxNum.add(v.size)
            val header = HeaderItem(idx, k)
            rowsAdapter!!.add(ListRow(header, listRowAdapter))
            idx++
        }

        adapter = rowsAdapter

        itemPosition = SP.itemPosition
        if (itemPosition !in 0 until tvListViewModel.size()) {
            itemPosition = 0
        }
        tvListViewModel.setItemPosition(itemPosition)
    }

    fun prevSource() {
        view?.post {
            val tvViewModel = tvListViewModel.getTVViewModel(itemPosition)
            if (tvViewModel != null) {
                if (tvViewModel.videoUrl.value!!.size > 1) {
                    val videoIndex = tvViewModel.videoIndex.value?.minus(1)
                    tvViewModel.setVideoIndex((videoIndex!! + tvViewModel.videoUrl.value!!.size) % tvViewModel.videoUrl.value!!.size)
                    tvViewModel.retryTimes = 0
                    tvViewModel.changed()
                }
            }
        }
    }

    fun nextSource() {
        view?.post {
            val tvViewModel = tvListViewModel.getTVViewModel(itemPosition)
            if (tvViewModel != null) {
                if (tvViewModel.videoUrl.value!!.size > 1) {
                    val videoIndex = tvViewModel.videoIndex.value?.plus(1)
                    tvViewModel.setVideoIndex(videoIndex!! % tvViewModel.videoUrl.value!!.size)
                    tvViewModel.retryTimes = 0
                    tvViewModel.changed()
                }
            }
        }
    }

    private fun setupEventListeners() {
        onItemViewClickedListener = ItemViewClickedListener()
        onItemViewSelectedListener = ItemViewSelectedListener()
    }

    private inner class ItemViewClickedListener : OnItemViewClickedListener {
        override fun onItemClicked(
            itemViewHolder: Presenter.ViewHolder,
            item: Any,
            rowViewHolder: RowPresenter.ViewHolder,
            row: Row
        ) {
            if (item is TVViewModel) {
                if (itemPosition != item.getTV().id) {
                    itemPosition = item.getTV().id
                    tvListViewModel.setItemPosition(itemPosition)
                    tvListViewModel.getTVViewModel(itemPosition)?.apply { retryTimes = 0; changed() }
                }
                (activity as? MainActivity)?.switchMainFragment()
            }
        }
    }

    private inner class ItemViewSelectedListener : OnItemViewSelectedListener {
        override fun onItemSelected(
            itemViewHolder: Presenter.ViewHolder?, item: Any?,
            rowViewHolder: RowPresenter.ViewHolder, row: Row
        ) {
            if (item is TVViewModel) {
                tvListViewModel.setItemPositionCurrent(item.getTV().id)
                (activity as MainActivity).mainActive()
            }
        }
    }

    fun check(tvViewModel: TVViewModel): Boolean {
        val title = tvViewModel.getTV().title
        val videoUrl = tvViewModel.videoIndex.value?.let { tvViewModel.videoUrl.value?.get(it) }
        if (videoUrl == null || videoUrl == "") {
            Log.e(TAG, "$title videoUrl is empty")
            return false
        }

        if (videoUrl == lastVideoUrl) {
            Log.e(TAG, "$title videoUrl is duplication")
            return false
        }

        return true
    }

    fun fragmentReady() {
        tvListViewModel.getTVViewModel(itemPosition)?.apply { retryTimes = 0; changed() }

    }

    fun play(itemPosition: Int) {
        view?.post {
            if (itemPosition > -1 && itemPosition < tvListViewModel.size()) {
                this.itemPosition = itemPosition
                tvListViewModel.setItemPosition(itemPosition)
                tvListViewModel.getTVViewModel(itemPosition)?.apply { retryTimes = 0; changed() }
            } else {
                Toast.makeText(context, "频道不存在", Toast.LENGTH_SHORT).show()
            }
        }
    }

    fun prev() {
        view?.post {
            if (tvListViewModel.size() == 0) return@post
            itemPosition--
            if (itemPosition == -1) {
                itemPosition = tvListViewModel.size() - 1
            }
            tvListViewModel.setItemPosition(itemPosition)
            tvListViewModel.getTVViewModel(itemPosition)?.apply { retryTimes = 0; changed() }
        }
    }

    fun next() {
        view?.post {
            if (tvListViewModel.size() == 0) return@post
            itemPosition++
            if (itemPosition == tvListViewModel.size()) {
                itemPosition = 0
            }
            tvListViewModel.setItemPosition(itemPosition)
            tvListViewModel.getTVViewModel(itemPosition)?.apply { retryTimes = 0; changed() }
        }
    }

    override fun onResume() {
        Log.i(TAG, "onResume")
        super.onResume()
    }

    override fun onStop() {
        Log.i(TAG, "onStop")
        super.onStop()
        SP.itemPosition = itemPosition
        Log.i(TAG, "$POSITION $itemPosition saved")
    }

    override fun onDestroy() {
        Log.i(TAG, "onDestroy")
        super.onDestroy()
    }

    companion object {
        private const val TAG = "MainFragment"
        private const val POSITION = "position"
    }
}