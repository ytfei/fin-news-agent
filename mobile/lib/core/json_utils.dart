/// 宽松的 JSON 取值工具。
///
/// 为什么需要它：后端 Pydantic 未开启 `exclude_none`，**空值会显式输出 `null`**
/// （而不是省略 key），且部分字段类型是联合类型（如 `str | None`）。若直接用
/// `json['x'] as String` 会在 null 时抛异常，导致整页解析失败。
/// 这里的取值函数一律容错：类型不符就回落默认值，保证单条脏数据不会炸掉整个列表。
library;

/// 取 int；null / 类型不符 / 字符串数字都尽量兼容。
int asInt(Object? value, [int fallback = 0]) {
  if (value == null) return fallback;
  if (value is int) return value;
  if (value is num) return value.toInt();
  if (value is String) return int.tryParse(value) ?? fallback;
  return fallback;
}

/// 取可空 int（区分「没有这个字段」与「值为 0」）。
int? asIntOrNull(Object? value) {
  if (value == null) return null;
  return asInt(value);
}

/// 取 double（后端 float 可能是 int 也可能是小数）。
double? asDouble(Object? value) {
  if (value == null) return null;
  if (value is num) return value.toDouble();
  if (value is String) return double.tryParse(value);
  return null;
}

/// 取非空字符串；null 或其它类型转为空串。
String asString(Object? value, [String fallback = '']) {
  if (value == null) return fallback;
  if (value is String) return value;
  return value.toString();
}

/// 取可空字符串。
String? asStringOrNull(Object? value) {
  if (value == null) return null;
  final text = asString(value);
  return text.isEmpty ? null : text;
}

/// 取 bool；后端布尔字段有默认值，null 时回落。
bool asBool(Object? value, [bool fallback = false]) {
  if (value == null) return fallback;
  if (value is bool) return value;
  if (value is num) return value != 0;
  if (value is String) {
    final text = value.toLowerCase();
    if (text == 'true' || text == '1') return true;
    if (text == 'false' || text == '0') return false;
  }
  return fallback;
}

/// 解析时间。
///
/// 注意后端时间字段的**偏移量不统一**：`published_at` 用 UTC（`+00:00`），
/// 而 `publish_time` / `ingested_at` 是北京时间（`+08:00`）。统一用
/// [DateTime.parse] 解析后再 `toLocal()`，不要假定末尾是 `Z`。
DateTime? asDateTime(Object? value) {
  if (value == null) return null;
  if (value is DateTime) return value;
  if (value is! String || value.isEmpty) return null;
  final parsed = DateTime.tryParse(value);
  return parsed?.toLocal();
}

/// 取对象列表并规整为 `List<Map<String, dynamic>>`。
///
/// 后端不少字段是 `list[dict[str, Any]]`（如 entities / references / content 内的
/// 结构），Dart 侧保留为 Map 比强行建模更灵活（详情页按需取值）。
List<Map<String, dynamic>> asMapList(Object? value) {
  if (value is! List) return const [];
  return value.whereType<Map>().map((e) => Map<String, dynamic>.from(e)).toList();
}

/// 取字符串列表。
List<String> asStringList(Object? value) {
  if (value is! List) return const [];
  return value.map((e) => e?.toString() ?? '').where((e) => e.isNotEmpty).toList();
}
