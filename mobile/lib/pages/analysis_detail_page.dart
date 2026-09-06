import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/analysis.dart';
import '../providers/analysis_providers.dart';
import '../theme/app_theme.dart';
import '../widgets/async_state_view.dart';
import '../widgets/band_chip.dart';

/// 分析报告详情页。
///
/// `content` 是后端 `dict[str, Any]`，内部结构随 Agent 而异，这里只取约定俗成的
/// `headline` / `bullets` / `summary` 等键，缺键时静默跳过，不因后端加字段而崩。
class AnalysisDetailPage extends ConsumerWidget {
  const AnalysisDetailPage({super.key, required this.reportId});

  final String reportId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final async = ref.watch(analysisDetailProvider(reportId));

    return Scaffold(
      appBar: AppBar(title: const Text('分析详情')),
      body: AsyncStateView<AnalysisDetail>(
        value: async,
        emptyText: '未找到该报告',
        emptyIcon: Icons.description_outlined,
        onRetry: () => ref.invalidate(analysisDetailProvider(reportId)),
        dataBuilder: (report) => SafeArea(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
            children: [
              if (report.isDegraded) const _DegradedBanner(),
              const SizedBox(height: 8),
              if (report.headline.isNotEmpty) ...[
                Text(report.headline, style: Theme.of(context).textTheme.headlineSmall),
                const SizedBox(height: 8),
              ],
              Wrap(
                spacing: 8,
                runSpacing: 6,
                children: [
                  BandChip(band: report.band, score: report.score),
                  if (report.impactLevel != null) _Tag(text: '影响 ${report.impactLevel}'),
                  if (report.horizon != null) _Tag(text: '周期 ${report.horizon}'),
                  if (report.sentiment != null) _Tag(text: report.sentiment!),
                  if (report.confidence != null)
                    _Tag(text: '置信度 ${(report.confidence! * 100).round()}%'),
                ],
              ),
              const SizedBox(height: 14),
              if (report.summary.isNotEmpty) ...[
                Text(report.summary, style: Theme.of(context).textTheme.bodyMedium),
                const SizedBox(height: 14),
              ],
              if (report.bullets.isNotEmpty) _Section(
                title: '要点',
                child: Column(
                  children: report.bullets
                      .map((b) => Padding(
                            padding: const EdgeInsets.only(bottom: 6),
                            child: Row(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                const Text('·  '),
                                Expanded(child: Text(b)),
                              ],
                            ),
                          ))
                      .toList(),
                ),
              ),
              if (report.beneficiaries.isNotEmpty)
                _Targets(title: '受益方向', items: report.beneficiaries, positive: true),
              if (report.victims.isNotEmpty)
                _Targets(title: '受损方向', items: report.victims, positive: false),
              if (report.references.isNotEmpty)
                _References(items: report.references),
              _Meta(report: report),
              const SizedBox(height: 12),
              Text(
                report.disclaimer,
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _DegradedBanner extends StatelessWidget {
  const _DegradedBanner();

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: AppColors.bandIndustry.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: AppColors.bandIndustry.withValues(alpha: 0.45)),
      ),
      child: Row(
        children: [
          const Icon(Icons.warning_amber_rounded, size: 16),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              '本报告为降级产出（AI 深度分析未成功，已回退为简化结果）',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
        ],
      ),
    );
  }
}

class _Tag extends StatelessWidget {
  const _Tag({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHighest.withValues(alpha: 0.6),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(text, style: theme.textTheme.bodySmall),
    );
  }
}

class _Section extends StatelessWidget {
  const _Section({required this.title, required this.child});

  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: theme.textTheme.titleMedium),
          const SizedBox(height: 8),
          child,
        ],
      ),
    );
  }
}

/// 受益 / 受损标的。A 股习惯：红涨绿跌，且配合 +/- 符号（不单靠颜色区分）。
class _Targets extends StatelessWidget {
  const _Targets({
    required this.title,
    required this.items,
    required this.positive,
  });

  final String title;
  final List<ImpactTarget> items;
  final bool positive;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final color = positive ? AppColors.up : AppColors.down;

    return _Section(
      title: title,
      child: Wrap(
        spacing: 8,
        runSpacing: 8,
        children: items.map((t) {
          final name = t.name ?? t.code ?? '—';
          return Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
            decoration: BoxDecoration(
              color: color.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(6),
              border: Border.all(color: color.withValues(alpha: 0.4)),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  positive ? '+' : '-',
                  style: TextStyle(
                    color: color,
                    fontWeight: FontWeight.w600,
                    fontSize: 13,
                  ),
                ),
                const SizedBox(width: 3),
                Text(name, style: theme.textTheme.bodySmall),
              ],
            ),
          );
        }).toList(),
      ),
    );
  }
}

/// 参考来源。
class _References extends StatelessWidget {
  const _References({required this.items});

  final List<Map<String, dynamic>> items;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return _Section(
      title: '参考来源',
      child: Column(
        children: items.map((ref) {
          final title = (ref['title'] ?? ref['news_title'] ?? '').toString();
          final source = (ref['src_name'] ?? ref['source'] ?? '').toString();
          final text = source.isEmpty ? title : '$source · $title';
          return Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('·  '),
                Expanded(
                  child: Text(
                    text.isEmpty ? '未命名来源' : text,
                    style: theme.textTheme.bodySmall,
                  ),
                ),
              ],
            ),
          );
        }).toList(),
      ),
    );
  }
}

class _Meta extends StatelessWidget {
  const _Meta({required this.report});

  final AnalysisDetail report;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final rows = <String, String>{
      'Agent': report.agentType,
      '状态': report.status,
      if (report.model != null) '模型': report.model!,
      if (report.promptVersion != null) 'Prompt 版本': report.promptVersion!,
      if (report.publishedAt != null) '发布时间': report.publishedAt.toString().substring(0, 16),
    };

    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Column(
        children: rows.entries
            .map(
              (e) => Padding(
                padding: const EdgeInsets.only(bottom: 3),
                child: Row(
                  children: [
                    SizedBox(
                      width: 88,
                      child: Text(e.key, style: theme.textTheme.bodySmall),
                    ),
                    Expanded(
                      child: Text(
                        e.value,
                        style: theme.textTheme.bodySmall,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
              ),
            )
            .toList(),
      ),
    );
  }
}
