import 'dart:async';
import 'dart:convert';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:http/http.dart' as http;

import '../core/api_exception.dart';
import '../core/device_id.dart';
import '../core/sse_client.dart';
import '../models/chat.dart';
import 'api_providers.dart';

/// 会话列表。
class SessionsNotifier extends AsyncNotifier<List<ChatSession>> {
  @override
  Future<List<ChatSession>> build() => _fetch();

  Future<List<ChatSession>> _fetch() async {
    final client = await ref.watch(apiClientProvider.future);
    final data = await client.get<List<dynamic>>('/chat/sessions');
    if (data == null) return const [];
    return data.map(ChatSession.fromJson).toList();
  }

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(_fetch);
  }

  /// 新建会话，返回新会话 id。
  Future<String> create() async {
    final client = await ref.read(apiClientProvider.future);
    final json = await client.post<Map<String, dynamic>>('/chat/sessions');
    final session = ChatSession.fromJson(json);
    await refresh();
    return session.id;
  }

  Future<void> remove(String sessionId) async {
    final client = await ref.read(apiClientProvider.future);
    await client.delete('/chat/sessions/$sessionId');
    await refresh();
  }
}

final sessionsProvider =
    AsyncNotifierProvider<SessionsNotifier, List<ChatSession>>(SessionsNotifier.new);

/// 单个会话的消息与流式发送。
///
/// Riverpod 3 的 family 通过**构造函数**注入参数（2.x 的 `arg` 属性与
/// `FamilyAsyncNotifier` 基类都已移除），因此这里用 `ChatController(sessionId)`。
///
/// 后端提供两种模式（`PostMessageRequest.stream`）：
/// * `true` → SSE 流式（`delta` / `references` / `done` / `error` 四类事件）
/// * `false` → 一次性返回完整 `ChatMessageOut`
///
/// 默认走流式以获得打字机效果；[sendBlocking] 作为弱网 / 兼容的降级路径。
class ChatController extends AsyncNotifier<List<ChatMessage>> {
  ChatController(this.sessionId);

  final String sessionId;

  /// 测试时可注入 http 客户端；默认每次请求新建一个。
  http.Client? httpClient;

  StreamSubscription<SseEvent>? _sub;
  bool _streaming = false;

  bool get isStreaming => _streaming;

  @override
  Future<List<ChatMessage>> build() async {
    ref.onDispose(cancel);
    final client = await ref.watch(apiClientProvider.future);
    final data =
        await client.get<List<dynamic>>('/chat/sessions/$sessionId/messages');
    if (data == null) return const [];
    // 后端按时间正序返回，直接渲染即可
    return data.map(ChatMessage.fromJson).toList();
  }

  /// 取消进行中的流式请求（用户点「停止」或页面销毁时）。
  void cancel() {
    _sub?.cancel();
    _sub = null;
    _streaming = false;
  }

  Future<void> refresh() async {
    state = const AsyncLoading();
    state = await AsyncValue.guard(build);
  }

  /// 发送消息并以流式方式接收回复。
  Future<void> send(String content) async {
    final text = content.trim();
    if (text.isEmpty || _streaming) return;

    // 乐观渲染：先插入用户消息与一条占位回复，避免等待网络往返出现卡顿感
    final current = state.value ?? const <ChatMessage>[];
    state = AsyncData(<ChatMessage>[
      ...current,
      _localUserMessage(text),
      ChatMessage.streaming(sessionId: sessionId),
    ]);

    _streaming = true;
    final baseUrl = await ref.read(baseUrlProvider.future);
    final request = http.Request(
      'POST',
      Uri.parse('$baseUrl/chat/sessions/$sessionId/messages'),
    )
      ..headers['Content-Type'] = 'application/json'
      ..headers['Accept'] = 'text/event-stream'
      ..headers['X-Device-Id'] = await DeviceId.get()
      ..body = jsonEncode({'content': text, 'stream': true});

    try {
      final client = httpClient ?? http.Client();
      final response = await client.send(request);
      if (response.statusCode >= 400) {
        throw ApiException(
          '请求失败（${response.statusCode}）',
          status: response.statusCode,
        );
      }

      // 用 listen + Completer 而非 `await for`：后者无法被 cancel() 中断，
      // 用户点「停止」会失效。订阅句柄存到 _sub 供 cancel() 取消。
      final completer = Completer<void>();
      _sub = parseSse(response.stream).listen(
        (event) {
          switch (event.event) {
            case 'delta':
              _appendDelta(event.data['text'] as String? ?? '');
            case 'references':
              // 后端在流结束后才下发引用（不在 delta 之前），此处才渲染引用区
              _applyReferences(event.data['items']);
            case 'done':
              _finish(event.data);
            case 'error':
              _fail(event.data['detail'] as String? ??
                  '分析服务暂时不可用，请稍后重试');
            default:
              break;
          }
        },
        onError: (Object error) {
          if (!completer.isCompleted) completer.completeError(error);
        },
        onDone: () {
          if (!completer.isCompleted) completer.complete();
        },
        cancelOnError: true,
      );
      await completer.future;
      _streaming = false;
    } on ApiException catch (e) {
      _fail(e.message);
    } catch (_) {
      _fail('网络异常，请检查连接后重试');
    } finally {
      _streaming = false;
    }
  }

