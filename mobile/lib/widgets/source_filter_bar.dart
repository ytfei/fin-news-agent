import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/news.dart';
import '../providers/news_providers.dart';

/// 渠道筛选栏：「全部」+ 各渠道标签（带条数）+ 排序切换。
///
/// 渠道数据来自 `/news/sources` 聚合接口；条数让用户在切换前就知道各渠道的量。
class SourceFilterBar extends ConsumerWidget {
  const SourceFilterBar({super.key, required this.sources});

  final List<NewsSource> sources;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final filter = ref.watch(newsFilterProvider);
    final theme = Theme.of(context);

    return Column(
      children: [
        SizedBox(
          height: 40,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            children: [
              _Chip(
                label: '全部',
                selected: filter.source == null,
                onTap: () =>
                    ref.read(newsFilterProvider.notifier).selectSource(null),
              ),
              ...sources.map(
                (s) => _Chip(
                  label: s.displayName,
                  count: s.count,
                  selected: filter.source == s.src,
                  onTap: () =>
                      ref.read(newsFilterProvider.notifier).selectSource(s.src),
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 4),
        // 排序：再次点击同一字段翻转升降序
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Row(
            children: [
              _SortButton(
                label: '最新',
                active: filter.sort == 'publish_time',
                order: filter.order,
                onTap: () => ref
                    .read(newsFilterProvider.notifier)
                    .toggleSort('publish_time'),
              ),
              const SizedBox(width: 8),
              _SortButton(
                label: '评分',
                active: filter.sort == 'score',
                order: filter.order,
                onTap: () =>
                    ref.read(newsFilterProvider.notifier).toggleSort('score'),
              ),
              const SizedBox(width: 8),
              _SortButton(
                label: '重要度',
                active: filter.sort == 'impact',
                order: filter.order,
                onTap: () =>
                    ref.read(newsFilterProvider.notifier).toggleSort('impact'),
              ),
              const Spacer(),
              Text(
                '${sources.length} 个渠道',
                style: theme.textTheme.bodySmall,
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _Chip extends StatelessWidget {
  const _Chip({
    required this.label,
    this.count,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final int? count;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final primary = theme.colorScheme.primary;

    return Padding(
      padding: const EdgeInsets.only(right: 8),
      child: Material(
        color: selected ? primary.withValues(alpha: 0.16) : Colors.transparent,
        borderRadius: BorderRadius.circular(18),
        child: InkWell(
          onTap: onTap,
          borderRadius: BorderRadius.circular(18),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(18),
              border: Border.all(
                color: selected
                    ? primary.withValues(alpha: 0.6)
                    : theme.textTheme.bodySmall!.color!.withValues(alpha: 0.24),
              ),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  label,
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
                    color: selected ? primary : theme.textTheme.bodyMedium?.color,
                  ),
                ),
                if (count != null && count! > 0) ...[
                  const SizedBox(width: 4),
                  Text(
                    '$count',
                    style: TextStyle(
                      fontSize: 11,
                      color: selected
                          ? primary
                          : theme.textTheme.bodySmall?.color,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _SortButton extends StatelessWidget {
  const _SortButton({
    required this.label,
    required this.active,
    required this.order,
    required this.onTap,
  });

  final String label;
  final bool active;
  final String order;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final primary = theme.colorScheme.primary;
    final color = active ? primary : theme.textTheme.bodySmall?.color;

    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(6),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              label,
              style: TextStyle(
                fontSize: 12,
                color: color,
                fontWeight: active ? FontWeight.w600 : FontWeight.w400,
              ),
            ),
            if (active) ...[
              const SizedBox(width: 2),
              Icon(
                order == 'desc'
                    ? Icons.arrow_downward_rounded
                    : Icons.arrow_upward_rounded,
                size: 12,
                color: color,
              ),
            ],
          ],
        ),
      ),
    );
  }
}
