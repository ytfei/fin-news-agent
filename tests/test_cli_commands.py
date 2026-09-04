"""CLI 命令接线测试：防止新增命令忘记注册到 choices / _dispatch。"""
import pytest

from fin_news.cli import _dispatch, main


def _parse(argv: list[str]):
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=[
            "ingest", "pipeline", "worker", "score", "embed", "sweep",
            "premarket", "postmarket", "status", "selftest",
        ],
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["embed"], ("embed", None, False)),
        (["embed", "--limit", "20"], ("embed", 20, False)),
        (["sweep"], ("sweep", None, False)),
        (["sweep", "--apply"], ("sweep", None, True)),
        (["score"], ("score", None, False)),
        (["status"], ("status", None, False)),
    ],
)
def test_argument_parsing(argv, expected):
    args = _parse(argv)
    assert (args.command, args.limit, args.apply) == expected


def test_unknown_command_is_rejected():
    with pytest.raises(SystemExit):
        _parse(["embed-all"])


def test_dispatch_covers_every_command():
    """每个声明的命令都必须在 _dispatch 里有分支。"""
    import inspect

    source = inspect.getsource(_dispatch)
    for command in (
        "ingest", "pipeline", "worker", "score", "embed", "sweep",
        "premarket", "postmarket", "status", "selftest", "article",
    ):
        assert f'"{command}"' in source, f"_dispatch 缺少 {command} 分支"


def test_module_docstring_lists_new_commands():
    """帮助信息要跟上新增命令，否则用户看不到用法。"""
    import fin_news.cli as cli

    assert "cli embed" in (cli.__doc__ or "")
    assert "cli sweep" in (cli.__doc__ or "")
    assert "cli article" in (cli.__doc__ or "")
    assert "--apply" in (cli.__doc__ or "")


def test_main_entrypoint_exists():
    assert callable(main)
