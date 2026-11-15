"""Typed settings key registry (architecture sections 3, 6, 12; roadmap 2.1).

The single source of truth for every settings key the app understands: its
value type, default, validation bounds, scope (personal vs admin-default-only),
UI section, and whether it is a secret. Tasks 2.2-2.6 build on this registry —
the settings page, the masked provider config, the provider adapters, and the
editable settings UI all read the key definitions from here, so a key is
declared once and every surface picks it up.

Keys registered (roadmap 2.1):

- ``theme`` (enum light|dark, default light) — personal, UI section. This is
  the task-1.4 theme-switcher key; its behavior is unchanged (the resolution
  chain in ``app/core/theme.py`` still reads the personal value, then the
  admin-default value, then ``light``).
- ``top_k`` (int 1..20, default 5) — personal, UI section.
- ``chunk_target_tokens`` / ``chunk_overlap_tokens`` (int; defaults 400 / 60)
  — admin-default-only, Ingestion section; the overlap must stay strictly
  below the target (a cross-field rule enforced on write).
- the personal provider config (chat / embedding / search), each with an
  admin-default fallback; ``chat_api_key`` and ``embedding_api_key`` are
  secrets (masked in every read surface; stored encrypted from task 2.2).

Validation here is pure (no DB): :func:`validate_payload` type-checks every
provided key against its spec and rejects unknown keys, out-of-bounds values,
and the chunk overlap<target relationship with a :class:`SettingValidationError`
the API layer translates into a localized 422. The DB read of current
admin-default values (needed to evaluate the cross-field rule against a partial
payload) is the API layer's job — it has the session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SettingType = Literal["enum", "int", "str"]
SettingScope = Literal["personal", "admin_default_only"]
SettingSection = Literal["chat", "embeddings", "search", "ingestion", "ui"]

#: The placeholder a set secret's value is replaced with in every read surface.
MASKED = "***"

#: The section order the settings page renders sections in.
SECTION_ORDER: tuple[str, ...] = ("chat", "embeddings", "search", "ingestion", "ui")


@dataclass(frozen=True)
class SettingSpec:
    """The definition of one settings key.

    ``type`` drives value validation (``enum`` -> member of ``enum_values``;
    ``int`` -> integer within ``min``/``max``; ``str`` -> any string).
    ``scope`` drives where a write lands and how a value resolves:
    ``personal`` resolves own -> admin-default -> code-default, while
    ``admin_default_only`` resolves admin-default -> code-default and is
    writable only by admins.
    """

    name: str
    type: SettingType
    default: object
    scope: SettingScope
    section: SettingSection
    secret: bool = False
    #: Allowed values, ``type == "enum"`` only.
    enum_values: tuple[str, ...] = ()
    #: Inclusive lower bound, ``type == "int"`` only.
    min: int | None = None
    #: Inclusive upper bound, ``type == "int"`` only.
    max: int | None = None


class SettingValidationError(Exception):
    """A settings payload failed registry validation.

    ``code`` is a stable machine-readable reason the API layer maps to a
    localized 422 message: ``unknown_key`` (not a registered key),
    ``invalid_value`` (wrong type or out of bounds), ``cross_field`` (the
    chunk overlap must stay below the target).
    """

    def __init__(self, key: str, code: str) -> None:
        super().__init__(f"{key}: {code}")
        self.key = key
        self.code = code


SETTING_SPECS: dict[str, SettingSpec] = {
    # --- UI (personal) ---
    "theme": SettingSpec(
        name="theme",
        type="enum",
        default="light",
        scope="personal",
        section="ui",
        enum_values=("light", "dark"),
    ),
    "top_k": SettingSpec(
        name="top_k",
        type="int",
        default=5,
        scope="personal",
        section="ui",
        min=1,
        max=20,
    ),
    # --- Chat provider (personal, admin-default fallback) ---
    "chat_base_url": SettingSpec(
        name="chat_base_url", type="str", default="", scope="personal", section="chat"
    ),
    "chat_model_id": SettingSpec(
        name="chat_model_id", type="str", default="", scope="personal", section="chat"
    ),
    "chat_api_key": SettingSpec(
        name="chat_api_key",
        type="str",
        default="",
        scope="personal",
        section="chat",
        secret=True,
    ),
    # --- Embedding provider (personal, admin-default fallback) ---
    "embedding_base_url": SettingSpec(
        name="embedding_base_url",
        type="str",
        default="",
        scope="personal",
        section="embeddings",
    ),
    "embedding_model_id": SettingSpec(
        name="embedding_model_id",
        type="str",
        default="",
        scope="personal",
        section="embeddings",
    ),
    "embedding_api_key": SettingSpec(
        name="embedding_api_key",
        type="str",
        default="",
        scope="personal",
        section="embeddings",
        secret=True,
    ),
    "embedding_dim": SettingSpec(
        name="embedding_dim",
        type="int",
        default=0,
        scope="personal",
        section="embeddings",
        min=0,
    ),
    "embedding_prefix_query": SettingSpec(
        name="embedding_prefix_query",
        type="str",
        default="",
        scope="personal",
        section="embeddings",
    ),
    "embedding_prefix_passage": SettingSpec(
        name="embedding_prefix_passage",
        type="str",
        default="",
        scope="personal",
        section="embeddings",
    ),
    # --- Search (personal) ---
    "search_provider": SettingSpec(
        name="search_provider",
        type="enum",
        default="duckduckgo",
        scope="personal",
        section="search",
        enum_values=("searxng", "duckduckgo"),
    ),
    "searxng_url": SettingSpec(
        name="searxng_url", type="str", default="", scope="personal", section="search"
    ),
    # --- Ingestion (admin-default-only; shared chunker config) ---
    "chunk_target_tokens": SettingSpec(
        name="chunk_target_tokens",
        type="int",
        default=400,
        scope="admin_default_only",
        section="ingestion",
        min=100,
        max=2000,
    ),
    "chunk_overlap_tokens": SettingSpec(
        name="chunk_overlap_tokens",
        type="int",
        default=60,
        scope="admin_default_only",
        section="ingestion",
        min=0,
        max=500,
    ),
}


def keys_in_section(section: str) -> list[str]:
    """The registered key names whose spec belongs to ``section``, in registry order."""
    return [key for key, spec in SETTING_SPECS.items() if spec.section == section]


def validate_value(spec: SettingSpec, raw: object) -> object:
    """Validate one value against its spec; return it, or raise.

    ``enum`` must be a member of ``enum_values``; ``int`` must be a real int
    (bools are rejected — Python's ``bool`` is an ``int`` subclass) within the
    inclusive ``min``/``max``; ``str`` must be a string.
    """
    if spec.type == "enum":
        if isinstance(raw, str) and raw in spec.enum_values:
            return raw
        raise SettingValidationError(spec.name, "invalid_value")
    if spec.type == "int":
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise SettingValidationError(spec.name, "invalid_value")
        if spec.min is not None and raw < spec.min:
            raise SettingValidationError(spec.name, "invalid_value")
        if spec.max is not None and raw > spec.max:
            raise SettingValidationError(spec.name, "invalid_value")
        return raw
    # type == "str"
    if not isinstance(raw, str):
        raise SettingValidationError(spec.name, "invalid_value")
    return raw


def validate_payload(payload: dict[str, object]) -> dict[str, object]:
    """Validate every provided key; return the validated mapping or raise.

    Raises :class:`SettingValidationError` with code ``unknown_key`` for a key
    not in the registry, or ``invalid_value`` for a bad type / out of bounds.
    The returned mapping preserves the caller's key order.
    """
    validated: dict[str, object] = {}
    for key, raw in payload.items():
        spec = SETTING_SPECS.get(key)
        if spec is None:
            raise SettingValidationError(key, "unknown_key")
        validated[key] = validate_value(spec, raw)
    return validated


def check_cross_fields(target: object, overlap: object) -> None:
    """Enforce the chunker relationship: overlap must stay below target.

    Both arguments are the *effective* values (already individually validated,
    so both are ints in practice). Raises :class:`SettingValidationError` with
    code ``cross_field`` when ``overlap >= target`` (or a value is not an int,
    which can only happen if a caller bypassed :func:`validate_value`).
    """
    if not isinstance(target, int) or not isinstance(overlap, int):
        raise SettingValidationError("chunk_overlap_tokens", "cross_field")
    if overlap >= target:
        raise SettingValidationError("chunk_overlap_tokens", "cross_field")


def resolve_effective_value(
    spec: SettingSpec, own: object | None, admin_default: object | None
) -> tuple[object, str]:
    """Resolve a key's effective value and its source badge.

    ``personal`` resolves own -> admin-default -> code-default;
    ``admin_default_only`` resolves admin-default -> code-default (a personal
    row is never consulted for these). A stored value that no longer validates
    (e.g. data edited by hand) is skipped, so resolution falls through to the
    next candidate and never surfaces an invalid value. Returns
    ``(value, source)`` where ``source`` is ``"own"``, ``"admin_default"``, or
    ``"code_default"``.
    """
    candidates: list[tuple[str, object | None]] = []
    if spec.scope == "personal":
        candidates.append(("own", own))
    candidates.append(("admin_default", admin_default))
    for source, raw in candidates:
        if raw is None:
            continue
        try:
            return validate_value(spec, raw), source
        except SettingValidationError:
            continue
    return spec.default, "code_default"
