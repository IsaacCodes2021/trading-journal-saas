"""Choosing the bucket: the settings that turn it on (api/config.py), the store the app builds, and the sweep job's choice."""

from pathlib import Path

import pytest

from tradejournal.api.app import _store_for
from tradejournal.api.config import ApiConfig, ConfigError
from tradejournal.bucketstore import BucketFileStore, BucketSettings
from tradejournal.filestore import LocalFileStore
from tradejournal.pg import sweep

KEY = "test" + "accesskey0123456789"
SECRET = "test" + "secret/with+odd=chars"  # noqa: S105 - made up
S3 = {
    "TJ_S3_ENDPOINT": "https://0123456789abcdef.r2.cloudflarestorage.com",
    "TJ_S3_BUCKET": "roundturn-files",
    "TJ_S3_ACCESS_KEY_ID": KEY,
    "TJ_S3_SECRET_ACCESS_KEY": SECRET,
}
PRODUCTION = {"TJ_ENV": "production", "TJ_APP_ORIGIN": "https://app.example.com", "TJ_SESSION_SECRET": "ab" * 32}


def test_the_four_settings_give_the_config_a_bucket():
    config = ApiConfig.from_env(S3)
    assert config.bucket == BucketSettings(S3["TJ_S3_ENDPOINT"], "roundturn-files", KEY, SECRET)
    assert config.files_dir is None


def test_with_none_of_them_there_is_no_bucket():
    assert ApiConfig.from_env({}).bucket is None


def test_a_missing_setting_is_refused_by_name_without_the_values():
    env = {k: v for k, v in S3.items() if k != "TJ_S3_BUCKET"}
    with pytest.raises(ConfigError, match="go together") as caught:
        ApiConfig.from_env(env)
    assert SECRET not in str(caught.value) and KEY not in str(caught.value)


@pytest.mark.parametrize(
    ("over", "words"),
    [
        ({"TJ_S3_ENDPOINT": "https://x.example/bucket"}, "TJ_S3_ENDPOINT"),
        ({"TJ_S3_BUCKET": "Not A Bucket"}, "TJ_S3_BUCKET"),
        ({"TJ_S3_REGION": "US EAST"}, "TJ_S3_REGION"),
        ({"TJ_S3_SECRET_ACCESS_KEY": "two words"}, "TJ_S3_SECRET_ACCESS_KEY"),
    ],
)
def test_a_setting_that_is_not_what_it_names_is_a_config_error_that_names_it(over, words):
    with pytest.raises(ConfigError, match=words) as caught:
        ApiConfig.from_env(S3 | over)
    assert SECRET not in str(caught.value)


def test_a_folder_and_a_bucket_together_are_refused():
    with pytest.raises(ConfigError, match="choose one"):
        ApiConfig.from_env(S3 | {"TJ_FILES_DIR": "/var/data/files"})


def test_production_needs_an_https_endpoint_and_development_does_not():
    plain = S3 | {"TJ_S3_ENDPOINT": "http://127.0.0.1:9000"}
    with pytest.raises(ConfigError, match="https in production"):
        ApiConfig.from_env(PRODUCTION | plain)
    assert ApiConfig.from_env(plain).bucket is not None
    assert ApiConfig.from_env(PRODUCTION | S3).bucket is not None


def test_the_secret_is_not_in_the_text_of_the_config():
    config = ApiConfig.from_env(S3)
    assert SECRET not in repr(config) and SECRET not in str(config)


def test_the_app_builds_the_store_the_config_names(tmp_path):
    assert isinstance(_store_for(ApiConfig.from_env(S3)), BucketFileStore)
    assert isinstance(_store_for(ApiConfig(files_dir=tmp_path / "files")), LocalFileStore)
    assert _store_for(ApiConfig()) is None


def test_the_sweep_job_keeps_files_in_the_same_place_as_the_web_server(monkeypatch, tmp_path):
    for name in (*S3, "TJ_S3_REGION", "TJ_FILES_DIR"):
        monkeypatch.delenv(name, raising=False)
    assert sweep._files_from_env() is None
    monkeypatch.setenv("TJ_FILES_DIR", str(tmp_path / "files"))
    assert isinstance(sweep._files_from_env(), LocalFileStore)
    monkeypatch.delenv("TJ_FILES_DIR")
    for name, value in S3.items():
        monkeypatch.setenv(name, value)
    assert isinstance(sweep._files_from_env(), BucketFileStore)
    monkeypatch.setenv("TJ_FILES_DIR", str(Path(tmp_path) / "files"))
    with pytest.raises(ValueError, match="choose one"):
        sweep._files_from_env()


def test_a_half_set_bucket_stops_the_sweep_job_before_it_deletes_anything(monkeypatch):
    for name in (*S3, "TJ_S3_REGION", "TJ_FILES_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TJ_S3_BUCKET", "roundturn-files")
    with pytest.raises(ValueError, match="go together"):
        sweep._files_from_env()
