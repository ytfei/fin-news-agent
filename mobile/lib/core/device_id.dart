import 'package:shared_preferences/shared_preferences.dart';
import 'package:uuid/uuid.dart';

/// 匿名设备 ID：MVP 阶段用于隔离会话数据。
///
/// 后端 `src/fin_news/api/deps.py` 按请求头 `X-Device-Id` 区分设备，其注释写明
/// 「MVP 阶段使用匿名设备 ID；接入账号体系后替换为 Bearer JWT」。
/// 因此这里只需保证两点：同一设备稳定不变、不同设备互不相同。
/// 后续接入登录态时，把 Dio 拦截器里的注入逻辑换成 Token 即可，业务代码无感。
class DeviceId {
  DeviceId._();

  static const _key = 'device.id';
  static const _uuid = Uuid();

  /// 进程内缓存，避免每次请求都读磁盘
  static String? _cached;

  /// 测试期可直接覆盖
  static String? override;

  static Future<String> get() async {
    final forced = override;
    if (forced != null && forced.isNotEmpty) return forced;

    final cached = _cached;
    if (cached != null && cached.isNotEmpty) return cached;

    final prefs = await SharedPreferences.getInstance();
    var id = prefs.getString(_key);
    if (id == null || id.isEmpty) {
      id = _uuid.v4();
      await prefs.setString(_key, id);
    }
    _cached = id;
    return id;
  }
}
