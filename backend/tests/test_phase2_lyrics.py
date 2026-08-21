from app.domain.lyrics.parser import detect_lyrics_format, parse_lyrics


def test_detects_text_lrc_and_kirakara_formats():
    assert detect_lyrics_format("春の歌\n次の行").format == "text"
    assert detect_lyrics_format("[00:12.34]春の歌").format == "lrc"
    assert detect_lyrics_format("[00:12.34]春[00:12.80]の歌").format == "krl"
    assert detect_lyrics_format("[00:12.34]{物語|ものがたり}").format == "krl"


def test_plain_lrc_preserves_line_anchors_and_uses_whole_line_units():
    lyrics = parse_lyrics("[00:10.00]春風\n[00:12.00]青空", media_duration_ms=15_000)
    first, second = lyrics["lines"]
    assert lyrics["source_type"] == "lrc"
    assert first["anchor_ms"] == 10_000
    assert first["start_ms"] == 10_000
    assert first["end_ms"] == 12_000
    assert [unit["surface"] for unit in first["units"]] == ["春風"]
    assert all(unit["timing_source"] == "estimated" for unit in first["units"])
    assert second["end_ms"] == 15_000


def test_plain_text_uses_whole_line_units_until_segmentation():
    lyrics = parse_lyrics("春風が吹く\n青空を見上げる")
    assert [line["units"][0]["surface"] for line in lyrics["lines"]] == [
        "春風が吹く",
        "青空を見上げる",
    ]
    assert all(len(line["units"]) == 1 for line in lyrics["lines"])


def test_krl_preserves_word_times_roles_and_dual_ruby():
    lyrics = parse_lyrics(
        "@Ruby=青空,あおぞら\n[00:01.00]【@Lead+Chorus】{物語|ものがたり>モノガタリ}[00:02.00]青空[00:03.00]",
        filename="song.krl",
    )
    line = lyrics["lines"][0]
    assert line["timing_precision"] == "unit"
    assert line["units"][0]["start_ms"] == 1000
    assert line["units"][0]["ruby"] == "ものがたり"
    assert line["units"][0]["ruby_2"] == "モノガタリ"
    assert line["units"][0]["ruby_span"] == 2
    assert line["units"][0]["roles"] == ["Lead", "Chorus"]
    assert line["units"][2]["ruby"] == "あおぞら"
    assert line["units"][-1]["end_ms"] == 3000


def test_all_imported_entities_have_stable_ids():
    lyrics = parse_lyrics("[00:01.00]ab\n[00:02.00]cd")
    ids = [line["id"] for line in lyrics["lines"]]
    ids += [unit["id"] for line in lyrics["lines"] for unit in line["units"]]
    assert len(ids) == len(set(ids))
    assert all(ids)
