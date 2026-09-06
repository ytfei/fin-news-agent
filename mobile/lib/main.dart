import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'app.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  // 全局用 ProviderScope 包裹：状态层（Riverpod）在此之下生效
  runApp(const ProviderScope(child: FinNewsApp()));
}
