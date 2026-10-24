import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from finbot.db import Warehouse  # noqa: E402
from finbot.generate import generate  # noqa: E402


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("data")
    generate(str(d), seed=7)
    return str(d)


@pytest.fixture(scope="session")
def clean_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("clean")
    generate(str(d), seed=7, anomalies=False, dirty_rows=False)
    return str(d)


@pytest.fixture(scope="session")
def wh(data_dir):
    return Warehouse(data_dir)
