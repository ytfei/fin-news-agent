import '../core/json_utils.dart';

/// 后端统一分页容器（对应 `schemas.py` 的 `Page[T]`）。
///
/// 两点容易踩坑，已在实现中规避：
/// 1. **结构是扁平的**。`schemas.py` 里虽然还定义了 `PageMeta`，但它是死代码
///    （没有任何 router 使用），实际响应是 `{page, page_size, total, has_more, items}`，
///    不要按嵌套 `meta` 去解析。
/// 2. **翻页终止必须用 `has_more`**，不能用 `items.length == pageSize` 推断——
///    最后一页可能正好满页，按长度判断会导致提前终止或无限请求。
class Page<T> {
  const Page({
    required this.page,
    required this.pageSize,
    required this.total,
    required this.hasMore,
    required this.items,
  });

  final int page;
  final int pageSize;
  final int total;
  final bool hasMore;
  final List<T> items;

  factory Page.fromJson(
    Map<String, dynamic> json,
    T Function(Object? raw) fromJsonT,
  ) {
    final raw = json['items'];
    return Page<T>(
      page: asInt(json['page'], 1),
      pageSize: asInt(json['page_size'], 20),
      total: asInt(json['total'], 0),
      hasMore: asBool(json['has_more'], false),
      items: raw is List ? raw.map(fromJsonT).toList() : const [],
    );
  }

  /// 空页，用于初始状态或出错时兜底，避免 UI 层判空。
  static Page<T> empty<T>() =>
      Page<T>(page: 1, pageSize: 20, total: 0, hasMore: false, items: const []);

  bool get isEmpty => items.isEmpty;
}
