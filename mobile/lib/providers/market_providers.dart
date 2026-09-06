import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/api_exception.dart';
import '../models/market.dart';
import 'api_providers.dart';

/// 简报时段。
enum BriefPeriod {
  preMarket,
  postMarket;

  String get apiValue => this == BriefPeriod.preMarket ? 'pre_market' : 'post_market';

  String get label => this == BriefPeriod.preMarket ? '盘前展望' : '盘后复盘';
}

/// 简报查询条件：时段 + 交易日（null 表示最近一期）。
class BriefSelection {
  const BriefSelection({required this.period, this.date});

  final BriefPeriod period;
  final DateTime? date;

  @override
  bool operator ==(Object other) =>
      other is BriefSelection && other.period == period && other.date == date;

  @override
  int get hashCode => Object.hash(period, date);
}

/// 后端日期参数格式：`YYYY-MM-DD`（不引 intl，避免额外的本地化初始化）。
String formatApiDate(DateTime date) => [
      date.year.toString().padLeft(4, '0'),
      date.month.toString().padLeft(2, '0'),
      date.day.toString().padLeft(2, '0'),
    ].join('-');

/// 当前选中的时段（报告页 Tab）。
/// Riverpod 3 已把 StateProvider 归入 legacy，改用推荐的 [Notifier]。
class BriefPeriodNotifier extends Notifier<BriefPeriod> {
  @override
  BriefPeriod build() => BriefPeriod.preMarket;

  void select(BriefPeriod period) => state = period;
}

final briefPeriodProvider =
    NotifierProvider<BriefPeriodNotifier, BriefPeriod>(BriefPeriodNotifier.new);

/// 当前查看的交易日；null 表示由后端返回最近一期。
class BriefDateNotifier extends Notifier<DateTime?> {
  @override
  DateTime? build() => null;

  void select(DateTime? date) => state = date;
}

final briefDateProvider =
    NotifierProvider<BriefDateNotifier, DateTime?>(BriefDateNotifier.new);

/// 盘前 / 盘后简报正文。
///
/// 非交易日或该日无简报时后端返回 404，这里统一转为 null，由 UI 展示空态，
/// 不把 404 当成错误抛给用户（这是正常的业务状态而非故障）。
final briefProvider =
    FutureProvider.autoDispose.family<MarketBrief?, BriefSelection>((ref, sel) async {
  final client = await ref.watch(apiClientProvider.future);
  final path = sel.period == BriefPeriod.preMarket
      ? '/market/pre-market'
      : '/market/post-market';
  try {
    final json = await client.get<Map<String, dynamic>>(
      path,
      queryParameters: {
        if (sel.date != null) 'date': formatApiDate(sel.date!),
      },
    );
    if (json == null) return null;
    return MarketBrief.fromJson(json);
  } on ApiException catch (e) {
    if (e.status == 404) return null;
    rethrow;
  }
});

/// 历史简报归档（报告页底部的往期列表）。
final briefsArchiveProvider =
    FutureProvider.family<List<BriefMeta>, BriefPeriod>((ref, period) async {
  final client = await ref.watch(apiClientProvider.future);
  final data = await client.get<List<dynamic>>(
    '/market/briefs',
    queryParameters: {'days': 30, 'period': period.apiValue},
  );
  if (data == null) return const [];
  return data.map(BriefMeta.fromJson).toList();
});
