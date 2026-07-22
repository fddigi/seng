from __future__ import annotations

from scraper_core.matching import build_synonym_lookup, expand_synonyms, normalize_model_number


def test_normalize_model_number_strips_glued_generation_suffix():
    """Regression test: PA SPEAKERS' real-world failure - a search term
    "RCF ART 710" never matched the listing title "RCF ART-710A-MK5"."""
    assert normalize_model_number("RCF ART-710A-MK5") == "RCF ART 710"
    assert normalize_model_number("RCF ART 710") == "RCF ART 710"


def test_normalize_model_number_strips_suffix_with_no_separator_at_all():
    """Regression test: PA SPEAKERS' second real-world failure - no word
    boundary whatsoever between the model number and its generation marker."""
    assert normalize_model_number("Yamaha DXR8MKII") == "YAMAHA DXR8"
    assert normalize_model_number("Yamaha DXR8") == "YAMAHA DXR8"


def test_normalize_model_number_leaves_plain_model_numbers_untouched():
    assert normalize_model_number("RCF SUB 705") == "RCF SUB 705"


def test_normalize_model_number_is_case_insensitive():
    assert normalize_model_number("rcf art-710a-mk5") == "RCF ART 710"


def test_build_synonym_lookup_groups_cross_language_words():
    """Regression test: PLAGG's real-world failure - "kuling jakke" (Danish)
    never matched "Kuling jacka" (Swedish) or "kurtka" (Polish) via plain
    substring matching, because they share no literal substring."""
    clusters = [{"jakke", "jacka", "kurtka"}, {"bukser", "spodnie"}]
    lookup = build_synonym_lookup(clusters)

    assert expand_synonyms("jakke", lookup) == frozenset({"jakke", "jacka", "kurtka"})
    assert expand_synonyms("kurtka", lookup) == expand_synonyms("jakke", lookup)


def test_expand_synonyms_untracked_word_returns_itself_only():
    lookup = build_synonym_lookup([{"jakke", "jacka"}])
    assert expand_synonyms("sko", lookup) == frozenset({"sko"})


def test_build_synonym_lookup_is_case_insensitive():
    lookup = build_synonym_lookup([{"Jakke", "JACKA"}])
    assert expand_synonyms("jakke", lookup) == frozenset({"jakke", "jacka"})
