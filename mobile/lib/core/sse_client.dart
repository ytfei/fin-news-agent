import 'dart:async';
import 'dart:convert';

/// 一个 SSE 事件。
class SseEvent {
  const SseEvent({required this.event, required this.data});

  /// 事件名：delta / references / done / error
  final String event;
  final Map<String, dynamic> data;

  @override
  String toString() => 'SseEvent($event)';
}

const int _lf = 0x0A; // \n
const int _cr = 0x0D; // \r

/// 找到事件块的结束位置（`\n\n` 或 `\r\n\r\n`），未找到返回 -1。
int _findBlockEnd(List<int> buffer) {
  for (var i = 0; i + 1 < buffer.length; i++) {
    if (buffer[i] == _lf && buffer[i + 1] == _lf) return i;
  }
  for (var i = 0; i + 3 < buffer.length; i++) {
    if (buffer[i] == _cr &&
        buffer[i + 1] == _lf &&
        buffer[i + 2] == _cr &&
        buffer[i + 3] == _lf) {
      return i;
    }
  }
  return -1;
}

/// 解析单个事件块（已完整接收的字节）。
SseEvent? _parseBlock(List<int> bytes) {
  // allowMalformed：极端情况下宁可丢一个字符，也不要让整条流中断
  final text = utf8.decode(bytes, allowMalformed: true);

  var event = 'message';
  final dataLines = <String>[];

  for (final rawLine in text.split('\n')) {
    final line = rawLine.trim();
    if (line.isEmpty || line.startsWith(':')) continue; // 空行 / 注释行
    if (line.startsWith('event:')) {
      event = line.substring(6).trim();
    } else if (line.startsWith('data:')) {
      dataLines.add(line.substring(5).trim());
    }
  }
  if (dataLines.isEmpty) return null;

  final raw = dataLines.join('\n');
  Object? decoded;
  try {
    decoded = jsonDecode(raw);
  } catch (_) {
    return null; // 非 JSON 负载直接忽略
  }
  if (decoded is! Map) return null;

  return SseEvent(event: event, data: Map<String, dynamic>.from(decoded));
}

/// 把 SSE 字节流解析为事件流。
///
/// **必须按字节累积再解码**：网络分片（chunk）完全可能从某个多字节汉字的中间
/// 切开，若对每个 chunk 单独 `utf8.decode` 就会出现乱码（经典的中文流式输出问题）。
/// 这里先把字节攒到完整的事件块（以空行分隔），再一次性解码。
Stream<SseEvent> parseSse(Stream<List<int>> byteStream) async* {
  final buffer = <int>[];

  await for (final chunk in byteStream) {
    buffer.addAll(chunk);

    // 可能一次收到多个事件块，循环处理
    while (true) {
      final end = _findBlockEnd(buffer);
      if (end < 0) break;

      final block = buffer.sublist(0, end);
      // 事件块之间的分隔符长度为 2（\n\n）或 4（\r\n\r\n）
      final sepLen = (end + 1 < buffer.length && buffer[end + 1] == _lf) ? 2 : 4;
      buffer.removeRange(0, end + sepLen);

      final event = _parseBlock(block);
      if (event != null) yield event;
    }
  }

  // 流结束时处理残留（后端未以空行结尾的情况）
  if (buffer.isNotEmpty) {
    final event = _parseBlock(buffer);
    if (event != null) yield event;
  }
}
