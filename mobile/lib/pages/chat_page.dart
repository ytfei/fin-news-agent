import 'package:flutter/cupertino.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/chat.dart';
import '../providers/chat_providers.dart';
import '../widgets/async_state_view.dart';

/// 追问页：会话列表入口。
class ChatPage extends ConsumerWidget {
  const ChatPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final sessions = ref.watch(sessionsProvider);

    return Scaffold(
      appBar: AppBar(
        title: const Text('追问'),
        actions: [
          IconButton(
            icon: const Icon(Icons.add_comment_outlined),
            tooltip: '新建会话',
            onPressed: () async {
              final id = await ref.read(sessionsProvider.notifier).create();
              if (!context.mounted) return;
              await Navigator.of(context).push(
                CupertinoPageRoute<void>(
                  builder: (_) => ChatSessionPage(sessionId: id),
                ),
              );
              // 返回后刷新列表（消息数 / 最后时间会变）
              ref.invalidate(sessionsProvider);
            },
          ),
        ],
      ),
      body: AsyncStateView<List<ChatSession>>(
        value: sessions,
        isEmpty: (list) => list.isEmpty,
        emptyText: '还没有会话\n点右上角新建，向 AI 追问市场逻辑',
        emptyIcon: Icons.chat_bubble_outline,
        onRetry: () => ref.read(sessionsProvider.notifier).refresh(),
        dataBuilder: (list) => RefreshIndicator(
          onRefresh: () => ref.read(sessionsProvider.notifier).refresh(),
          child: ListView.separated(
            padding: const EdgeInsets.symmetric(vertical: 8),
            itemCount: list.length,
            separatorBuilder: (_, _) => const Divider(height: 1),
            itemBuilder: (context, index) {
              final s = list[index];
              return Dismissible(
                key: ValueKey(s.id),
                direction: DismissDirection.endToStart,
                background: Container(
                  alignment: Alignment.centerRight,
                  padding: const EdgeInsets.only(right: 20),
                  color: Theme.of(context).colorScheme.error.withValues(alpha: 0.8),
                  child: const Icon(Icons.delete_outline, color: Colors.white),
                ),
                confirmDismiss: (_) async => await showCupertinoDialog<bool>(
                      context: context,
                      builder: (context) => CupertinoAlertDialog(
                        title: const Text('删除会话'),
                        content: const Text('删除后该会话的消息将无法恢复，确定继续？'),
                        actions: [
                          CupertinoDialogAction(
                            child: const Text('取消'),
                            onPressed: () => Navigator.of(context).pop(false),
                          ),
                          CupertinoDialogAction(
                            isDestructiveAction: true,
                            child: const Text('删除'),
                            onPressed: () => Navigator.of(context).pop(true),
                          ),
                        ],
                      ),
                    ) ??
                    false,
                onDismissed: (_) =>
                    ref.read(sessionsProvider.notifier).remove(s.id),
                child: ListTile(
                  title: Text(
                    s.displayTitle,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  subtitle: Text(
                    '${s.messageCount} 条消息${s.lastMessageAt == null ? '' : ' · ${_fmt(s.lastMessageAt!)}'}',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                  trailing: const Icon(Icons.chevron_right, size: 18),
                  onTap: () async {
                    await Navigator.of(context).push(
                      CupertinoPageRoute<void>(
                        builder: (_) => ChatSessionPage(sessionId: s.id),
                      ),
                    );
                    ref.invalidate(sessionsProvider);
                  },
                ),
              );
            },
          ),
        ),
      ),
    );
  }
}

String _fmt(DateTime time) =>
    '${time.month.toString().padLeft(2, '0')}-${time.day.toString().padLeft(2, '0')} '
    '${time.hour.toString().padLeft(2, '0')}:${time.minute.toString().padLeft(2, '0')}';

/// 单个会话的聊天界面。
class ChatSessionPage extends ConsumerStatefulWidget {
  const ChatSessionPage({super.key, required this.sessionId});

  final String sessionId;

  @override
  ConsumerState<ChatSessionPage> createState() => _ChatSessionPageState();
}

class _ChatSessionPageState extends ConsumerState<ChatSessionPage> {
  final TextEditingController _input = TextEditingController();
  final ScrollController _scroll = ScrollController();

