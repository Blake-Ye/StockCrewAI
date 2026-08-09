from __future__ import annotations

import os
import tempfile

import pytest
from hypothesis import settings


_TEST_FLOW_STORAGE = tempfile.TemporaryDirectory(prefix="stockcrewai-wp00-", dir="/private/tmp")
os.environ.setdefault("CREWAI_STORAGE_DIR", _TEST_FLOW_STORAGE.name)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="显式运行标记为 live 的外部服务测试。",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live: 显式启动的真实外部服务冒烟测试，默认跳过。",
    )
    settings.register_profile(
        "ci",
        max_examples=200,
        derandomize=True,
        deadline=None,
        database=None,
    )
    settings.load_profile("ci")


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    if config.getoption("--run-live"):
        return
    skip_live = pytest.mark.skip(reason="live 测试需显式传入 --run-live。")
    for item in items:
        if item.get_closest_marker("live") is not None:
            item.add_marker(skip_live)
