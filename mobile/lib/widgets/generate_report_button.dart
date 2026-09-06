import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../providers/api_providers.dart';

/// 「生成报告」按钮：手动触发深度分析（POST /news/{id}/analyze）。
///
/// 后端返回 202 只代表「已入队 / 已插队」，实际分析需 1~6 分钟，因此成功后
/// 提示「已加入队列」并进入 disabled 态，不等待结果。
class GenerateReportButton extends ConsumerStatefulWidget {
  const GenerateReportButton({super.key, required this.newsId});

  final String newsId;

  @override
  ConsumerState<GenerateReportButton> createState() =>
      _GenerateReportButtonState();
}

enum _Phase { idle, loading, queued, error }

class _GenerateReportButtonState extends ConsumerState<GenerateReportButton> {
  _Phase _phase = _Phase.idle;

  Future<void> _trigger() async {
    if (_phase == _Phase.loading || _phase == _Phase.queued) return;
    setState(() => _phase = _Phase.loading);
    try {
      final client = await ref.read(apiClientProvider.future);
      await client.post('/news/${widget.newsId}/analyze');
      if (!mounted) return;
      setState(() => _phase = _Phase.queued);
      _toast('已加入队列，正在生成，稍后下拉刷新即可查看');
    } catch (e) {
      if (!mounted) return;
      setState(() => _phase = _Phase.error);
      _toast('生成请求失败，请稍后重试');
    }
  }

  void _toast(String message) {
    ScaffoldMessenger.of(context)
      ..clearSnackBars()
      ..showSnackBar(SnackBar(content: Text(message)));
  }

  @override
  Widget build(BuildContext context) {
    final label = switch (_phase) {
      _Phase.loading => '提交中…',
      _Phase.queued => '已加入队列',
      _Phase.error => '重试',
      _Phase.idle => '生成报告',
    };
    final enabled = _phase != _Phase.loading && _phase != _Phase.queued;

    return OutlinedButton.icon(
      onPressed: enabled ? _trigger : null,
      icon: const Icon(Icons.auto_awesome, size: 16),
      label: Text(label),
      style: OutlinedButton.styleFrom(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        textStyle: const TextStyle(fontSize: 12, fontWeight: FontWeight.w500),
        visualDensity: VisualDensity.compact,
      ),
    );
  }
}
