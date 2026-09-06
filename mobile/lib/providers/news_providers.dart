import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/news.dart';
import '../models/page.dart';
import 'api_providers.dart';

/// 资讯流筛选条件。
class NewsFilter {
  const NewsFilter({
    this.source,
    this.sort = 'publish_time',
    this.order = 'desc',
  });

  /// 渠道标识（cls / wallstreetcn / yicai）；null 表示全部渠道
  final String? source;

  /// publish_time / score / impact
  final String sort;

  /// asc / desc
  final String order;

  NewsFilter copyWith({String? source, String? sort, String? order}) => NewsFilter(
        source: source,
        sort: sort ?? this.sort,
        order: order ?? this.order,
      );
}

/// 当前筛选条件。改变它会自动触发列表重新拉取。
///
/// Riverpod 3 已把 `StateProvider` 归入 legacy，这里改用推荐的 [Notifier]。
class NewsFilterNotifier extends Notifier<NewsFilter> {
  @override
  NewsFilter build() => const NewsFilter();

  /// 切换渠道；传 null 表示「全部渠道」。
  void selectSource(String? source) => state = state.copyWith(source: source);

  /// 切换排序字段。再次点击同一字段时翻转升降序（移动端常见交互）。
  void toggleSort(String sort) {
    if (state.sort == sort) {
      state = state.copyWith(order: state.order == 'desc' ? 'asc' : 'desc');
    } else {
      state = state.copyWith(sort: sort, order: 'desc');
    }
  }
}

final newsFilterProvider =
    NotifierProvider<NewsFilterNotifier, NewsFilter>(NewsFilterNotifier.new);

/// 渠道聚合（资讯流顶部的标签与其条数）。
final newsSourcesProvider = FutureProvider<List<NewsSource>>((ref) async {
  final client = await ref.watch(apiClientProvider.future);
  final data = await client.get<List<dynamic>>('/news/sources');
  if (data == null) return const [];
  return data.map(NewsSource.fromJson).toList();
});

/// 资讯流分页状态。
class NewsListState {
  const NewsListState({
    required this.items,
    required this.page,
    required this.hasMore,
    this.isLoadingMore = false,
  });

  final List<NewsItem> items;
  final int page;

  /// 是否还有下一页 —— 翻页必须用这个字段判断，
  /// 不能用 `items.length == pageSize` 推断（最后一页可能正好满页）
  final bool hasMore;
  final bool isLoadingMore;

  NewsListState copyWith({
    List<NewsItem>? items,
    int? page,
    bool? hasMore,
    bool? isLoadingMore,
  }) =>
      NewsListState(
        items: items ?? this.items,
        page: page ?? this.page,
        hasMore: hasMore ?? this.hasMore,
        isLoadingMore: isLoadingMore ?? this.isLoadingMore,
      );
}

/// 资讯流列表。
///
/// 用 AsyncNotifier 而非 FutureProvider：需要支持「下拉刷新」与「上拉加载更多」，
/// 且加载更多时要保留已加载的数据（不能整页闪回 loading）。
class NewsListNotifier extends AsyncNotifier<NewsListState> {
  static const int _pageSize = 20;

  @override
  Future<NewsListState> build() => _load(page: 1);

  Future<NewsListState> _load({required int page}) async {
    final filter = ref.watch(newsFilterProvider);
    final client = await ref.watch(apiClientProvider.future);
    final json = await client.get<Map<String, dynamic>>(
      '/news',
      queryParameters: {
        'page': page,
        'page_size': _pageSize,
        if (filter.source != null) 'source': filter.source,
        'sort': filter.sort,
        'order': filter.order,
      },
    );
    final data = Page.fromJson(json ?? const {}, NewsItem.fromJson);
    return NewsListState(
      items: data.items,
      page: data.page,
      hasMore: data.hasMore,
    );
  }

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(() => _load(page: 1));
  }

  Future<void> loadMore() async {
    final current = state.value;
    // 没有更多 / 正在加载 / 首屏未就绪时都跳过，避免重复请求
    if (current == null || !current.hasMore || current.isLoadingMore) return;

    state = AsyncData(current.copyWith(isLoadingMore: true));
    final next = await AsyncValue.guard(() => _load(page: current.page + 1));

    next.when(
      data: (page) {
        final base = state.value;
        if (base == null) return;
        state = AsyncData(
          NewsListState(
            items: <NewsItem>[...base.items, ...page.items],
            page: page.page,
            hasMore: page.hasMore,
          ),
        );
      },
      error: (error, _) {
        // 加载更多失败时保留已加载的数据，只清掉 loading 标记
        final base = state.value;
        if (base != null) {
          state = AsyncData(base.copyWith(isLoadingMore: false));
        }
      },
      loading: () {},
    );
  }
}

final newsListProvider =
    AsyncNotifierProvider<NewsListNotifier, NewsListState>(NewsListNotifier.new);

/// 资讯详情。
final newsDetailProvider =
    FutureProvider.family<NewsDetail, String>((ref, newsId) async {
  final client = await ref.watch(apiClientProvider.future);
  final json = await client.get<Map<String, dynamic>>('/news/$newsId');
  return NewsDetail.fromJson(json);
});
