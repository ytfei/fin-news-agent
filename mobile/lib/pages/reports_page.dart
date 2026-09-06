import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/market.dart';
import '../providers/market_providers.dart';
import '../theme/app_theme.dart';
import '../widgets/async_state_view.dart';

/// 盘前盘后报告页。
///
/// iOS 风格细节：时段切换用 `CupertinoSlidingSegmentedControl`、日期选择用
/// `CupertinoDatePicker`（底部弹出），比 Material 的对应组件更贴合平台习惯。
class ReportsPage extends ConsumerStatefulWidget {
  const ReportsPage({super.key});

  @override
  ConsumerState<ReportsPage> createState() => _ReportsPageState();
}

class _ReportsPageState extends ConsumerState<ReportsPage> {
  void _pickDate() {
    final now = DateTime.now();
    final current = ref.read(briefDateProvider) ?? now;
    showCupertinoModalPopup<void>(
      context: context,
      builder: (context) => Container(
        height: 280,
        color: CupertinoTheme.of(context).scaffoldBackgroundColor,
        child: SafeArea(
          top: false,
          child: Column(
            children: [
              SizedBox(
                height: 216,
                child: CupertinoDatePicker(
                  mode: CupertinoDatePickerMode.date,
                  initialDateTime: current,
                  maximumDate: now,
                  minimumDate: now.subtract(const Duration(days: 365)),
                  onDateTimeChanged: (value) =>
                      ref.read(briefDateProvider.notifier).select(value),
                ),
              ),
              CupertinoButton(
                child: const Text('回到最近一期'),
                onPressed: () {
                  ref.read(briefDateProvider.notifier).select(null);
                  Navigator.of(context).pop();
                },
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final period = ref.watch(briefPeriodProvider);
    final date = ref.watch(briefDateProvider);
    final briefAsync = ref.watch(
      briefProvider(BriefSelection(period: period, date: date)),
    );

    return Scaffold(
      appBar: AppBar(
        title: const Text('报告'),
        actions: [
          TextButton.icon(
            onPressed: _pickDate,
            icon: const Icon(Icons.calendar_today_outlined, size: 16),
            label: Text(
              date == null ? '最近一期' : formatApiDate(date),
              style: const TextStyle(fontSize: 13),
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
            child: SizedBox(
              width: double.infinity,
              child: CupertinoSlidingSegmentedControl<BriefPeriod>(
                groupValue: period,
                onValueChanged: (value) {
                  if (value != null) {
                    ref.read(briefPeriodProvider.notifier).select(value);
                  }
                },
                children: const {
                  BriefPeriod.preMarket: Padding(
                    padding: EdgeInsets.symmetric(vertical: 6),
                    child: Text('盘前展望'),
                  ),
                  BriefPeriod.postMarket: Padding(
                    padding: EdgeInsets.symmetric(vertical: 6),
                    child: Text('盘后复盘'),
                  ),
                },
              ),
            ),
          ),
          Expanded(
            child: RefreshIndicator(
              onRefresh: () async => ref.invalidate(
                briefProvider(BriefSelection(period: period, date: date)),
              ),
              child: AsyncStateView<MarketBrief?>(
                value: briefAsync,
                // 非交易日 / 该日无简报属于正常业务状态，不是故障
                isEmpty: (brief) => brief == null,
                emptyText: '该日期没有简报\n（可能非交易日，换个日期试试）',
                emptyIcon: Icons.event_busy_outlined,
                dataBuilder: (brief) => _BriefBody(brief: brief!, period: period),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _BriefBody extends ConsumerWidget {
  const _BriefBody({required this.brief, required this.period});

  final MarketBrief brief;
  final BriefPeriod period;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final theme = Theme.of(context);
    final archive = ref.watch(briefsArchiveProvider(period));

    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 32),
      children: [
        if (brief.isDegraded)
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
            decoration: BoxDecoration(
              color: AppColors.bandIndustry.withValues(alpha: 0.14),
              borderRadius: BorderRadius.circular(8),
              border: Border.all(
                color: AppColors.bandIndustry.withValues(alpha: 0.45),
              ),
            ),
            child: Text(
              '本简报为降级产出（AI 深度分析未成功，已回退为简化结果）',
              style: theme.textTheme.bodySmall,
            ),
          ),
        const SizedBox(height: 8),
        Text(brief.title, style: theme.textTheme.headlineSmall),
        const SizedBox(height: 8),
        Text(brief.summary, style: theme.textTheme.bodyMedium),
        // ---- 按时段渲染不同区块 ----
        if (period == BriefPeriod.preMarket) ...[
          if (brief.usMarket.isNotEmpty) _UsMarket(items: brief.usMarket),
          if (brief.focusDirections.isNotEmpty)
            _DictList(title: '关注方向', items: brief.focusDirections),
        ] else ...[
          if (brief.verdict.isNotEmpty) _Verdict(verdict: brief.verdict),
          if (brief.attribution.isNotEmpty)
            _DictList(title: '涨跌归因', items: brief.attribution),
          if (brief.nextDayFocus.isNotEmpty)
            _StringList(title: '次日关注', items: brief.nextDayFocus),
        ],
        if (brief.bullets.isNotEmpty) _StringList(title: '要点', items: brief.bullets),
        const SizedBox(height: 16),
        // ---- 历史归档 ----
        archive.when(
          loading: () => const SizedBox.shrink(),
          error: (_, _) => const SizedBox.shrink(),
          data: (items) {
            if (items.isEmpty) return const SizedBox.shrink();
            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('往期简报', style: theme.textTheme.titleMedium),
                const SizedBox(height: 6),
                ...items.take(12).map(
                      (m) => ListTile(
                        dense: true,
                        contentPadding: EdgeInsets.zero,
                        title: Text(
                          m.title,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: theme.textTheme.bodyMedium,
                        ),
                        subtitle: Text(
                          m.tradeDate == null
                              ? ''
                              : formatApiDate(m.tradeDate!),
                          style: theme.textTheme.bodySmall,
                        ),
                        onTap: () => ref
                            .read(briefDateProvider.notifier)
                            .select(m.tradeDate),
                      ),
                    ),
              ],
            );
          },
        ),
        const SizedBox(height: 12),
        Text(brief.disclaimer, style: theme.textTheme.bodySmall),
      ],
    );
  }
}

class _UsMarket extends StatelessWidget {
  const _UsMarket({required this.items});

  final List<UsQuote> items;

  @override
  Widget build(BuildContext context) {
    return _Section(
      title: '隔夜外盘',
      child: Column(
        children: items.map((q) {
          final pct = q.pctChg;
          final color = pct == null
              ? AppColors.flat
              : (pct >= 0 ? AppColors.up : AppColors.down);
          return Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    q.name ?? q.symbol,
                    style: Theme.of(context).textTheme.bodySmall,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                if (pct != null)
                  Text(
                    '${pct >= 0 ? '+' : ''}${pct.toStringAsFixed(2)}%',
                    style: TextStyle(fontSize: 12, color: color),
                  ),
              ],
            ),
          );
        }).toList(),
      ),
    );
  }
}

class _Verdict extends StatelessWidget {
  const _Verdict({required this.verdict});

  final Map<String, dynamic> verdict;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final text = (verdict['summary'] ?? verdict['text'] ?? '').toString();
    if (text.isEmpty) return const SizedBox.shrink();
    return _Section(
      title: '今日结论',
      child: Text(text, style: theme.textTheme.bodyMedium),
    );
  }
}

class _DictList extends StatelessWidget {
  const _DictList({required this.title, required this.items});

  final String title;
  final List<Map<String, dynamic>> items;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return _Section(
      title: title,
      child: Column(
        children: items.map((m) {
          final text = (m['title'] ??
                  m['direction'] ??
                  m['name'] ??
                  m['text'] ??
                  m['summary'] ??
                  '')
              .toString();
          return Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('·  '),
                Expanded(
                  child: Text(
                    text.isEmpty ? '—' : text,
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

class _StringList extends StatelessWidget {
  const _StringList({required this.title, required this.items});

  final String title;
  final List<String> items;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return _Section(
      title: title,
      child: Column(
        children: items
            .map(
              (s) => Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('·  '),
                    Expanded(child: Text(s, style: theme.textTheme.bodySmall)),
                  ],
                ),
              ),
            )
            .toList(),
      ),
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
      padding: const EdgeInsets.only(top: 16),
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
