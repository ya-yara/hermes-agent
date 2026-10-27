"""Validate patches.yaml is well-formed YAML with required schema fields."""
import pathlib
import pytest
import yaml


PATCHES_PATH = pathlib.Path(__file__).parents[2] / "overlay" / "patches.yaml"


@pytest.fixture(scope="module")
def manifest():
    return yaml.safe_load(PATCHES_PATH.read_text())


def test_patches_yaml_exists():
    assert PATCHES_PATH.exists(), f"overlay/patches.yaml not found at {PATCHES_PATH}"


def test_patches_yaml_is_valid_yaml(manifest):
    assert isinstance(manifest, dict)


def test_patches_yaml_has_schema_version(manifest):
    assert str(manifest.get("schema_version")) == "1"


def test_patches_yaml_has_groups(manifest):
    assert isinstance(manifest.get("groups"), list)
    assert len(manifest["groups"]) >= 1


def test_each_group_has_required_fields(manifest):
    for group in manifest["groups"]:
        assert "id" in group, f"group missing 'id': {group}"
        assert "intent" in group, f"group {group.get('id')} missing 'intent'"
        assert "custom_files" in group or "custom_patch" in group, \
            f"group {group.get('id')} needs custom_files or custom_patch"


def test_custom_files_are_non_empty_strings(manifest):
    for group in manifest["groups"]:
        files = group.get("custom_files", [])
        for f in files:
            assert isinstance(f, str) and f.strip(), \
                f"group {group.get('id')} has blank/invalid entry in custom_files: {f!r}"
