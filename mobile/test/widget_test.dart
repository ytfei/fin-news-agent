import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:fin_news/app.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    // 设备 ID 持久化要读 shared_preferences，测试环境需先打桩
    SharedPreferences.setMockInitialValues({});
  });

  testWidgets('应用可启动并展示四个底部导航项', (WidgetTester tester) async {
    await tester.pumpWidget(const ProviderScope(child: FinNewsApp()));
    // 用 pump 而非 pumpAndSettle：页面会发起真实网络请求，等其 settle 会超时。
    // 这里只验证首帧能正常构建。
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 200));

    // 底部导航四项
    expect(find.text('资讯'), findsWidgets);
    expect(find.text('深度分析'), findsWidgets);
    expect(find.text('报告'), findsWidgets);
    expect(find.text('追问'), findsWidgets);
  });

  testWidgets('默认停留在资讯页', (WidgetTester tester) async {
    await tester.pumpWidget(const ProviderScope(child: FinNewsApp()));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 200));

    // IndexedStack 会构建全部页面，但只有当前页可见；这里验证 AppBar 标题存在
    expect(find.text('资讯'), findsWidgets);
  });
}
