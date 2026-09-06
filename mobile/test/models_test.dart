import 'package:fin_news/models/chat.dart';
import 'package:fin_news/models/news.dart';
import 'package:fin_news/models/page.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('NewsItem', () {
    test('字段按 snake_case 正确映射', () {
      final news = NewsItem.fromJson({
        'id': 'uuid-1',
        'title': '央行下调存款准备金率',
        'summary': '摘要内容',
        'source': 'tushare',
        'src': 'cls',
        'src_name': '财联社',
        'publish_time': '2026-09-05T10:30:00+08:00',
        'score': 6,
        'band': 'INDUSTRY',
        'tags': ['货币', '银行'],
        'entities': [
          {'type': 'stock', 'code': '600519', 'name': '贵州茅台', 'confidence': 0.8},
        ],
        'has_analysis': true,
        'analysis_id': 'report-1',
        'seen_count': 2,
      });

      expect(news.id, 'uuid-1');
      expect(news.title, '央行下调存款准备金率');
      expect(news.src, 'cls');
      expect(news.displaySource, '财联社'); // 优先渠道中文名
      expect(news.score, 6);
      expect(news.band, 'INDUSTRY');
      expect(news.tags, ['货币', '银行']);
      expect(news.entities.single.name, '贵州茅台');
      expect(news.entities.single.confidence, 0.8);
      expect(news.hasAnalysis, isTrue);
      expect(news.analysisId, 'report-1');
      expect(news.publishTime, isNotNull);
    });

    test('后端显式输出 null 时不会抛异常', () {
      // 后端 Pydantic 未开 exclude_none，空值会以 null 出现
      final news = NewsItem.fromJson({
        'id': 'uuid-2',
        'title': '标题',
        'summary': null,
        'source': 'tushare',
        'src': null,
        'src_name': null,
        'score': null,
        'band': null,
        'tags': null,
        'entities': null,
        'has_analysis': false,
        'analysis_id': null,
      });

      expect(news.summary, isNull);
      expect(news.score, isNull);
      expect(news.band, isNull);
      expect(news.tags, isEmpty);
      expect(news.entities, isEmpty);
      // 渠道名全缺时退回 source
      expect(news.displaySource, 'tushare');
    });

    test('缺字段时不崩溃（脏数据兜底）', () {
      final news = NewsItem.fromJson({'id': 'only-id'});
      expect(news.id, 'only-id');
      expect(news.title, '');
      expect(news.seenCount, 1); // 后端默认值
      expect(news.hasAnalysis, isFalse);
    });
  });

  group('Page', () {
    test('扁平结构解析，翻页依据 has_more', () {
      final page = Page.fromJson(
        {
          'page': 2,
          'page_size': 20,
          'total': 35,
          'has_more': true,
          'items': [
            {'id': 'a', 'title': 'A', 'source': 'tushare'},
            {'id': 'b', 'title': 'B', 'source': 'tushare'},
          ],
        },
        NewsItem.fromJson,
      );

      expect(page.page, 2);
      expect(page.total, 35);
      expect(page.hasMore, isTrue); // 最后一页可能正好满页，必须用它判断
      expect(page.items.map((e) => e.id), ['a', 'b']);
    });

    test('items 缺失或类型异常时回落为空列表', () {
      final page = Page.fromJson({'page': 1}, NewsItem.fromJson);
      expect(page.items, isEmpty);
      expect(page.hasMore, isFalse);
    });

    test('empty() 提供安全初始态', () {
      final page = Page.empty<NewsItem>();
      expect(page.items, isEmpty);
      expect(page.isEmpty, isTrue);
    });
  });

  group('ChatMessage', () {
    test('解析助手消息与引用', () {
      final msg = ChatMessage.fromJson({
        'id': 'm1',
        'session_id': 's1',
        'role': 'assistant',
        'content': '这是回答',
        'references': [
          {'title': '资讯A'},
        ],
        'status': 'OK',
        'model': 'doubao',
        'latency_ms': 1200,
        'disclaimer': 'AI 生成，仅供参考，不构成投资建议。',
      });

      expect(msg.isUser, isFalse);
      expect(msg.content, '这是回答');
      expect(msg.references.single['title'], '资讯A');
      expect(msg.latencyMs, 1200);
      expect(msg.disclaimer, isNotNull);
    });

    test('streaming() 构造的占位消息可被识别', () {
      final msg = ChatMessage.streaming(sessionId: 's1');
      expect(msg.status, 'STREAMING');
      expect(msg.role, 'assistant');
    });

    test('copyWith 只覆盖传入字段', () {
      final msg = ChatMessage.streaming(sessionId: 's1');
      final next = msg.copyWith(content: '部分', status: 'OK');
      expect(next.content, '部分');
      expect(next.status, 'OK');
      expect(next.role, 'assistant'); // 未传，保持
    });
  });
}
