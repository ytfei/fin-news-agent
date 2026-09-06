import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../config/env.dart';
import '../providers/api_providers.dart';

/// 后端地址设置。
///
/// 为什么需要它：iOS 模拟器可用 `localhost` 访问宿主机，但**真机必须填局域网 IP**
/// （如 `http://192.168.1.10:8000/api/v1`）。提供一个应用内入口，免去每次改地址
/// 都要重新编译。
class SettingsPage extends ConsumerStatefulWidget {
  const SettingsPage({super.key});

  @override
  ConsumerState<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends ConsumerState<SettingsPage> {
  final TextEditingController _controller = TextEditingController();
  bool _loaded = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final url = await EnvConfig.resolveBaseUrl();
    if (!mounted) return;
    setState(() {
      _controller.text = url;
      _loaded = true;
    });
  }

  Future<void> _save() async {
    final url = _controller.text.trim();
    if (url.isEmpty) return;
    await EnvConfig.setBaseUrl(url);
    // 让所有依赖地址的 provider 重新拉取
    ref.invalidate(baseUrlProvider);
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..clearSnackBars()
      ..showSnackBar(const SnackBar(content: Text('已保存，正在重新加载数据')));
    Navigator.of(context).pop();
  }

  Future<void> _reset() async {
    await EnvConfig.clearBaseUrl();
    ref.invalidate(baseUrlProvider);
    await _load();
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..clearSnackBars()
      ..showSnackBar(
        SnackBar(content: Text('已恢复默认：${EnvConfig.fallbackBaseUrl}')),
      );
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('设置')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text('后端地址', style: theme.textTheme.titleMedium),
            const SizedBox(height: 8),
            TextField(
              controller: _controller,
              enabled: _loaded,
              autocorrect: false,
              enableSuggestions: false,
              keyboardType: TextInputType.url,
              decoration: const InputDecoration(
                hintText: 'http://192.168.1.10:8000/api/v1',
                border: OutlineInputBorder(),
                isDense: true,
              ),
            ),
            const SizedBox(height: 10),
            Text(
              'iOS 模拟器填 http://localhost:8000/api/v1；'
              '真机需填电脑的局域网 IP，且手机与电脑在同一 Wi-Fi。',
              style: theme.textTheme.bodySmall,
            ),
            const SizedBox(height: 20),
            FilledButton(onPressed: _save, child: const Text('保存并重连')),
            const SizedBox(height: 8),
            OutlinedButton(
              onPressed: _reset,
              child: const Text('恢复默认地址'),
            ),
          ],
        ),
      ),
    );
  }
}
