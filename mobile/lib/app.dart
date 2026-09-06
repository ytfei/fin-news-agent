import 'package:flutter/material.dart';

import 'pages/analysis_page.dart';
import 'pages/chat_page.dart';
import 'pages/news_feed_page.dart';
import 'pages/reports_page.dart';
import 'theme/app_theme.dart';

/// 应用根组件：底部四项导航 + 主题。
///
/// 页面用 [IndexedStack] 而非直接切换，这样切换 Tab 时各页的滚动位置与
/// 已加载数据不会丢失（移动端信息流场景的标配）。
class FinNewsApp extends StatefulWidget {
  const FinNewsApp({super.key});

  @override
  State<FinNewsApp> createState() => _FinNewsAppState();
}

class _FinNewsAppState extends State<FinNewsApp> {
  int _index = 0;

  static const _pages = <Widget>[
    NewsFeedPage(),
    AnalysisPage(),
    ReportsPage(),
    ChatPage(),
  ];

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '财经快讯',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.light(),
      darkTheme: AppTheme.dark(),
      // 金融场景默认深色：长时间阅读更省眼，也更显专业
      themeMode: ThemeMode.dark,
      home: Scaffold(
        body: IndexedStack(index: _index, children: _pages),
        bottomNavigationBar: NavigationBar(
          selectedIndex: _index,
          onDestinationSelected: (value) => setState(() => _index = value),
          destinations: const [
            NavigationDestination(
              icon: Icon(Icons.article_outlined),
              selectedIcon: Icon(Icons.article),
              label: '资讯',
            ),
            NavigationDestination(
              icon: Icon(Icons.analytics_outlined),
              selectedIcon: Icon(Icons.analytics),
              label: '深度分析',
            ),
            NavigationDestination(
              icon: Icon(Icons.assessment_outlined),
              selectedIcon: Icon(Icons.assessment),
              label: '报告',
            ),
            NavigationDestination(
              icon: Icon(Icons.chat_bubble_outline),
              selectedIcon: Icon(Icons.chat_bubble),
              label: '追问',
            ),
          ],
        ),
      ),
    );
  }
}
