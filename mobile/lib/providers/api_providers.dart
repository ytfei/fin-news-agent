import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/env.dart';
import '../core/api_client.dart';

/// 后端地址：dart-define → 本地存储 → 内置默认。
///
/// 放在 FutureProvider 里是因为本地存储要异步读取；设置页改完地址后
/// invalidate 本 provider 即可让所有接口拿到新地址。
final baseUrlProvider = FutureProvider<String>((ref) => EnvConfig.resolveBaseUrl());

/// 接口客户端。地址变化时自动重建。
final apiClientProvider = FutureProvider<ApiClient>((ref) async {
  final baseUrl = await ref.watch(baseUrlProvider.future);
  return ApiClient(baseUrl: baseUrl);
});

// 说明：改地址与恢复默认由 settings_page 直接调用 EnvConfig + invalidate，
// 不在这里封 Ref 参数的函数——Riverpod 3 中 WidgetRef 并非 Ref 的子类型，
// 传参会报类型不匹配。
