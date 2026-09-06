import '../core/json_utils.dart';

/// 受益 / 受损标的（对应 `ImpactTargetOut`）。
///
/// direction: positive = 受益，negative = 受损。
class ImpactTarget {
  const ImpactTarget({
    this.code,
    this.name,
    required this.type,
    required this.reason,
    required this.direction,
  });

  final String? code;
  final String? name;
  final String type; // stock / sector / index
  final String reason;
  final String direction; // positive / negative

  bool get isPositive => direction == 'positive';

  factory ImpactTarget.fromJson(Object? raw) {
    final json = raw is Map ? Map<String, dynamic>.from(raw) : const {};
    return ImpactTarget(
      code: asStringOrNull(json['code']),
      name: asStringOrNull(json['name']),
      type: asString(json['type'], 'sector'),
      reason: asString(json['reason']),
      direction: asString(json['direction'], 'positive'),
    );
  }
}

/// 分析报告（对应 `AnalysisReportOut`）。
class AnalysisReport {
  const AnalysisReport({
    required this.id,
    required this.agentType,
    this.newsId,
    this.newsTitle,
    this.tradeDate,
    required this.title,
    required this.summary,
    this.score,
    this.band,
    this.sentiment,
    this.impactLevel,
    this.horizon,
    this.confidence,
    required this.beneficiaries,
    required this.victims,
    required this.entities,
    required this.references,
    required this.status,
    this.model,
    this.promptVersion,
    this.publishedAt,
    required this.disclaimer,
  });

  final String id;
  final String agentType;
  final String? newsId;
  final String? newsTitle;
  final DateTime? tradeDate;
  final String title;
  final String summary;
  final int? score;
  final String? band;
  final String? sentiment;
  final String? impactLevel;
  final String? horizon;
  final double? confidence;
  final List<ImpactTarget> beneficiaries;
  final List<ImpactTarget> victims;

  /// 后端是 `list[dict]`，结构随 Agent 不同而有差异，保留为 Map 按需取值
  final List<Map<String, dynamic>> entities;
  final List<Map<String, dynamic>> references;

  /// PUBLISHED（正常） / DEGRADED（降级产出） / SUPERSEDED（被新版取代）
  final String status;
  final String? model;
  final String? promptVersion;
  final DateTime? publishedAt;
  final String disclaimer;

  bool get isDegraded => status == 'DEGRADED';

  factory AnalysisReport.fromJson(Object? raw) {
    final json = raw is Map ? Map<String, dynamic>.from(raw) : const {};
    return AnalysisReport(
      id: asString(json['id']),
      agentType: asString(json['agent_type']),
      newsId: asStringOrNull(json['news_id']),
      newsTitle: asStringOrNull(json['news_title']),
      tradeDate: asDateTime(json['trade_date']),
      title: asString(json['title']),
      summary: asString(json['summary']),
      score: asIntOrNull(json['score']),
      band: asStringOrNull(json['band']),
      sentiment: asStringOrNull(json['sentiment']),
      impactLevel: asStringOrNull(json['impact_level']),
      horizon: asStringOrNull(json['horizon']),
      confidence: asDouble(json['confidence']),
      beneficiaries: _targets(json['beneficiaries']),
      victims: _targets(json['victims']),
      entities: asMapList(json['entities']),
      references: asMapList(json['references']),
      status: asString(json['status']),
      model: asStringOrNull(json['model']),
      promptVersion: asStringOrNull(json['prompt_version']),
      publishedAt: asDateTime(json['published_at']),
      disclaimer: asString(
        json['disclaimer'],
        'AI 生成，仅供参考，不构成投资建议。',
      ),
    );
  }

  static List<ImpactTarget> _targets(Object? raw) =>
      raw is List ? raw.map(ImpactTarget.fromJson).toList() : const [];
}

/// 分析详情（对应 `AnalysisDetailOut`）。
///
/// `content` 是 `dict[str, Any]`，内部结构由各 Agent 的 Pydantic 模型决定
/// （如 headline / summary / bullets / verdict 等），这里保留为 Map，
/// UI 层按需取值并做缺键兜底，避免后端加字段就导致解析失败。
class AnalysisDetail extends AnalysisReport {
  const AnalysisDetail({
    required super.id,
    required super.agentType,
    super.newsId,
    super.newsTitle,
    super.tradeDate,
    required super.title,
    required super.summary,
    super.score,
    super.band,
    super.sentiment,
    super.impactLevel,
    super.horizon,
    super.confidence,
    required super.beneficiaries,
    required super.victims,
    required super.entities,
    required super.references,
    required super.status,
    super.model,
    super.promptVersion,
    super.publishedAt,
    required super.disclaimer,
    required this.content,
    required this.externalSources,
    this.run,
  });

