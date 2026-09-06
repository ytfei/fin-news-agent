import '../core/json_utils.dart';

/// 关联实体（个股 / 板块 / 指数 / 宏观）。
class NewsEntity {
  const NewsEntity({
    required this.type,
    this.code,
    this.name,
    this.confidence,
  });

  final String type; // stock / sector / index / macro
  final String? code;
  final String? name;
  final double? confidence;

  factory NewsEntity.fromJson(Object? raw) {
    final json = raw is Map ? Map<String, dynamic>.from(raw) : const {};
    return NewsEntity(
      type: asString(json['type'], 'macro'),
      code: asStringOrNull(json['code']),
      name: asStringOrNull(json['name']),
      confidence: asDouble(json['confidence']),
    );
  }
}

/// 渠道聚合项：资讯流顶部的渠道标签及其条数。
class NewsSource {
  const NewsSource({this.src, this.srcName, required this.count});

  /// 渠道标识（cls / wallstreetcn / yicai）
  final String? src;

  /// 渠道中文名（财联社 / 华尔街见闻 / 第一财经）
  final String? srcName;
  final int count;

  factory NewsSource.fromJson(Object? raw) {
    final json = raw is Map ? Map<String, dynamic>.from(raw) : const {};
    return NewsSource(
      src: asStringOrNull(json['src']),
      srcName: asStringOrNull(json['src_name']),
      count: asInt(json['count']),
    );
  }

  /// 展示名：优先中文名，缺失时退回标识，再退回「未知渠道」。
  String get displayName => srcName ?? src ?? '未知渠道';
}

/// 相关资讯（详情页底部的相似资讯）。
class RelatedNews {
  const RelatedNews({
    required this.id,
    required this.title,
    this.publishTime,
    this.score,
    required this.similarity,
  });

  final String id;
  final String title;
  final DateTime? publishTime;
  final int? score;
  final double similarity;

  factory RelatedNews.fromJson(Object? raw) {
    final json = raw is Map ? Map<String, dynamic>.from(raw) : const {};
    return RelatedNews(
      id: asString(json['id']),
      title: asString(json['title']),
      publishTime: asDateTime(json['publish_time']),
      score: asIntOrNull(json['score']),
      similarity: asDouble(json['similarity']) ?? 0.0,
    );
  }
}

/// 评分历史（详情页展示评分是怎么变化的）。
class ScoreHistory {
  const ScoreHistory({
    required this.score,
    this.band,
    this.reason,
    this.model,
    this.promptVersion,
    this.createdAt,
  });

  final int score;
  final String? band;
  final String? reason;
  final String? model;
  final String? promptVersion;
  final DateTime? createdAt;

  factory ScoreHistory.fromJson(Object? raw) {
    final json = raw is Map ? Map<String, dynamic>.from(raw) : const {};
    return ScoreHistory(
      score: asInt(json['score']),
      band: asStringOrNull(json['band']),
      reason: asStringOrNull(json['reason']),
      model: asStringOrNull(json['model']),
      promptVersion: asStringOrNull(json['prompt_version']),
      createdAt: asDateTime(json['created_at']),
    );
  }
}

/// 资讯列表项（对应 `NewsItemOut`）。
class NewsItem {
  const NewsItem({
    required this.id,
    required this.title,
    this.summary,
    required this.source,
    this.src,
    this.srcName,
    this.kind,
    this.channels,
    this.publishTime,
    this.ingestedAt,
    this.score,
    this.band,
    this.scoreReason,
    required this.tags,
    required this.entities,
    required this.hasAnalysis,
    this.analysisSummary,
    this.analysisId,
    required this.seenCount,
    this.status,
    this.isExpired = false,
  });

  final String id;
  final String title;
  final String? summary;

  /// 数据源名，当前恒为 `tushare`（真正区分渠道的是 [src]）
  final String source;

  /// 渠道标识，用于传给 /news 的 source 过滤参数
  final String? src;
  final String? srcName;
  final String? kind;
  final String? channels;
  final DateTime? publishTime;
  final DateTime? ingestedAt;

  /// 1-10，未评分为 null
  final int? score;