  @override
  void dispose() {
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  /// 新消息到达后滚到底部。
  void _scrollToBottom() {
    if (!_scroll.hasClients) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scroll.hasClients) return;
      _scroll.animateTo(
        _scroll.position.maxScrollExtent,
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
      );
    });
  }

  Future<void> _send() async {
    final text = _input.text.trim();
    if (text.isEmpty) return;
    _input.clear();
    FocusScope.of(context).unfocus();
    await ref
        .read(chatControllerProvider(widget.sessionId).notifier)
        .send(text);
    _scrollToBottom();
  }

  @override
  Widget build(BuildContext context) {
    final async = ref.watch(chatControllerProvider(widget.sessionId));
    final controller = ref.read(chatControllerProvider(widget.sessionId).notifier);
    final streaming = controller.isStreaming;

    // 流式输出时持续滚到底部
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (streaming && _scroll.hasClients) {
        _scroll.jumpTo(_scroll.position.maxScrollExtent);
      }
    });

    return Scaffold(
      appBar: AppBar(title: const Text('追问')),
      body: SafeArea(
        child: Column(
          children: [
            Expanded(
              child: AsyncStateView<List<ChatMessage>>(
                value: async,
                emptyText: '开始提问吧',
                emptyIcon: Icons.chat_bubble_outline,
                skeletonCount: 3,
                dataBuilder: (messages) => ListView.builder(
                  controller: _scroll,
                  padding: const EdgeInsets.fromLTRB(12, 12, 12, 8),
                  itemCount: messages.length,
                  itemBuilder: (context, index) =>
                      _Bubble(message: messages[index]),
                ),
              ),
            ),
            _Composer(
              controller: _input,
              streaming: streaming,
              onSend: _send,
              onStop: () => ref
                  .read(chatControllerProvider(widget.sessionId).notifier)
                  .cancel(),
            ),
          ],
        ),
      ),
    );
  }
}

/// 消息气泡。
class _Bubble extends StatelessWidget {
  const _Bubble({required this.message});

  final ChatMessage message;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final isUser = message.isUser;
    final streaming = message.status == 'STREAMING';

    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * 0.78,
        ),
        decoration: BoxDecoration(
          color: isUser
              ? theme.colorScheme.primary.withValues(alpha: 0.18)
              : theme.colorScheme.surfaceContainerHighest.withValues(alpha: 0.7),
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(14),
            topRight: const Radius.circular(14),
            bottomLeft: Radius.circular(isUser ? 14 : 4),
            bottomRight: Radius.circular(isUser ? 4 : 14),
          ),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 流式输出时在末尾追加闪烁光标，形成打字机效果
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Flexible(
                  child: Text(
                    message.content.isEmpty && streaming
                        ? '思考中…'
                        : message.content,
                    style: theme.textTheme.bodyMedium?.copyWith(
                      color: message.isFailed ? theme.colorScheme.error : null,
                    ),
                  ),
                ),
                if (streaming) const _Cursor(),
              ],
            ),
            // 引用来源：后端在流结束后才下发，因此这里渲染的是最终态
            if (message.references.isNotEmpty) ...[
              const SizedBox(height: 8),
              const Divider(height: 1),
              const SizedBox(height: 6),
              ...message.references.take(3).map(
                    (ref) => Padding(
                      padding: const EdgeInsets.only(bottom: 2),
                      child: Text(
                        '· ${(ref['title'] ?? ref['news_title'] ?? '').toString()}',
                        style: theme.textTheme.bodySmall,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ),
            ],
            if (message.disclaimer != null) ...[
              const SizedBox(height: 6),
              Text(
                message.disclaimer!,
                style: theme.textTheme.bodySmall,
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// 打字机光标（闪烁）。
class _Cursor extends StatefulWidget {
  const _Cursor();

  @override
  State<_Cursor> createState() => _CursorState();
}

class _CursorState extends State<_Cursor>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 600),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: Tween<double>(begin: 0.2, end: 1.0).animate(_controller),
      child: const Text('▌', style: TextStyle(fontSize: 13)),
    );
  }
}

/// 输入区：生成中显示「停止」，否则显示发送。
class _Composer extends StatelessWidget {
  const _Composer({
    required this.controller,
    required this.streaming,
    required this.onSend,
    required this.onStop,
  });

  final TextEditingController controller;
  final bool streaming;
  final VoidCallback onSend;
  final VoidCallback onStop;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
      decoration: BoxDecoration(
        color: theme.scaffoldBackgroundColor,
        border: Border(
          top: BorderSide(
            color: theme.textTheme.bodySmall!.color!.withValues(alpha: 0.18),
          ),
        ),
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Expanded(
                child: TextField(
                  controller: controller,
                  minLines: 1,
                  maxLines: 5,
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => onSend(),
                  decoration: const InputDecoration(
                    hintText: '问点什么…',
                    border: OutlineInputBorder(),
                    isDense: true,
                    contentPadding: EdgeInsets.symmetric(
                      horizontal: 12,
                      vertical: 10,
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              streaming
                  ? IconButton.filledTonal(
                      onPressed: onStop,
                      icon: const Icon(Icons.stop_rounded),
                      tooltip: '停止生成',
                    )
                  : IconButton.filled(
                      onPressed: onSend,
                      icon: const Icon(Icons.arrow_upward_rounded),
                      tooltip: '发送',
                    ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            'AI 生成，仅供参考，不构成投资建议。',
            style: theme.textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}
