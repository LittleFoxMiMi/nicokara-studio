from app.services.japanese_phoneme import PhonemeToken, split_phonemes_at_segment_starts


def test_split_phonemes_uses_only_whisper_segment_starts() -> None:
    transcript = {
        "segments": [
            {"start_ms": 1000, "end_ms": 1500},
            {"start_ms": 3000, "end_ms": 3200},
        ]
    }
    tokens = [
        PhonemeToken("a", 900, 940),
        PhonemeToken("i", 1200, 1240),
        PhonemeToken("u", 2000, 2040),
        PhonemeToken("e", 3100, 3140),
        PhonemeToken("o", 5000, 5040),
    ]

    split_phonemes_at_segment_starts(transcript, tokens)

    assert transcript["segments"][0]["phonemes"] == "i u"
    assert transcript["segments"][1]["phonemes"] == "e o"
    assert transcript["phoneme_segmentation"] == "whisper_segment_start_boundaries"


def test_split_phonemes_handles_empty_and_unsorted_tokens() -> None:
    transcript = {"segments": [{"start_ms": 0}, {"start_ms": 2000}, {"start_ms": 4000}]}
    tokens = [PhonemeToken("u", 4500, 4520), PhonemeToken("a", 1000, 1020)]

    split_phonemes_at_segment_starts(transcript, tokens)

    assert transcript["segments"][0]["phonemes"] == "a"
    assert transcript["segments"][1]["phonemes"] == ""
    assert transcript["segments"][2]["phonemes"] == "u"
