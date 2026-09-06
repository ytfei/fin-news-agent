import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// 统一的异步三态容器：加载中 / 错误可重试 / 空数据。
///
/// 四个页面都用它包裹异步数据，避免各页各写一套 loading / error / empty，
/// 也保证空态与错误提示的视觉和文案一致。
///
/// [isEmpty] 由调用方提供，因为数据可能是列表，也可能是可空对象（如报告）。
class AsyncStateView<T> extends StatelessWidget {
  const AsyncStateView({
    super.key,
    required this.value,
    required this.dataBuilder,
    this.isEmpty,
    this.emptyText = '这里还没有内容',
    this.emptyIcon = Icons.inbox_outlined,
    this.onRetry,
    this.skeletonCount = 5,
  });

  final AsyncValue<T> value;
  final Widget Function(T data) dataBuilder;

  /// 返回 true 时展示空态
  final bool Function(T data)? isEmpty;
  final String emptyText;
  final IconData emptyIcon;
  final VoidCallback? onRetry;
  final int skeletonCount;

  @override
  Widget build(BuildContext context) {
    return value.when(
      loading: () => _LoadingList(count: skeletonCount),
      error: (error, _) => ErrorView(
        message: error.toString(),
        onRetry: onRetry,
      ),
      data: (data) {
        if (isEmpty?.call(data) ?? false) {
          return EmptyView(
            text: emptyText,
            icon: emptyIcon,
            onRetry: onRetry,
          );
        }
        return dataBuilder(data);
      },
    );
  }
}

/// 骨架屏：比转圈更能传达「正在加载列表」，也避免布局跳动。
class _LoadingList extends StatelessWidget {
  const _LoadingList({required this.count});

  final int count;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).textTheme.bodySmall?.color;
    return ListView.builder(
      padding: const EdgeInsets.symmetric(vertical: 8),
      itemCount: count,
      itemBuilder: (_, _) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 120,
              height: 12,
              decoration: BoxDecoration(
                color: muted?.withValues(alpha: 0.18),
                borderRadius: BorderRadius.circular(3),
              ),
            ),
            const SizedBox(height: 10),
            Container(
              width: double.infinity,
              height: 15,
              decoration: BoxDecoration(
                color: muted?.withValues(alpha: 0.16),
                borderRadius: BorderRadius.circular(3),
              ),
            ),
            const SizedBox(height: 6),
            Container(
              width: MediaQuery.of(context).size.width * 0.6,
              height: 15,
              decoration: BoxDecoration(
                color: muted?.withValues(alpha: 0.16),
                borderRadius: BorderRadius.circular(3),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 空数据状态。
class EmptyView extends StatelessWidget {
  const EmptyView({
    super.key,
    required this.text,
    this.icon = Icons.inbox_outlined,
    this.onRetry,
  });

  final String text;
  final IconData icon;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 44, color: theme.textTheme.bodySmall?.color),
            const SizedBox(height: 14),
            Text(
              text,
              textAlign: TextAlign.center,
              style: theme.textTheme.bodyMedium,
            ),
            if (onRetry != null) ...[
              const SizedBox(height: 16),
              OutlinedButton.icon(
                onPressed: onRetry,
                icon: const Icon(Icons.refresh, size: 18),
                label: const Text('刷新'),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// 错误状态（带重试）。
///
/// 直接展示 [ApiException] 的中文提示；其它异常则退化为通用文案，
/// 不把 Dart 堆栈之类的信息暴露给用户。
class ErrorView extends StatelessWidget {
  const ErrorView({super.key, required this.message, this.onRetry});

  final String message;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final text = message.replaceAll(RegExp(r'^Exception:?\s*'), '');

    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.error_outline,
              size: 44,
              color: theme.colorScheme.error,
            ),
            const SizedBox(height: 14),
            Text(
              text,
              textAlign: TextAlign.center,
              style: theme.textTheme.bodyMedium,
            ),
            if (onRetry != null) ...[
              const SizedBox(height: 16),
              FilledButton.icon(
                onPressed: onRetry,
                icon: const Icon(Icons.refresh, size: 18),
                label: const Text('重试'),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
