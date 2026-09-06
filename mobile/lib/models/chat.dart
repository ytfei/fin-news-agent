import '../core/json_utils.dart';

/// 追问会话（对应 `ChatSessionOut`）。
class ChatSession {
  const ChatSession({
    required this.id,
    this.title,
    required this.contextFilter,
    required this.messageCount,
    this.createdAt,
    this.lastMessageAt,
  });

  final String id;
  final String? title;
  final Map<String, dynamic> contextFilter;
  final int messageCount;
  final DateTime? createdAt;
  final DateTime? lastMessageAt;

  /// 列表展示名：未命名时用「新对话」占位（后端 title 可为空）。
  String get displayTitle {
    final text = title?.trim();
    return (text == null || text.isEmpty) ? '新对话' : text;
  }

  factory ChatSession.fromJson(Object? raw) {
    final json = raw is Map ? Map<String, dynamic>.from(raw) : const {};
    return ChatSession(
      id: asString(json['id']),
      title: asStringOrNull(json['title']),
      contextFilter: json['context_filter'] is Map
          ? Map<String, dynamic>.from(json['context_filter'] as Map)
          : const {},
      messageCount: asInt(json['message_count']),
      createdAt: asDateTime(json['created_at']),
      lastMessageAt: asDateTime(json['last_message_at']),
    );
  }
}

/// 聊天消息（对应 `ChatMessageOut`）。
class ChatMessage {
  const ChatMessage({
    required this.id,
    required this.sessionId,
    required this.role,
    required this.content,
    required this.references,
    required this.status,
    this.model,
    this.latencyMs,
    this.createdAt,
    this.disclaimer,
  });

  final String id;
  final String sessionId;

  /// user / assistant
  final String role;
  final String content;

  /// 引用来源；后端是 `list[dict]`，结构随检索结果而定
  final List<Map<String, dynamic>> references;

  /// OK / FAILED
  final String status;
  final String? model;
  final int? latencyMs;
  final DateTime? createdAt;
  final String? disclaimer;

  bool get isUser => role == 'user';
  bool get isFailed => status != 'OK';

  /// 流式输出期间构造的「临时消息」：id 为空，用于本地先渲染再等服务端确认。
  factory ChatMessage.streaming({required String sessionId, String content = ''}) =>
      ChatMessage(
        id: 'streaming',
        sessionId: sessionId,
        role: 'assistant',
        content: content,
        references: const [],
        status: 'STREAMING',
      );

  factory ChatMessage.fromJson(Object? raw) {
    final json = raw is Map ? Map<String, dynamic>.from(raw) : const {};
    return ChatMessage(
      id: asString(json['id']),
      sessionId: asString(json['session_id']),
      role: asString(json['role']),
      content: asString(json['content']),
      references: asMapList(json['references']),
      status: asString(json['status'], 'OK'),
      model: asStringOrNull(json['model']),
      latencyMs: asIntOrNull(json['latency_ms']),
      createdAt: asDateTime(json['created_at']),
      disclaimer: asStringOrNull(json['disclaimer']),
    );
  }

  ChatMessage copyWith({
    String? id,
    String? content,
    List<Map<String, dynamic>>? references,
    String? status,
    String? model,
    int? latencyMs,
    DateTime? createdAt,
    String? disclaimer,
  }) =>
      ChatMessage(
        id: id ?? this.id,
        sessionId: sessionId,
        role: role,
        content: content ?? this.content,
        references: references ?? this.references,
        status: status ?? this.status,
        model: model ?? this.model,
        latencyMs: latencyMs ?? this.latencyMs,
        createdAt: createdAt ?? this.createdAt,
        disclaimer: disclaimer ?? this.disclaimer,
      );
}
