from renforge.util import files


def test_win_unicode_string_assigns_a_compatible_buffer_pointer() -> None:
    value, buffer = files._win_unicode_string("activity.jsonl")

    assert value.Buffer == "activity.jsonl"
    assert buffer.value == "activity.jsonl"
