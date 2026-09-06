import 'package:flutter/material.dart';

/// 语义色板。
///
/// 设计约束：颜色只用于传达**有语义**的信息——涨跌与评分分档。其余界面元素
/// 一律用中性色，避免大面积高饱和色块干扰长时间阅读。
///
/// 涨跌遵循 A 股习惯：**红涨绿跌**（与欧美相反，不要照搬国外设计稿）。
class AppColors {
  AppColors._();

  // ---- 涨跌 ----
  static const up = Color(0xFFE5484D); // 涨 / 受益
  static const down = Color(0xFF30A46C); // 跌 / 受损
  static const flat = Color(0xFF6B7280); // 平

  // ---- 评分分档（与后端 ScoreBand 一一对应）----
  // NOISE(1-3) 噪声 / STOCK(4-5) 个股 / INDUSTRY(6-7) 行业 / MACRO(8-10) 宏观
  static const bandNoise = Color(0xFF6B7280);
  static const bandStock = Color(0xFF5B9BFF);
  static const bandIndustry = Color(0xFFF5A623);
  static const bandMacro = Color(0xFF8B5CF6);

  /// 按分档取色；未知分档回落为中性灰。
  static Color bandColor(String? band) {
    switch (band?.toUpperCase()) {
      case 'NOISE':
        return bandNoise;
      case 'STOCK':
        return bandStock;
      case 'INDUSTRY':
        return bandIndustry;
      case 'MACRO':
        return bandMacro;
      default:
        return flat;
    }
  }

  /// 分档的中文短标签，用于卡片角标。
  static String bandLabel(String? band) {
    switch (band?.toUpperCase()) {
      case 'NOISE':
        return '噪声';
      case 'STOCK':
        return '个股';
      case 'INDUSTRY':
        return '行业';
      case 'MACRO':
        return '宏观';
      default:
        return '未评分';
    }
  }
}

/// Material 3 主题（金融场景默认深色，长时间盯盘/阅读更省眼且显专业）。
class AppTheme {
  AppTheme._();

  static const _primary = Color(0xFF1A73E8);
  static const _primaryDark = Color(0xFF5B9BFF); // 深色底上提高明度保证对比度

  static ThemeData dark() => _build(Brightness.dark);

  static ThemeData light() => _build(Brightness.light);

  static ThemeData _build(Brightness brightness) {
    final dark = brightness == Brightness.dark;
    final primary = dark ? _primaryDark : _primary;
    final surface = dark ? const Color(0xFF17202A) : const Color(0xFFFFFFFF);
    final background = dark ? const Color(0xFF0F1419) : const Color(0xFFF5F7FA);
    final onSurface = dark ? const Color(0xFFE8EAED) : const Color(0xFF202124);
    final onSurfaceMuted = dark ? const Color(0xFF9AA0A6) : const Color(0xFF5F6368);

    final scheme = ColorScheme(
      brightness: brightness,
      primary: primary,
      onPrimary: Colors.white,
      secondary: primary,
      onSecondary: Colors.white,
      error: AppColors.up,
      onError: Colors.white,
      surface: surface,
      onSurface: onSurface,
      // Flutter 3.22+ 起 surfaceTint 等拆分字段需显式给值
      surfaceContainerHighest: dark ? const Color(0xFF1F2A37) : const Color(0xFFE8EAED),
      outline: onSurfaceMuted.withValues(alpha: 0.24),
      outlineVariant: onSurfaceMuted.withValues(alpha: 0.12),
      shadow: Colors.black,
      scrim: Colors.black,
      inverseSurface: dark ? const Color(0xFFE8EAED) : const Color(0xFF202124),
      onInverseSurface: dark ? const Color(0xFF202124) : const Color(0xFFE8EAED),
      inversePrimary: _primary,
      surfaceTint: primary,
    );

    return ThemeData(
      useMaterial3: true,
      brightness: brightness,
      colorScheme: scheme,
      scaffoldBackgroundColor: background,
      // iOS 上默认字体即 PingFang SC，此处显式指定以防跨平台差异
      fontFamily: 'PingFang SC',
      appBarTheme: AppBarTheme(
        centerTitle: false,
        elevation: 0,
        scrolledUnderElevation: 1,
        backgroundColor: background,
        foregroundColor: onSurface,
        surfaceTintColor: Colors.transparent,
        titleTextStyle: TextStyle(
          fontFamily: 'PingFang SC',
          fontSize: 20,
          fontWeight: FontWeight.w600,
          color: onSurface,
        ),
      ),
      cardTheme: CardThemeData(
        color: surface,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
          side: BorderSide(color: onSurfaceMuted.withValues(alpha: 0.14)),
        ),
      ),
      listTileTheme: ListTileThemeData(
        textColor: onSurface,
        iconColor: onSurfaceMuted,
      ),
      dividerTheme: DividerThemeData(
        color: onSurfaceMuted.withValues(alpha: 0.14),
        thickness: 1,
        space: 1,
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: surface,
        indicatorColor: primary.withValues(alpha: 0.16),
        labelTextStyle: WidgetStateProperty.resolveWith((states) {
          final selected = states.contains(WidgetState.selected);
          return TextStyle(
            fontFamily: 'PingFang SC',
            fontSize: 12,
            fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
            color: selected ? primary : onSurfaceMuted,
          );
        }),
      ),
      textTheme: TextTheme(
        headlineSmall: TextStyle(
          fontFamily: 'PingFang SC',
          fontSize: 20,
          fontWeight: FontWeight.w600,
          color: onSurface,
        ),
        titleMedium: TextStyle(
          fontFamily: 'PingFang SC',
          fontSize: 16,
          fontWeight: FontWeight.w500,
          color: onSurface,
        ),
        bodyMedium: TextStyle(
          fontFamily: 'PingFang SC',
          fontSize: 14,
          fontWeight: FontWeight.w400,
          color: onSurface,
        ),
        bodySmall: TextStyle(
          fontFamily: 'PingFang SC',
          fontSize: 12,
          fontWeight: FontWeight.w400,
          color: onSurfaceMuted,
        ),
      ),
      // iOS 风格的滚动物理：内容到底/到顶有回弹
      platform: TargetPlatform.iOS,
    );
  }
}
