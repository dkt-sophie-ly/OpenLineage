# Copyright 2018-2023 contributors to the OpenLineage project
# SPDX-License-Identifier: Apache-2.0

import json
import os
import textwrap
from enum import Enum
from unittest import mock

import attr
import pytest
from openlineage.client import set_producer
from openlineage.common.provider.dbt.local import (
    DbtLocalArtifactProcessor,
    LazyJinjaLoadDict,
)
from openlineage.common.provider.dbt.processor import ParentRunMetadata
from openlineage.common.test import match


@pytest.fixture(scope="session", autouse=True)
def setup_producer():
    set_producer("https://github.com/OpenLineage/OpenLineage/tree/0.0.1/integration/dbt")


@pytest.fixture()
def parent_run_metadata():
    return ParentRunMetadata(
        run_id="f99310b4-3c3c-1a1a-2b2b-c1b95c24ff11",
        job_name="dbt-job-name",
        job_namespace="dbt",
    )


def serialize(inst, field, value):
    if isinstance(value, Enum):
        return value.value
    return value


@pytest.mark.parametrize(
    "path",
    [
        "tests/dbt/small",
        "tests/dbt/large",
        "tests/dbt/profiles",
        "tests/dbt/catalog",
        "tests/dbt/fail",
        "tests/dbt/build",
        "tests/dbt/compiled_code",
        "tests/dbt/spark/thrift",
        "tests/dbt/spark/odbc",
        "tests/dbt/postgres",
        "tests/dbt/snapshot",
    ],
)
def test_dbt_parse_and_compare_event(path, parent_run_metadata):
    processor = DbtLocalArtifactProcessor(
        producer="https://github.com/OpenLineage/OpenLineage/tree/0.0.1/integration/dbt",
        job_namespace="job-namespace",
        project_dir=path,
    )
    processor.dbt_run_metadata = parent_run_metadata
    dbt_events = processor.parse()
    events = [
        attr.asdict(event, value_serializer=serialize)
        for event in dbt_events.starts + dbt_events.completes + dbt_events.fails
    ]
    with open(f"{path}/result.json") as f:
        assert match(json.load(f), events)


@mock.patch("uuid.uuid4")
@mock.patch("datetime.datetime")
def test_dbt_parse_dbt_test_event(mock_datetime, mock_uuid, parent_run_metadata):
    mock_datetime.now.return_value.isoformat.return_value = "2021-08-25T11:00:25.277467+00:00"
    mock_uuid.side_effect = [
        "6edf42ed-d8d0-454a-b819-d09b9067ff99",
        "1a69c0a7-04bb-408b-980e-cbbfb1831ef7",
        "f99310b4-339a-4381-ad3e-c1b95c24ff11",
        "c11f2efd-4415-45fc-8081-10d2aaa594d2",
    ]

    processor = DbtLocalArtifactProcessor(
        producer="https://github.com/OpenLineage/OpenLineage/tree/0.0.1/integration/dbt",
        job_namespace="dbt-test-namespace",
        project_dir="tests/dbt/test",
    )
    processor.dbt_run_metadata = parent_run_metadata

    dbt_events = processor.parse()
    events = [
        attr.asdict(event, value_serializer=serialize)
        for event in dbt_events.starts + dbt_events.completes + dbt_events.fails
    ]
    with open("tests/dbt/test/result.json") as f:
        assert match(json.load(f), events)


@mock.patch("uuid.uuid4")
@mock.patch.dict(
    os.environ,
    {
        "HOST": "foo_host",
        "PORT": "1111",
        "DB_NAME": "foo_db_name",
        "USER_NAME": "foo_user",
        "PASSWORD": "foo_password",
        "SCHEMA": "foo_schema",
    },
)
def test_dbt_parse_profile_with_env_vars(mock_uuid, parent_run_metadata):
    mock_uuid.side_effect = [
        "6edf42ed-d8d0-454a-b819-d09b9067ff99",
    ]

    processor = DbtLocalArtifactProcessor(
        producer="https://github.com/OpenLineage/OpenLineage/tree/0.0.1/integration/dbt",
        project_dir="tests/dbt/env_vars",
        target="prod",
        job_namespace="ol-namespace",
    )
    processor.dbt_run_metadata = parent_run_metadata

    dbt_events = processor.parse()
    events = [
        attr.asdict(event, value_serializer=serialize)
        for event in dbt_events.starts + dbt_events.completes + dbt_events.fails
    ]
    with open("tests/dbt/env_vars/result.json") as f:
        assert match(json.load(f), events)


@pytest.fixture()
def jinja_env():
    env = DbtLocalArtifactProcessor.setup_jinja()
    env.globals.update({"test": "test_variable", "method": lambda: "test_method"})
    return env