  /// NOISE / STOCK / INDUSTRY / MACRO
  final String? band;
  final String? scoreReason;
  final List<String> tags;
  final List<NewsEntity> entities;

  final bool hasAnalysis;
  final String? analysisSummary;

  /// 关联报告的 id，跳转到分析详情时用
  final String? analysisId;
  final int seenCount;

  /// 资讯处理状态（NEW / SCORED / EMBEDDED / ANALYZED / EXPIRED ...）
  final String? status;

  /// 便捷布尔：status == EXPIRED（已过时效窗口、不再自动分析）
  final bool isExpired;

  factory NewsItem.fromJson(Object? raw) {
    final json = raw is Map ? Map<String, dynamic>.from(raw) : const {};
    final rawEntities = json['entities'];
    return NewsItem(
      id: asString(json['id']),
      title: asString(json['title']),
      summary: asStringOrNull(json['summary']),
      source: asString(json['source']),
      src: asStringOrNull(json['src']),
      srcName: asStringOrNull(json['src_name']),
      kind: asStringOrNull(json['kind']),
      channels: asStringOrNull(json['channels']),
      publishTime: asDateTime(json['publish_time']),
      ingestedAt: asDateTime(json['ingested_at']),
      score: asIntOrNull(json['score']),
      band: asStringOrNull(json['band']),
      scoreReason: asStringOrNull(json['score_reason']),
      tags: asStringList(json['tags']),
      entities: rawEntities is List
          ? rawEntities.map(NewsEntity.fromJson).toList()
          : const [],
      hasAnalysis: asBool(json['has_analysis']),
      analysisSummary: asStringOrNull(json['analysis_summary']),
      analysisId: asStringOrNull(json['analysis_id']),
      seenCount: asInt(json['seen_count'], 1),
      status: asStringOrNull(json['status']),
      isExpired: asBool(json['expired']),
    );
  }

  /// 卡片上展示的来源名：优先渠道中文名。
  String get displaySource => srcName ?? src ?? source;
}

/// 资讯详情（对应 `NewsDetailOut`，继承列表项的全部字段）。
class NewsDetail extends NewsItem {
  const NewsDetail({
    required super.id,
    required super.title,
    super.summary,
    required super.source,
    super.src,
    super.srcName,
    super.kind,
    super.channels,
    super.publishTime,
    super.ingestedAt,
    super.score,
    super.band,
    super.scoreReason,
    required super.tags,
    required super.entities,
    required super.hasAnalysis,
    super.analysisSummary,
    super.analysisId,
    required super.seenCount,
    super.status,
    super.isExpired,
    this.content,
    required this.contentTruncated,
    this.url,
    required this.scoreHistory,
    required this.relatedNews,
  });

  final String? content;
  final bool contentTruncated;
  final String? url;
  final List<ScoreHistory> scoreHistory;
  final List<RelatedNews> relatedNews;

  factory NewsDetail.fromJson(Object? raw) {
    final json = raw is Map ? Map<String, dynamic>.from(raw) : const {};
    final base = NewsItem.fromJson(json);
    final rawHistory = json['score_history'];
    final rawRelated = json['related_news'];
    return NewsDetail(
      id: base.id,
      title: base.title,
      summary: base.summary,
      source: base.source,
      src: base.src,
      srcName: base.srcName,
      kind: base.kind,
      channels: base.channels,
      publishTime: base.publishTime,
      ingestedAt: base.ingestedAt,
      score: base.score,
      band: base.band,
      scoreReason: base.scoreReason,
      tags: base.tags,
      entities: base.entities,
      hasAnalysis: base.hasAnalysis,
      analysisSummary: base.analysisSummary,
      analysisId: base.analysisId,
      seenCount: base.seenCount,
      status: base.status,
      isExpired: base.isExpired,
      content: asStringOrNull(json['content']),
      contentTruncated: asBool(json['content_truncated']),
      url: asStringOrNull(json['url']),
      scoreHistory:
          rawHistory is List ? rawHistory.map(ScoreHistory.fromJson).toList() : const [],
      relatedNews:
          rawRelated is List ? rawRelated.map(RelatedNews.fromJson).toList() : const [],
    );
  }
}
