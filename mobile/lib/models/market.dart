import '../core/json_utils.dart';
import 'analysis.dart';

/// 隔夜美股（盘前简报用）。
class UsQuote {
  const UsQuote({
    required this.symbol,
    this.name,
    this.close,
    this.pctChg,
    this.tradeDate,
  });

  final String symbol;
  final String? name;
  final double? close;

  /// 涨跌幅（百分点，如 -1.23 表示跌 1.23%）
  final double? pctChg;
  final DateTime? tradeDate;

  factory UsQuote.fromJson(Object? raw) {
    final json = raw is Map ? Map<String, dynamic>.from(raw) : const {};
    return UsQuote(
      symbol: asString(json['symbol']),
      name: asStringOrNull(json['name']),
      close: asDouble(json['close']),
      pctChg: asDouble(json['pct_chg']),
      tradeDate: asDateTime(json['trade_date']),
    );
  }
}

/// 历史简报列表项（对应 `BriefMetaOut`），用于报告页的归档入口。
class BriefMeta {
  const BriefMeta({
    required this.tradeDate,
    required this.period,
    required this.reportId,
    required this.title,
    required this.summary,
    this.publishedAt,
  });

  final DateTime? tradeDate;

  /// pre_market / post_market
  final String period;
  final String reportId;
  final String title;
  final String summary;
  final DateTime? publishedAt;

  bool get isPreMarket => period == 'pre_market';

  factory BriefMeta.fromJson(Object? raw) {
    final json = raw is Map ? Map<String, dynamic>.from(raw) : const {};
    return BriefMeta(
      tradeDate: asDateTime(json['trade_date']),
      period: asString(json['period']),
      reportId: asString(json['report_id']),
      title: asString(json['title']),
      summary: asString(json['summary']),
      publishedAt: asDateTime(json['published_at']),
    );
  }
}

/// 盘前 / 盘后简报正文。
///
/// 后端 `PreMarketBriefOut` 与 `PostMarketBriefOut` 都继承 `AnalysisDetailOut`，
/// 只是额外字段不同（盘前带隔夜美股与关注方向，盘后带结论与归因）。
/// 这里**合并成一个类**，UI 按 [period] 决定渲染哪些区块 —— 比拆两个类更好维护，
/// 也避免在两个 Tab 间切换时反复重建模型。
class MarketBrief extends AnalysisDetail {
  const MarketBrief({
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
    required super.content,
    required super.externalSources,
    super.run,
    required this.period,
    required this.usMarket,
    required this.focusDirections,
    required this.verdict,
    required this.attribution,
    required this.nextDayFocus,
  });

  /// pre_market / post_market
  final String period;

  // ---- 盘前专属 ----
  final List<UsQuote> usMarket;
  final List<Map<String, dynamic>> focusDirections;

  // ---- 盘后专属 ----
  final Map<String, dynamic> verdict;
  final List<Map<String, dynamic>> attribution;
  final List<String> nextDayFocus;

  bool get isPreMarket => period == 'pre_market';

  factory MarketBrief.fromJson(Object? raw) {
    final json = raw is Map ? Map<String, dynamic>.from(raw) : const {};
    final base = AnalysisDetail.fromJson(json);
    final rawUs = json['us_market'];
    return MarketBrief(
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
      content: base.content,
      externalSources: base.externalSources,
      run: base.run,
      // period 缺失时按 agent_type 兜底推断
      period: asString(json['period'],
          base.agentType == 'pre_market' ? 'pre_market' : 'post_market'),
      usMarket: rawUs is List ? rawUs.map(UsQuote.fromJson).toList() : const [],
      focusDirections: asMapList(json['focus_directions']),
      verdict: json['verdict'] is Map
          ? Map<String, dynamic>.from(json['verdict'] as Map)
          : const {},
      attribution: asMapList(json['attribution']),
      nextDayFocus: asStringList(json['next_day_focus']),
    );
  }
}
