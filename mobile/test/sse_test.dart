import 'dart:convert';

import 'package:fin_news/core/sse_client.dart';
import 'package:flutter_test/flutter_test.dart';

/// 把字符串按 UTF-8 切成指定大小的字节块，模拟网络分片。
Stream<List<int>> chunked(String text, int size) async* {
  final bytes = utf8.encode(text);
  for (var i = 0; i < bytes.length; i += size) {
    yield bytes.sublist(i, (i + size).clamp(0, bytes.length));
  }
}

void main() {
  test('解析 delta / references / done / error 四类事件', () async {
    const raw = 'event: delta\ndata: {"text":"你"}\n\n'
        'event: delta\ndata: {"text":"好"}\n\n'
        'event: references\ndata: {"items":[{"title":"来源A"}]}\n\n'
        'event: done\ndata: {"message_id":"m1","model":"x","latency_ms":12}\n\n';

    final events = await parseSse(chunked(raw, 8)).toList();

    expect(events.map((e) => e.event), ['delta', 'delta', 'references', 'done']);
    expect(events[0].data['text'], '你');
    expect(events[2].data['items'], isA<List>());
    expect(events[3].data['message_id'], 'm1');
  });

  test('中文不会被分片截断成乱码', () async {
    // "市" 是 3 字节汉字，故意按 1 字节切分——最容易触发乱码的场景
    const raw = 'event: delta\ndata: {"text":"市场"}\n\n';
    final events = await parseSse(chunked(raw, 1)).toList();

    expect(events.length, 1);
    expect(events.single.data['text'], '市场');
  });

  test('流末尾未以空行结尾时仍能解析出最后一个事件', () async {
    const raw = 'event: done\ndata: {"message_id":"m9"}'; // 无结尾空行
    final events = await parseSse(chunked(raw, 4)).toList();
    expect(events.single.data['message_id'], 'm9');
  });

  test('兼容 CRLF 分隔', () async {
    const raw = 'event: delta\r\ndata: {"text":"a"}\r\n\r\n';
    final events = await parseSse(chunked(raw, 5)).toList();
    expect(events.single.data['text'], 'a');
  });

  test('忽略注释行与非 JSON 负载，不中断流', () async {
    const raw = ': 心跳\n\n'
        'event: delta\ndata: not-json\n\n'
        'event: done\ndata: {"message_id":"ok"}\n\n';
    final events = await parseSse(chunked(raw, 6)).toList();
    expect(events.single.event, 'done');
  });
}
