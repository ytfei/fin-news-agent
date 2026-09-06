import 'package:flutter/material.dart';

import '../theme/app_theme.dart';

/// 评分分档色标（NOISE / STOCK / INDUSTRY / MACRO）。
///
/// 全 App 统一的语义色：颜色只用于传达分档这一种有语义的信息，
/// 因此样式保持克制（低透明度底色 + 同色描边），不抢正文注意力。
class BandChip extends StatelessWidget {
  const BandChip({super.key, required this.band, this.score, this.compact = false});

  final String? band;
  final int? score;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final color = AppColors.bandColor(band);
    final label = AppColors.bandLabel(band);
    final text = score == null || compact ? label : '$label $score';

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: color.withValues(alpha: 0.45)),
      ),
      child: Text(
        text,
        style: TextStyle(
          fontSize: 11,
          height: 1.2,
          color: color,
          fontWeight: FontWeight.w500,
        ),
      ),
    );
  }
}

/// 来源 + 时间的次要信息行。
class MetaLine extends StatelessWidget {
  const MetaLine({super.key, required this.source, this.time, this.trailing});

  final String source;
  final DateTime? time;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final timeText = time == null
        ? null
        : '${time!.month.toString().padLeft(2, '0')}-${time!.day.toString().padLeft(2, '0')} '
            '${time!.hour.toString().padLeft(2, '0')}:${time!.minute.toString().padLeft(2, '0')}';

    return Row(
      children: [
        Flexible(
          child: Text(
            source,
            style: theme.textTheme.bodySmall,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ),
        if (timeText != null) ...[
          const SizedBox(width: 6),
          Text('·', style: theme.textTheme.bodySmall),
          const SizedBox(width: 6),
          Text(timeText, style: theme.textTheme.bodySmall),
        ],
        if (trailing != null) ...[
          const SizedBox(width: 8),
          trailing!,
        ],
        const Spacer(),
      ],
    );
  }
}