  /// 非流式发送（降级路径）：一次性拿到完整回复。
  Future<void> sendBlocking(String content) async {
    final text = content.trim();
    if (text.isEmpty || _streaming) return;

    final current = state.value ?? const <ChatMessage>[];
    state = AsyncData(<ChatMessage>[...current, _localUserMessage(text)]);

    try {
      final client = await ref.read(apiClientProvider.future);
      final json = await client.post<Map<String, dynamic>>(
        '/chat/sessions/$sessionId/messages',
        body: {'content': text, 'stream': false},
      );
      final reply = ChatMessage.fromJson(json);
      final list = state.value ?? const <ChatMessage>[];
      state = AsyncData(<ChatMessage>[...list, reply]);
    } on ApiException catch (e) {
      _fail(e.message);
    }
  }

  ChatMessage _localUserMessage(String text) => ChatMessage(
        id: 'local-user-${DateTime.now().microsecondsSinceEpoch}',
        sessionId: sessionId,
        role: 'user',
        content: text,
        references: const [],
        status: 'OK',
        createdAt: DateTime.now(),
      );

  void _appendDelta(String text) {
    if (text.isEmpty) return;
    final list = state.value;
    if (list == null || list.isEmpty) return;
    final last = list.last;
    state = AsyncData(<ChatMessage>[
      ...list.sublist(0, list.length - 1),
      last.copyWith(content: last.content + text),
    ]);
  }

  void _applyReferences(Object? raw) {
    final list = state.value;
    if (list == null || list.isEmpty) return;
    final items = raw is List
        ? raw
            .whereType<Map>()
            .map((e) => Map<String, dynamic>.from(e))
            .toList()
        : <Map<String, dynamic>>[];
    final last = list.last;
    state = AsyncData(<ChatMessage>[
      ...list.sublist(0, list.length - 1),
      last.copyWith(references: items),
    ]);
  }

  void _finish(Map<String, dynamic> data) {
    final list = state.value;
    if (list == null || list.isEmpty) return;
    final last = list.last;
    state = AsyncData(<ChatMessage>[
      ...list.sublist(0, list.length - 1),
      last.copyWith(
        id: data['message_id'] as String? ?? last.id,
        status: 'OK',
        model: data['model'] as String?,
        latencyMs: data['latency_ms'] is int ? data['latency_ms'] as int : null,
        createdAt: DateTime.now(),
      ),
    ]);
  }

  void _fail(String message) {
    final list = state.value;
    if (list == null || list.isEmpty) return;
    final last = list.last;
    state = AsyncData(<ChatMessage>[
      ...list.sublist(0, list.length - 1),
      last.copyWith(
        status: 'FAILED',
        content: last.content.isEmpty ? message : last.content,
      ),
    ]);
    _streaming = false;
  }
}

/// Riverpod 3 的 family 语法：构造工厂接收 ArgT 参数。
final chatControllerProvider = AsyncNotifierProvider.family<ChatController,
    List<ChatMessage>, String>((sessionId) => ChatController(sessionId));