  final Map<String, dynamic> content;
  final List<Map<String, dynamic>> externalSources;
  final Map<String, dynamic>? run;

  factory AnalysisDetail.fromJson(Object? raw) {
    final json = raw is Map ? Map<String, dynamic>.from(raw) : const {};
    final base = AnalysisReport.fromJson(json);
    return AnalysisDetail(
      id: base.id,
      agentType: base.agentType,
      newsId: base.newsId,
      newsTitle: base.newsTitle,
      tradeDate: base.tradeDate,
      title: base.title,
      summary: base.summary,
      score: base.score,
      band: base.band,
      sentiment: base.sentiment,
      impactLevel: base.impactLevel,
      horizon: base.horizon,
      confidence: base.confidence,
      beneficiaries: base.beneficiaries,
      victims: base.victims,
      entities: base.entities,
      references: base.references,
      status: base.status,
      model: base.model,
      promptVersion: base.promptVersion,
      publishedAt: base.publishedAt,
      disclaimer: base.disclaimer,
      content: json['content'] is Map
          ? Map<String, dynamic>.from(json['content'] as Map)
          : const {},
      externalSources: asMapList(json['external_sources']),
      run: json['run'] is Map ? Map<String, dynamic>.from(json['run'] as Map) : null,
    );
  }

  /// 详情页正文：优先 content 内的结构化字段，缺失时退回 summary。
  List<String> get bullets => asStringList(content['bullets']);
  String get headline => asString(content['headline']);
}

/// 深度分析列表项（对应 `DeepAnalysisOut`）。
///
/// 与 [AnalysisDetail] 的差异：它额外带原资讯上下文（news_title / news_source /
/// news_publish_time）与要点预览 bullets，让列表页一次取足，避免 N+1 请求。
class DeepAnalysis {
  const DeepAnalysis({
    required this.id,
    required this.agentType,
    this.newsId,
    this.newsTitle,
    this.newsSource,
    this.newsPublishTime,
    required this.title,
    required this.summary,
    this.score,
    this.band,
    this.sentiment,
    this.impactLevel,
    this.horizon,
    this.confidence,
    required this.beneficiaries,
    required this.victims,
    required this.bullets,
    this.publishedAt,
    required this.disclaimer,
  });

  final String id;
  final String agentType;
  final String? newsId;
  final String? newsTitle;
  final String? newsSource;
  final DateTime? newsPublishTime;
  final String title;
  final String summary;
  final int? score;
  final String? band;
  final String? sentiment;
  final String? impactLevel;
  final String? horizon;
  final double? confidence;
  final List<ImpactTarget> beneficiaries;
  final List<ImpactTarget> victims;
  final List<String> bullets;
  final DateTime? publishedAt;
  final String disclaimer;

  factory DeepAnalysis.fromJson(Object? raw) {
    final json = raw is Map ? Map<String, dynamic>.from(raw) : const {};
    return DeepAnalysis(
      id: asString(json['id']),
      agentType: asString(json['agent_type']),
      newsId: asStringOrNull(json['news_id']),
      newsTitle: asStringOrNull(json['news_title']),
      newsSource: asStringOrNull(json['news_source']),
      newsPublishTime: asDateTime(json['news_publish_time']),
      title: asString(json['title']),
      summary: asString(json['summary']),
      score: asIntOrNull(json['score']),
      band: asStringOrNull(json['band']),
      sentiment: asStringOrNull(json['sentiment']),
      impactLevel: asStringOrNull(json['impact_level']),
      horizon: asStringOrNull(json['horizon']),
      confidence: asDouble(json['confidence']),
      beneficiaries: AnalysisReport._targets(json['beneficiaries']),
      victims: AnalysisReport._targets(json['victims']),
      bullets: asStringList(json['bullets']),
      publishedAt: asDateTime(json['published_at']),
      disclaimer: asString(
        json['disclaimer'],
        'AI 生成，仅供参考，不构成投资建议。',
      ),
    );
  }
}
