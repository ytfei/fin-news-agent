import 'package:dio/dio.dart';

import 'api_exception.dart';
import 'device_id.dart';

/// 后端接口客户端（Dio 封装）。
///
/// 职责：
/// * 统一注入 `X-Device-Id`（MVP 阶段的匿名身份，后续替换为 Bearer Token 即可）
/// * 把 Dio 的各类异常**归一化**为中文提示的 [ApiException]，UI 层无需认识 Dio
/// * 204 无 body 的接口返回 null
class ApiClient {
  ApiClient({required this.baseUrl}) {
    _dio = Dio(
      BaseOptions(
        baseUrl: baseUrl,
        connectTimeout: const Duration(seconds: 15),
        receiveTimeout: const Duration(seconds: 60),
        headers: const {'Content-Type': 'application/json'},
        // 4xx/5xx 一律抛 DioException，由 _toApiException 统一转换
        validateStatus: (status) => status != null && status < 400,
      ),
    )..interceptors.add(
        InterceptorsWrapper(
          onRequest: (options, handler) async {
            options.headers['X-Device-Id'] = await DeviceId.get();
            handler.next(options);
          },
        ),
      );
  }

  final String baseUrl;
  late final Dio _dio;

  /// 原始 Dio 实例（SSE 等需要自定义选项时使用）。
  Dio get dio => _dio;

  Future<T?> get<T>(
    String path, {
    Map<String, dynamic>? queryParameters,
  }) async {
    try {
      final resp = await _dio.get<dynamic>(
        path,
        queryParameters: _cleanQuery(queryParameters),
      );
      return _unwrap<T>(resp);
    } on DioException catch (e) {
      throw _toApiException(e);
    }
  }

  Future<T?> post<T>(String path, {Object? body}) async {
    try {
      final resp = await _dio.post<dynamic>(path, data: body);
      return _unwrap<T>(resp);
    } on DioException catch (e) {
      throw _toApiException(e);
    }
  }

  Future<T?> delete<T>(String path) async {
    try {
      final resp = await _dio.delete<dynamic>(path);
      return _unwrap<T>(resp);
    } on DioException catch (e) {
      throw _toApiException(e);
    }
  }

  /// 去掉 null 值，避免拼出 `?band=null` 这类参数。
  Map<String, dynamic>? _cleanQuery(Map<String, dynamic>? query) {
    if (query == null) return null;
    final cleaned = <String, dynamic>{}
      ..addEntries(
        query.entries.where((e) => e.value != null),
      );
    return cleaned.isEmpty ? null : cleaned;
  }

  T? _unwrap<T>(Response<dynamic> resp) {
    if (resp.statusCode == 204) return null;
    return resp.data as T?;
  }

  ApiException _toApiException(DioException e) {
    switch (e.type) {
      case DioExceptionType.connectionTimeout:
      case DioExceptionType.sendTimeout:
        return const ApiException('网络连接超时，请检查网络或后端地址');
      case DioExceptionType.receiveTimeout:
        // 分析类接口耗时长（实测单条可达 1~6 分钟），这里提示要对应上
        return const ApiException('服务响应超时，请稍后重试');
      case DioExceptionType.connectionError:
        return const ApiException(
          '无法连接服务器，请确认后端已启动、地址正确且手机与电脑在同一网络',
        );
      case DioExceptionType.badCertificate:
        return const ApiException('服务器证书校验失败');
      case DioExceptionType.cancel:
        return const ApiException('请求已取消');
      case DioExceptionType.badResponse:
        return ApiException(
          _extractDetail(e.response),
          status: e.response?.statusCode,
        );
      case DioExceptionType.transformTimeout:
        return const ApiException('响应解析超时，请稍后重试');
      case DioExceptionType.unknown:
        return ApiException(e.message ?? '未知网络错误');
    }
    // 这里刻意不加 default：Dio 的枚举已穷尽匹配，将来它若新增错误类型，
    // 编译期就会报「未穷尽」，提醒我们显式处理，而不是静默兜底。
  }

  /// 后端统一返回 `{"detail": "..."}`（见 web/src/api/client.ts 的处理）。
  String _extractDetail(Response<dynamic>? resp) {
    final status = resp?.statusCode;
    final data = resp?.data;
    if (data is Map) {
      final detail = data['detail'];
      if (detail is String && detail.isNotEmpty) return detail;
      if (detail != null) return detail.toString();
    }
    if (data is String && data.isNotEmpty && data.length < 200) return data;
    if (status == null) return '请求失败';
    if (status >= 500) return '服务暂时不可用（$status）';
    return '请求失败（$status）';
  }
}
