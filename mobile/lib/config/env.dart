import 'package:shared_preferences/shared_preferences.dart';

/// 运行期配置（主要是后端地址）。
///
/// 地址的来源优先级从高到低：
/// 1. `overrideBaseUrl` —— 测试期直接覆盖，避免依赖 shared_preferences
/// 2. 应用内「设置」里手填的地址（存 shared_preferences，便于真机调试）
/// 3. 编译时 `--dart-define=API_BASE_URL=...`
/// 4. 内置默认值（iOS 模拟器可直接连本机后端；真机需改局域网 IP）
///
/// 为什么必须可配置：iOS 模拟器用 `localhost` 即可访问宿主机，而真机必须填
/// 宿主机的局域网 IP（如 `http://192.168.1.10:8000/api/v1`）。写死必然踩坑。
class EnvConfig {
  EnvConfig._();

  static const String _dartDefineUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'http://localhost:8000/api/v1',
  );

  static const String _overrideKey = 'env.api_base_url';

  /// 测试期覆盖用；非空时优先于本地存储。
  static String? overrideBaseUrl;

  /// 编译时传入或内置的默认地址（设置页展示「恢复默认」时用）。
  static String get fallbackBaseUrl => _dartDefineUrl;

  /// 去掉尾部斜杠并补上 API 前缀，避免拼接出 `//api` 这类路径。
  static String normalize(String url) {
    var text = url.trim();
    if (text.isEmpty) return _dartDefineUrl;
    while (text.endsWith('/')) {
      text = text.substring(0, text.length - 1);
    }
    return text;
  }

  static Future<String> resolveBaseUrl() async {
    final override = overrideBaseUrl;
    if (override != null && override.trim().isNotEmpty) {
      return normalize(override);
    }
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString(_overrideKey);
    if (saved != null && saved.trim().isNotEmpty) {
      return normalize(saved);
    }
    return normalize(_dartDefineUrl);
  }

  static Future<void> setBaseUrl(String url) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_overrideKey, normalize(url));
  }

  static Future<void> clearBaseUrl() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_overrideKey);
  }
}
