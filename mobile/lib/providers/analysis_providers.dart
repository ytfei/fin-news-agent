import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/analysis.dart';
import '../models/page.dart';
import 'api_providers.dart';

/// 深度分析筛选条件。
class AnalysisFilter {
  const AnalysisFilter({
    this.band,
    this.sort = 'published_at',
    this.order = 'desc',
  });

  /// 分档：NOISE / STOCK / INDUSTRY / MACRO；null 表示全部分档
  final String? band;
  final String sort; // published_at / score / impact
  final String order;

  AnalysisFilter copyWith({String? band, String? sort, String? order}) =>
      AnalysisFilter(
        band: band,
        sort: sort ?? this.sort,
        order: order ?? this.order,
      );
}

/// Riverpod 3 已把 `StateProvider` 归入 legacy，这里改用推荐的 [Notifier]。
class AnalysisFilterNotifier extends Notifier<AnalysisFilter> {
  @override
  AnalysisFilter build() => const AnalysisFilter();

  /// 切换分档；传 null 表示「全部分档」。
  void selectBand(String? band) => state = state.copyWith(band: band);

  void toggleSort(String sort) {
    if (state.sort == sort) {
      state = state.copyWith(order: state.order == 'desc' ? 'asc' : 'desc');
    } else {
      state = state.copyWith(sort: sort, order: 'desc');
    }
  }
}

final analysisFilterProvider =
    NotifierProvider<AnalysisFilterNotifier, AnalysisFilter>(
        AnalysisFilterNotifier.new);

/// 深度分析分页状态。
class AnalysisListState {
  const AnalysisListState({
    required this.items,
    required this.page,
    required this.hasMore,
    this.isLoadingMore = false,
  });

  final List<DeepAnalysis> items;
  final int page;
  final bool hasMore;
  final bool isLoadingMore;

  AnalysisListState copyWith({
    List<DeepAnalysis>? items,
    int? page,
    bool? hasMore,
    bool? isLoadingMore,
  }) =>
      AnalysisListState(
        items: items ?? this.items,
        page: page ?? this.page,
        hasMore: hasMore ?? this.hasMore,
        isLoadingMore: isLoadingMore ?? this.isLoadingMore,
      );
}

/// 深度分析列表。
///
/// 注意：后端 `min_score` 默认是 4（`DEEP_ANALYSIS_MIN_SCORE`），这里**不主动传**，
/// 保持与 Web 端一致的默认行为——只展示「评分 4 分以上且已完成分析」的内容。
class AnalysisListNotifier extends AsyncNotifier<AnalysisListState> {
  static const int _pageSize = 20;

  @override
  Future<AnalysisListState> build() => _load(page: 1);

  Future<AnalysisListState> _load({required int page}) async {
    final filter = ref.watch(analysisFilterProvider);
    final client = await ref.watch(apiClientProvider.future);
    final json = await client.get<Map<String, dynamic>>(
      '/analysis/deep',
      queryParameters: {
        'page': page,
        'page_size': _pageSize,
        if (filter.band != null) 'band': filter.band,
        'sort': filter.sort,
        'order': filter.order,
      },
    );
    final data = Page.fromJson(json ?? const {}, DeepAnalysis.fromJson);
    return AnalysisListState(
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
    if (current == null || !current.hasMore || current.isLoadingMore) return;

    state = AsyncData(current.copyWith(isLoadingMore: true));
    final next = await AsyncValue.guard(() => _load(page: current.page + 1));

    next.when(
      data: (page) {
        final base = state.value;
        if (base == null) return;
        state = AsyncData(
          AnalysisListState(
            items: <DeepAnalysis>[...base.items, ...page.items],
            page: page.page,
            hasMore: page.hasMore,
          ),
        );
      },
      error: (_, _) {
        final base = state.value;
        if (base != null) {
          state = AsyncData(base.copyWith(isLoadingMore: false));
        }
      },
      loading: () {},
    );
  }
}

final analysisListProvider =
    AsyncNotifierProvider<AnalysisListNotifier, AnalysisListState>(
        AnalysisListNotifier.new);

/// 分析报告详情。
final analysisDetailProvider =
    FutureProvider.family<AnalysisDetail, String>((ref, reportId) async {
  final client = await ref.watch(apiClientProvider.future);
  final json = await client.get<Map<String, dynamic>>('/analysis/$reportId');
  return AnalysisDetail.fromJson(json);
});
