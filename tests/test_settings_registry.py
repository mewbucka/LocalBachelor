"""Settings registry unit tests (roadmap 2.1, architecture sections 3, 12).

Pure — no DB, no HTTP. The typed key registry's validation, the chunk
cross-field rule, and value resolution are unit-tested here; the HTTP wiring
(the 401/422/403 mapping, the DB read of admin defaults, the /settings page)
is covered in ``tests/test_api_settings.py``.
"""

import pytest

from app.core.settings_registry import (
    SETTING_SPECS,
    SettingSpec,
    SettingValidationError,
    check_cross_fields,
    keys_in_section,
    resolve_effective_value,
    validate_payload,
    validate_value,
)

# -- spec shape ------------------------------------------------------------------


def test_registry_covers_expected_keys() -> None:
    expected = {
        "theme",
        "top_k",
        "chat_base_url",
        "chat_model_id",
        "chat_api_key",
        "embedding_base_url",
        "embedding_model_id",
        "embedding_api_key",
        "embedding_dim",
        "embedding_prefix_query",
        "embedding_prefix_passage",
        "search_provider",
        "searxng_url",
        "chunk_target_tokens",
        "chunk_overlap_tokens",
    }
    assert set(SETTING_SPECS) == expected


def test_registry_defaults() -> None:
    assert SETTING_SPECS["theme"].default == "light"
    assert SETTING_SPECS["top_k"].default == 5
    assert SETTING_SPECS["chunk_target_tokens"].default == 400
    assert SETTING_SPECS["chunk_overlap_tokens"].default == 60
    assert SETTING_SPECS["chat_api_key"].default == ""
    assert SETTING_SPECS["search_provider"].default == "duckduckgo"


def test_secret_flags() -> None:
    assert SETTING_SPECS["chat_api_key"].secret
    assert SETTING_SPECS["embedding_api_key"].secret
    for key in (
        "theme",
        "top_k",
        "chat_base_url",
        "embedding_dim",
        "search_provider",
    ):
        assert not SETTING_SPECS[key].secret, key


def test_admin_default_only_keys_are_the_chunk_keys() -> None:
    admin_only = {k for k, s in SETTING_SPECS.items() if s.scope == "admin_default_only"}
    assert admin_only == {"chunk_target_tokens", "chunk_overlap_tokens"}


def test_keys_in_section_orders() -> None:
    assert keys_in_section("ui") == ["theme", "top_k"]
    assert keys_in_section("ingestion") == ["chunk_target_tokens", "chunk_overlap_tokens"]
    assert "chat_api_key" in keys_in_section("chat")


def test_every_spec_is_a_frozen_setting_spec() -> None:
    for spec in SETTING_SPECS.values():
        assert isinstance(spec, SettingSpec)


# -- validate_value --------------------------------------------------------------


def test_validate_enum_accepts_member() -> None:
    assert validate_value(SETTING_SPECS["theme"], "light") == "light"


def test_validate_enum_rejects_non_member() -> None:
    with pytest.raises(SettingValidationError) as exc:
        validate_value(SETTING_SPECS["theme"], "neon")
    assert exc.value.code == "invalid_value"


def test_validate_int_rejects_bool() -> None:
    with pytest.raises(SettingValidationError):
        validate_value(SETTING_SPECS["top_k"], True)


def test_validate_int_rejects_float() -> None:
    with pytest.raises(SettingValidationError):
        validate_value(SETTING_SPECS["top_k"], 5.0)


def test_validate_int_respects_bounds() -> None:
    with pytest.raises(SettingValidationError):
        validate_value(SETTING_SPECS["top_k"], 0)  # below min 1
    with pytest.raises(SettingValidationError):
        validate_value(SETTING_SPECS["top_k"], 21)  # above max 20
    assert validate_value(SETTING_SPECS["top_k"], 5) == 5


def test_validate_str_rejects_non_string() -> None:
    with pytest.raises(SettingValidationError):
        validate_value(SETTING_SPECS["chat_base_url"], 123)


# -- validate_payload ------------------------------------------------------------


def test_validate_payload_unknown_key() -> None:
    with pytest.raises(SettingValidationError) as exc:
        validate_payload({"bogus": 1})
    assert exc.value.code == "unknown_key"
    assert exc.value.key == "bogus"


def test_validate_payload_preserves_order() -> None:
    result = validate_payload({"chat_model_id": "gpt", "theme": "dark", "top_k": 8})
    assert list(result) == ["chat_model_id", "theme", "top_k"]
    assert result == {"chat_model_id": "gpt", "theme": "dark", "top_k": 8}


# -- check_cross_fields ----------------------------------------------------------


def test_cross_fields_ok() -> None:
    check_cross_fields(400, 60)  # overlap < target is fine


def test_cross_fields_equal_rejected() -> None:
    with pytest.raises(SettingValidationError) as exc:
        check_cross_fields(60, 60)
    assert exc.value.code == "cross_field"


def test_cross_fields_overlap_greater_rejected() -> None:
    with pytest.raises(SettingValidationError):
        check_cross_fields(100, 200)


def test_cross_fields_non_int_rejected() -> None:
    with pytest.raises(SettingValidationError):
        check_cross_fields("400", 60)


# -- resolve_effective_value -----------------------------------------------------


def test_resolve_personal_prefers_own() -> None:
    assert resolve_effective_value(SETTING_SPECS["theme"], "dark", "light") == (
        "dark",
        "own",
    )


def test_resolve_personal_falls_back_to_admin() -> None:
    assert resolve_effective_value(SETTING_SPECS["theme"], None, "dark") == (
        "dark",
        "admin_default",
    )


def test_resolve_personal_falls_back_to_code_default() -> None:
    assert resolve_effective_value(SETTING_SPECS["top_k"], None, None) == (
        5,
        "code_default",
    )


def test_resolve_admin_default_ignores_personal_row() -> None:
    spec = SETTING_SPECS["chunk_target_tokens"]
    # a personal row value is never consulted for admin_default_only keys
    assert resolve_effective_value(spec, 999, 1200) == (1200, "admin_default")
    assert resolve_effective_value(spec, 999, None) == (400, "code_default")


def test_resolve_skips_invalid_stored_value() -> None:
    # a hand-edited invalid own value is skipped -> falls to the code default
    assert resolve_effective_value(SETTING_SPECS["theme"], "neon", None) == (
        "light",
        "code_default",
    )
