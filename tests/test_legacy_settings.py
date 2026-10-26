import pytest

from deploy.legacy_settings import read_string_setting


def test_resolves_old_default_and_explicit_environment():
    source = 'SECRET_KEY = os.getenv("SECRET_KEY", "old-default-key")'
    assert read_string_setting(source, "SECRET_KEY", {}) == "old-default-key"
    assert read_string_setting(source, "SECRET_KEY", {"SECRET_KEY": "private-key"}) == "private-key"


def test_preserves_yandex_identity_salt_separately_from_new_signing_key():
    source = 'SECRET_SALT = os.getenv("YANDEX_ID_SALT", "").strip() or SECRET_KEY'
    known = {"SECRET_KEY": "effective-old-signing-key"}
    assert read_string_setting(source, "SECRET_SALT", {}, known) == known["SECRET_KEY"]
    assert read_string_setting(source, "SECRET_SALT", {"YANDEX_ID_SALT": "old-identity-salt"}, known) == "old-identity-salt"


def test_does_not_execute_legacy_source(tmp_path):
    marker = tmp_path / "must-not-be-created"
    source = f'import os\nopen({str(marker)!r}, "w").write("bad")\nSECRET_KEY = os.getenv("SECRET_KEY", "key")'
    assert read_string_setting(source, "SECRET_KEY", {}) == "key"
    assert not marker.exists()
    with pytest.raises(ValueError, match="Unsupported"):
        read_string_setting('SECRET_KEY = dangerous_function()', "SECRET_KEY", {})
