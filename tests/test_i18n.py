"""Testy lokalizace."""
import pytest

from app import i18n


def test_all_languages_have_same_keys():
    base = set(i18n.TRANSLATIONS["cs"])
    for lang, table in i18n.TRANSLATIONS.items():
        assert set(table) == base, f"jazyk {lang} má jiné klíče"


def test_languages_listed_match_translations():
    assert set(i18n.LANGUAGES) == set(i18n.TRANSLATIONS)


@pytest.mark.parametrize("query,cookie,header,expected", [
    ("de", "fr", "en-US", "de"),                 # ?lang= má přednost
    (None, "fr", "en-US,en;q=0.9", "fr"),        # pak cookie
    (None, None, "de-DE,de;q=0.9,en;q=0.8", "de"),  # pak hlavička
    (None, None, "es-ES,es;q=0.9", "cs"),        # nepodporovaný → výchozí
    ("xx", None, None, "cs"),                    # neplatný kód → výchozí
    (None, None, None, "cs"),
])
def test_resolve_lang(query, cookie, header, expected):
    assert i18n.resolve_lang(query, cookie, header) == expected


def test_translations_fall_back_to_default():
    t = i18n.translations("fr")
    assert t["card_clubs"] == "Radio-clubs"
    assert set(t) == set(i18n.TRANSLATIONS["cs"])  # žádný klíč nechybí


def test_placeholders_preserved_in_all_languages():
    for lang, table in i18n.TRANSLATIONS.items():
        assert "{days}" in table["panel_expiring"], lang
        assert "{count}" in table["panel_expiring"], lang
        assert "{count}" in table["panel_club"], lang
        assert "{days}" in table["res_in_days"], lang
