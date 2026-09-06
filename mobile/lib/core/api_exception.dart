/// 统一的接口异常：已转成可直接展示给用户的中文提示。
///
/// 数据层只抛这一种异常，UI 层无需区分 Dio 的各类错误类型。
class ApiException implements Exception {
  const ApiException(this.message, {this.status});

  /// 可直接展示的中文提示
  final String message;

  /// HTTP 状态码；网络不通/超时等无响应的情况为 null
  final int? status;

  bool get isNetworkError => status == null;

  @override
  String toString() => 'ApiException($status): $message';
}
