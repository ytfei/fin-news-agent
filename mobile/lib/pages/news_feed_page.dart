import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/news.dart';
import '../providers/news_providers.dart';
import '../widgets/async_state_view.dart';
import '../widgets/news_card.dart';
import '../widgets/source_filter_bar.dart';
import 'analysis_detail_page.dart';
import 'settings_page.dart';

/// 资讯流页（底部导航第一项）。
///
/// 交互：渠道筛选 + 排序切换 + 下拉刷新 + 上拉加载更多。
/// 点击卡片：已分析的资讯进入分析详情；未分析的给出轻提示
/// （首版不做独立的资讯详情页，避免范围蔓延）。
class NewsFeedPage extends ConsumerStatefulWidget {
  const NewsFeedPage({super.key});

  @override
  ConsumerState<NewsFeedPage> createState() => _NewsFeedPageState();
}

class _NewsFeedPageState extends ConsumerState<NewsFeedPage> {
  final ScrollController _scroll = ScrollController();

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
  }

  @override
  void dispose() {
    _scroll.removeListener(_onScroll);
    _scroll.dispose();
    super.dispose();
  }

  /// 距底部 300px 时提前加载下一页，避免用户滑到底才转圈。
  void _onScroll() {
    if (!_scroll.hasClients) return;
    if (_scroll.position.extentAfter < 300) {
      ref.read(newsListProvider.notifier).loadMore();
    }
  }

  Future<void> _refresh() async {
    await ref.read(newsListProvider.notifier).refresh();
    // 渠道条数会随新资讯变化，一并刷新
    ref.invalidate(newsSourcesProvider);
  }

  void _open(NewsItem news) {
    final reportId = news.analysisId;
    if (reportId == null || reportId.isEmpty) {
      ScaffoldMessenger.of(context)
        ..clearSnackBars()
        ..showSnackBar(const SnackBar(
          content: Text('暂无深度分析，可点击卡片下方的「生成报告」按钮手动生成'),
        ));
      return;
    }
    Navigator.of(context).push(
      CupertinoPageRoute<void>(
        builder: (_) => AnalysisDetailPage(reportId: reportId),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final sourcesAsync = ref.watch(newsSourcesProvider);
    final listAsync = ref.watch(newsListProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('资讯'),
        actions: [
          IconButton(
            icon: const Icon(Icons.settings_outlined),
            tooltip: '后端地址设置',
            onPressed: () => Navigator.of(context).push(
              CupertinoPageRoute<void>(builder: (_) => const SettingsPage()),
            ),
          ),
        ],
      ),
      // 筛选栏固定在上部，列表占满剩余空间并独立滚动
      body: Column(
        children: [
          sourcesAsync.when(
            // 加载中保留等高占位，避免筛选栏出现时列表跳动
            loading: () => const SizedBox(height: 72),
            error: (_, _) => const SizedBox.shrink(),
            data: (sources) => SourceFilterBar(sources: sources),
          ),
          Expanded(
            child: RefreshIndicator(
              onRefresh: _refresh,
              child: AsyncStateView<NewsListState>(
                value: listAsync,
                isEmpty: (state) => state.items.isEmpty,
                emptyText: '暂无资讯，下拉可刷新',
                emptyIcon: Icons.article_outlined,
                onRetry: _refresh,
                dataBuilder: (state) => ListView.builder(
                  controller: _scroll,
                  padding: const EdgeInsets.fromLTRB(12, 4, 12, 24),
                  itemCount: state.items.length + (state.hasMore ? 1 : 0),
                  itemBuilder: (context, index) {
                    if (index >= state.items.length) {
                      return const Padding(
                        padding: EdgeInsets.symmetric(vertical: 16),
                        child: Center(child: CupertinoActivityIndicator()),
                      );
                    }
                    final news = state.items[index];
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: NewsCard(news: news, onTap: () => _open(news)),
                    );
                  },
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
