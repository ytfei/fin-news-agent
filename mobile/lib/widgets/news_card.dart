import 'package:flutter/material.dart';

import '../models/news.dart';
import 'band_chip.dart';

/// 资讯卡片：来源 / 时间 → 标题 → 摘要 → 分档与已分析标记。
///
/// 已分析的资讯会在底部补一行分析摘要预览，让用户在列表页就能判断值不值得点进去。
class NewsCard extends StatelessWidget {
  const NewsCard({super.key, required this.news, this.onTap});

  final NewsItem news;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final summary = news.summary;
    final analysisSummary = news.analysisSummary;

    return Card(
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(14),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              MetaLine(source: news.displaySource, time: news.publishTime),
              const SizedBox(height: 6),
              Text(
                news.title,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.titleMedium,
              ),
              if (summary != null && summary.isNotEmpty) ...[
                const SizedBox(height: 4),
                Text(
                  summary,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: theme.textTheme.bodySmall,
                ),
              ],
              const SizedBox(height: 8),
              Row(
                children: [
                  BandChip(band: news.band, score: news.score),
                  if (news.hasAnalysis) ...[
                    const SizedBox(width: 6),
                    Icon(
                      Icons.auto_awesome,
                      size: 13,
                      color: theme.colorScheme.primary,
                    ),
                    const SizedBox(width: 2),
                    Text(
                      '已分析',
                      style: TextStyle(
                        fontSize: 11,
                        color: theme.colorScheme.primary,
                      ),
                    ),
                  ],
                ],
              ),
              if (news.hasAnalysis &&
                  analysisSummary != null &&
                  analysisSummary.isNotEmpty) ...[
                const SizedBox(height: 8),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.fromLTRB(8, 6, 8, 6),
                  decoration: BoxDecoration(
                    color: theme.colorScheme.surfaceContainerHighest
                        .withValues(alpha: 0.5),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Text(
                    analysisSummary,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.bodySmall,
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
