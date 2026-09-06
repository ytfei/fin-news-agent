# fin-news 移动端（Flutter / iOS）

财经资讯 Agent 的移动端 App，定位**消费端**：把资讯流、深度分析、盘前盘后报告、AI 追问
搬到手机上。后端完全复用，不改动任何 Python 代码。

> **当前状态：首版只交付 iOS。** 代码保持跨平台结构（无平台专属分支），
> `android/` 仅保留 `flutter create` 生成的骨架，未做适配与打包。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 框架 | Flutter 3.44（Dart 3.12） |
| 状态管理 | flutter_riverpod 3.x |
| 网络 | dio（REST）+ http（SSE 流式） |
| 本地存储 | shared_preferences（设备 ID、后端地址） |
| 序列化 | 手写 `fromJson`（针对后端「空值显式输出 null」的契约做了容错） |

## 快速开始

### 1. 先启动后端

```bash
# 在仓库根目录
docker compose up -d
# 或
uv run python -m fin_news.main
```

确认健康检查可访问（注意是 `/health` 而非 `/system/health`）：

```bash
curl http://localhost:8000/api/v1/health
# {"status":"ok","db":"up","llm":"up","event_backlog":7283,...}
```

### 2. 运行 App

```bash
cd mobile
flutter pub get
flutter run                    # 自动选择已启动的模拟器/真机
flutter run -d "iPhone 17 Pro" # 指定设备
```

**iOS 模拟器**可直接用 `localhost` 访问宿主机后端（模拟器共享 Mac 的网络栈）。

### 3. 真机 / 局域网

真机必须用电脑的**局域网 IP**（不能用 localhost）：

```bash
flutter run --dart-define=API_BASE_URL=http://192.168.1.10:8000/api/v1
```

也可以在 App 内「资讯页 → 右上角设置」直接填地址并保存，无需重新编译。

> 真机用 HTTP 明文已在 `ios/Runner/Info.plist` 放开 `NSAllowsLocalNetworking`（仅局域网）。
> 生产环境请改用 HTTPS 并移除该配置。

## 功能

| 模块 | 说明 |
| --- | --- |
| **资讯** | 渠道筛选（带各渠道条数）、按时间/评分/重要度排序、下拉刷新、上拉分页；已分析的资讯展示分析摘要，点击进入分析详情 |
| **深度分析** | 分档筛选、要点预览、详情页（受益/受损标的、影响等级、置信度、参考来源、降级提示） |
| **报告** | 盘前展望 / 盘后复盘切换、日期选择、历史简报归档 |
| **追问** | 会话列表（左滑删除）、SSE 流式打字机输出、停止生成、引用来源、免责声明 |

**不做**（留在 Web 端 / CLI）：运维操作（补数、重算评分、重跑分析、死信重放）、
评估集标注、个股详情与语义检索。

## 目录结构

```
lib/
├── main.dart              # 入口（ProviderScope）
├── app.dart               # 底部四项导航 + 主题
├── config/env.dart        # 后端地址（dart-define / 本地覆盖）
├── core/
│   ├── api_client.dart    # Dio 封装：X-Device-Id 注入、错误归一化
│   ├── api_exception.dart # 统一异常（中文提示，可直接展示）
│   ├── device_id.dart     # UUID 生成 + 持久化
│   ├── json_utils.dart    # 宽松取值（后端 null 容错）
│   └── sse_client.dart    # SSE 解析（字节累积后解码，防中文乱码）
├── models/                # 对齐后端 schemas.py 的模型
├── providers/             # Riverpod 状态层（四个模块各一个）
├── pages/                 # 四个主页面 + 设置页
├── widgets/               # 复用组件（卡片、分档色标、三态容器…）
└── theme/app_theme.dart   # Material 3 深色主题 + 语义色板
```

## 适配后端的几个关键点

改动后端契约时，这里最容易出问题，列出来备查：

1. **字段是纯 snake_case**，后端无 alias、也没有 camelCase 变体，Dart 侧不要加 `@JsonKey(name:)`。
2. **空值会显式输出 `null`**（Pydantic 未开 `exclude_none`），所有字段都必须可空并做判空处理，
   否则一条脏数据会让整页解析失败。
3. **时间字段偏移量不统一**：`published_at` 是 UTC（`+00:00`），`publish_time` / `ingested_at`
   是北京时间（`+08:00`）。统一 `DateTime.parse()` 后 `toLocal()`，不要假定结尾是 `Z`。
4. **分页终止必须用 `has_more`**，不能用「本页条数 == page_size」推断——最后一页可能正好满页。
5. **`/news` 默认只返回近 24 小时**，要取更早资讯必须显式传 `start` / `end`。
6. **追问的 `references` 事件在流结束后才下发**，不在首个 `delta` 之前，UI 要等到 `done` 再渲染引用区。
7. **SSE 必须按字节累积后再解码**：网络分片会切断多字节汉字，逐块解码会出现乱码
   （`test/sse_test.dart` 有专门的用例覆盖）。

## 测试

```bash
flutter analyze   # 静态检查（应保持 No issues found）
flutter test      # 16 个用例：模型解析、SSE 解析、App 启动
```

SSE 的用例特意按 1 字节切分中文来验证不乱码，这是流式输出最容易踩的坑。

## 打包（iOS）

```bash
flutter build ipa --dart-define=API_BASE_URL=https://your-domain/api/v1
```

需要：Apple 开发者账号、在 Xcode 中配置签名（`ios/Runner.xcodeproj`）。
Archive 前记得确认 `Info.plist` 的 `NSAllowsLocalNetworking` 已移除（若后端是 HTTPS）。

## 已知限制

- Android 未适配（骨架存在但从未运行过）。后续要支持时，主要是补 `INTERNET` 权限、
  滚动物理改为 `ClampingScrollPhysics`、以及签名配置，业务逻辑无需改动。
- 无登录体系：会话按 `X-Device-Id` 隔离（后端注释已说明后续会替换为 Bearer JWT），
  换设备或重装 App 后会话不保留。
