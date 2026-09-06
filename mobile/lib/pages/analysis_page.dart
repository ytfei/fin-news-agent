import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/analysis.dart';
import '../providers/analysis_providers.dart';
import '../widgets/async_state_view.dart';
import '../widgets/band_chip.dart';
import 'analysis_detail_page.dart';

/// 深度分析列表页。
///
/// 展示「评分 4 分以上且 AI 已完成分析」的内容（后端 `min_score` 默认为 4，
/// 这里沿用其默认值，与 Web 端行为一致）。
class AnalysisPage extends ConsumerStatefulWidget {
  const AnalysisPage({super.key});

  @override
  ConsumerState<AnalysisPage> createState() => _AnalysisPageState();
}

class _AnalysisPageState extends ConsumerState<AnalysisPage> {
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

  void _onScroll() {
    if (!_scroll.hasClients) return;
    if (_scroll.position.extentAfter < 300) {
      ref.read(analysisListProvider.notifier).loadMore();
    }
  }

  Future<void> _refresh() =>
      ref.read(analysisListProvider.notifier).refresh();

  @override
  Widget build(BuildContext context) {
    final filter = ref.watch(analysisFilterProvider);
    final listAsync = ref.watch(analysisListProvider);

    return Scaffold(
      appBar: AppBar(title: const Text('深度分析')),
      body: Column(
        children: [
          // 分档筛选：噪声档后端默认不返回，这里也不提供该选项
          SizedBox(
            height: 40,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              children: [
                _BandChip2(
                  label: '全部',
                  selected: filter.band == null,
                  onTap: () => ref
                      .read(analysisFilterProvider.notifier)
                      .selectBand(null),
                ),
                for (final band in const ['STOCK', 'INDUSTRY', 'MACRO'])
                  _BandChip2(
                    label: _labelOf(band),
                    selected: filter.band == band,
                    onTap: () => ref
                        .read(analysisFilterProvider.notifier)
                        .selectBand(band),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 4),
          Expanded(
            child: RefreshIndicator(
              onRefresh: _refresh,
              child: AsyncStateView<AnalysisListState>(
                value: listAsync,
                isEmpty: (state) => state.items.isEmpty,
                emptyText: '暂无深度分析',
                emptyIcon: Icons.analytics_outlined,
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
                    final item = state.items[index];
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 10),
                      child: _AnalysisCard(
                        item: item,
                        onTap: () => Navigator.of(context).push(
                          CupertinoPageRoute<void>(
                            builder: (_) =>
                                AnalysisDetailPage(reportId: item.id),
                          ),
                        ),
                      ),
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

String _labelOf(String band) {
  switch (band) {
    case 'STOCK':
      return '个股';
    case 'INDUSTRY':
      return '行业';
    case 'MACRO':
      return '宏观';
    default:
      return band;
  }
}

class _BandChip2 extends StatelessWidget {
  const _BandChip2({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  final String label;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final primary = theme.colorScheme.primary;
    return Padding(
      padding: const EdgeInsets.only(right: 8),
      child: FilterChip(
        label: Text(label),
        selected: selected,
        onSelected: (_) => onTap(),
        showCheckmark: false,
        visualDensity: VisualDensity.compact,
        backgroundColor: Colors.transparent,
        selectedColor: primary.withValues(alpha: 0.16),
        side: BorderSide(
          color: selected
              ? primary.withValues(alpha: 0.6)
              : theme.textTheme.bodySmall!.color!.withValues(alpha: 0.24),
        ),
      ),
    );
  }
}

/// 分析卡片：标题 + 原资讯来源时间 + 分档 + 要点预览。
class _AnalysisCard extends StatelessWidget {
  const _AnalysisCard({required this.item, required this.onTap});

  final DeepAnalysis item;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final bullets = item.bullets.take(2).toList();

    return Card(
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(14),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Text(
                      item.newsSource ?? '深度分析',
                      style: theme.textTheme.bodySmall,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  BandChip(band: item.band, score: item.score, compact: true),
                ],
              ),
              const SizedBox(height: 6),
              Text(
                item.title,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.titleMedium,
              ),
              if (item.summary.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(
                  item.summary,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodySmall,
                ),
              ],
              if (bullets.isNotEmpty) ...[
                const SizedBox(height: 8),
                ...bullets.map(
                  (b) => Padding(
                    padding: const EdgeInsets.only(bottom: 3),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('· ', style: theme.textTheme.bodySmall),
                        Expanded(
                          child: Text(
                            b,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: theme.textTheme.bodySmall,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
