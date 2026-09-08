---
name: selftest-jimeng-api-skill
overview: 在 cli.py 的 selftest 命令中新增「即梦文生图 skill」自检项：读取环境变量 JIMENG_SESSION_ID，用 subprocess 直接调用 skills/jimeng-api/scripts/generate_image.py 脚本，用一个写死的简单提示词生成图片并下载到 pic/ 目录，脚本返回码为 0 即判定 skill 正常运行。
todos:
  - id: add-deps
    content: 在 pyproject.toml 添加 requests 与 Pillow 依赖并 uv sync
    status: completed
  - id: add-selftest-jimeng
    content: 在 cli.py 新增 _selftest_jimeng 函数，subprocess 调用 jimeng-api 脚本生成图片
    status: completed
    dependencies:
      - add-deps
  - id: wire-selftest
    content: 将即梦自检接入 _cmd_selftest，更新汇总日志与模块 docstring
    status: completed
    dependencies:
      - add-selftest-jimeng
  - id: verify
    content: 运行 ruff、pytest，并用 JIMENG_SESSION_ID 实测 selftest 生成图片成功
    status: completed
    dependencies:
      - wire-selftest
---

## 产品概述

在 CLI `selftest` 命令中新增「即梦文生图」自检项，通过子进程调用 `jimeng-api` skill 的 `generate_image.py` 脚本生成并下载一张测试图片，验证该 skill 正常运行。

## 核心功能

- 从环境变量 `JIMENG_SESSION_ID` 读取即梦 API 的 sessionid，未设置时跳过该项自检（不阻断其他自检）
- 用固定测试提示词调用脚本的文生图模式，生成图片并下载到项目 `pic/` 目录
- 以脚本返回码与输出中的成功标志判定自检结果，并计入 selftest 总结果
- 补充脚本运行时依赖 `requests` 与 `Pillow`，保证 `sys.executable` 能直接运行脚本

## 技术栈

- Python 标准库 `subprocess` / `os` / `pathlib`（无新增第三方运行时依赖用于自检逻辑本身）
- uv 依赖管理：`pyproject.toml` 新增 `requests` 与 `Pillow`（jimeng-api 脚本的硬/软依赖）

## 实现方法

沿用 `cli.py` 现有 `_selftest_sources` / `_selftest_llm` / `_selftest_embedding` 的统一模式，新增 `_selftest_jimeng(settings) -> bool`，接入 `_cmd_selftest` 的 `all_ok` 汇总。核心是 `subprocess.run` 以 `sys.executable` 运行 `skills/jimeng-api/scripts/generate_image.py` 的文生图子命令。

## 关键决策

1. **sessionid 不落代码**：`os.environ.get("JIMENG_SESSION_ID")`，未设置则 warning 跳过并返回 `True`，避免把凭据写进 git。
2. **脚本路径不依赖 cwd**：`Path(__file__).resolve().parents[2] / "skills/jimeng-api/scripts/generate_image.py"`，即项目根下的 skills 目录。
3. **成功判定**：`returncode == 0` 且 stdout 包含 `Successfully generated`（脚本成功时的固定输出），失败时截取 stderr 后 300 字符写入 error 日志。
4. **依赖补齐**：脚本硬依赖 `requests`、软依赖 `Pillow`，当前项目均未声明；加入 `pyproject.toml` 的 dependencies，`uv sync` 后 `sys.executable` 可直接运行，避免 selftest 因 ImportError 误报。
5. **测试提示词**：写死一个简单中文场景（如「一只可爱的橘猫在草地上晒太阳」），符合用户「随便写个提示词」的要求。

## 实现细节

- `_selftest_jimeng` 结构：读环境变量 → 未设置跳过返回 `True` → `subprocess.run([...], capture_output=True, text=True, timeout=300)` → 判定返回 bool。
- `_cmd_selftest` 中 `all_ok = source_ok and llm_ok and embed_ok and jimeng_ok`，结果日志补 `jimeng=jimeng_ok` 字段。
- 更新 `cli.py` 模块 docstring 第 15 行 selftest 说明，补充即梦自检项。

## 目录结构

```
fin-news-v5/
├── pyproject.toml        # [MODIFY] dependencies 新增 requests>=2.28.0 与 Pillow>=9.0.0
└── src/fin_news/cli.py   # [MODIFY] 新增 import、_selftest_jimeng 函数、接入 _cmd_selftest、更新 docstring
```