def test_jinja_undefined_variable(jinja_env):
    text = "{{ variable }}"
    assert text == LazyJinjaLoadDict.render_values_jinja(jinja_env, text)


def test_jinja_undefined_method(jinja_env):
    text = "{{ undefined_method() }}"
    assert text == LazyJinjaLoadDict.render_values_jinja(jinja_env, text)


def test_jinja_defined_method(jinja_env):
    os.environ["PORT_REDSHIFT"] = "13"
    text = "{{ env_var('PORT_REDSHIFT') | as_number }}"
    assert LazyJinjaLoadDict.render_values_jinja(jinja_env, text) == "13"
    del os.environ["PORT_REDSHIFT"]


def test_jinja_defined_variable(jinja_env):
    text = "{{ test }}"
    assert LazyJinjaLoadDict.render_values_jinja(jinja_env, text) == "test_variable"


def test_jinja_undefined_method_with_args(jinja_env):
    text = "# {{ does_not_exist(some_arg.subarg.subarg2) }}"
    assert text == LazyJinjaLoadDict.render_values_jinja(jinja_env, text)


def test_jinja_multiline(jinja_env):
    os.environ["PORT_REDSHIFT"] = "13"

    text = textwrap.dedent(
        """
    # {{ does_not_exist(some_arg.subarg.subarg2) }}
    {{ env_var('PORT_REDSHIFT') | as_number }}
    {{ undefined }}
    more_text
    even_more_text"""
    )

    parsed = LazyJinjaLoadDict.render_values_jinja(jinja_env, text)

    assert parsed == textwrap.dedent(
        """
    # {{ does_not_exist(some_arg.subarg.subarg2) }}
    13
    {{ undefined }}
    more_text
    even_more_text"""
    )

    del os.environ["PORT_REDSHIFT"]


def test_lazy_load_jinja_dict(jinja_env):
    dictionary = {"key": "{{ test }}"}
    assert {"key": "{{ test }}"} == LazyJinjaLoadDict.render_values_jinja(jinja_env, dictionary)

    assert LazyJinjaLoadDict.render_values_jinja(jinja_env, dictionary)["key"] == "test_variable"


def test_jinja_list(jinja_env):
    test_list = ["key", "{{ test }}"]
    assert ["key", "test_variable"] == LazyJinjaLoadDict.render_values_jinja(jinja_env, test_list)


def test_logging_handler_warns():
    path = "tests/dbt/test/target/manifest.json"
    logger = mock.Mock()
    DbtLocalArtifactProcessor.load_metadata(path, [1], logger)

    logger.warning.assert_called_once_with(
        "Artifact schema version: https://schemas.getdbt.com/dbt/manifest/v2.json is above "
        "dbt-ol supported version 1. This might cause errors."
    )


def test_logging_handler_does_not_warn():
    path = "tests/dbt/test/target/manifest.json"
    logger = mock.Mock()
    DbtLocalArtifactProcessor.load_metadata(path, [2], logger)

    logger.warning.assert_not_called()


@mock.patch.dict(os.environ, {"DBT_TARGET_PATH": "target-from-envvar"}, clear=True)
def test_build_target_path_with_user_defined():
    processor = DbtLocalArtifactProcessor(
        producer="https://github.com/OpenLineage/OpenLineage/tree/0.0.1/integration/dbt",
        project_dir="tests/dbt/env_vars",
        target="prod",
        target_path="arg-target-name",
        job_namespace="ol-namespace",
    )
    assert processor.build_target_path({}) == "arg-target-name"


@mock.patch.dict(os.environ, {"DBT_TARGET_PATH": "target-from-envvar"}, clear=True)
def test_build_target_path_with_envvar():
    processor = DbtLocalArtifactProcessor(
        producer="https://github.com/OpenLineage/OpenLineage/tree/0.0.1/integration/dbt",
        project_dir="tests/dbt/env_vars",
        target="prod",
        job_namespace="ol-namespace",
    )
    assert processor.build_target_path({}) == "target-from-envvar"


@pytest.mark.parametrize(
    "test_name,dbt_project,expected",
    [
        ("with_dbt_project", {"target-path": "from-dbt-project"}, "from-dbt-project"),
        ("with_default", {}, "target"),
    ],
)
def test_build_target_path(test_name, dbt_project, expected):
    processor = DbtLocalArtifactProcessor(
        producer="https://github.com/OpenLineage/OpenLineage/tree/0.0.1/integration/dbt",
        project_dir="tests/dbt/env_vars",
        target="prod",
        job_namespace="ol-namespace",
    )
    assert processor.build_target_path(dbt_project) == expected
