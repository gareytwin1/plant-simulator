import pytest

from app.configversion import (
    CONFIG_VERSION_PATH,
    ConfigVersion,
    ConfigVersionError,
    read_config_version,
)


def test_parse_reads_three_integers():
    assert ConfigVersion.parse("2.10.3\n") == ConfigVersion(2, 10, 3)


@pytest.mark.parametrize(
    "text",
    ["", "1", "1.0", "1.0.0.0", "v1.0.0", "1.0.0-rc1", "1.0.x", "-1.0.0", "01.0.0", "1. 0.0"],
)
def test_parse_rejects_anything_but_major_minor_patch(text):
    with pytest.raises(ConfigVersionError):
        ConfigVersion.parse(text)


def test_str_round_trips_through_parse():
    version = ConfigVersion(1, 2, 3)

    assert str(version) == "1.2.3"
    assert ConfigVersion.parse(str(version)) == version


def test_comparable_with_means_the_same_major():
    base = ConfigVersion(1, 0, 0)

    assert base.comparable_with(ConfigVersion(1, 9, 4))
    assert not base.comparable_with(ConfigVersion(2, 0, 0))


def test_versions_order_numerically_not_textually():
    assert ConfigVersion(1, 10, 0) > ConfigVersion(1, 9, 0)


def test_read_config_version_reads_the_repository_file():
    assert read_config_version() == ConfigVersion.parse(CONFIG_VERSION_PATH.read_text())


def test_read_config_version_reports_a_missing_file(tmp_path):
    with pytest.raises(ConfigVersionError, match="cannot read"):
        read_config_version(tmp_path / "VERSION")


def test_read_config_version_reports_a_malformed_file(tmp_path):
    path = tmp_path / "VERSION"
    path.write_text("one point oh\n")

    with pytest.raises(ConfigVersionError, match="MAJOR.MINOR.PATCH"):
        read_config_version(path)
