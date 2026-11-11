"""Unit tests for serial-port auto-detection."""

from psu_top.detect import looks_like_psu, parse_choice, prompt_choice, select_port

TWO = [("/dev/ttyUSB0", "KIPRIM,DC310S"), ("/dev/ttyUSB1", "KIPRIM,DC605Pro")]


def test_kiprim_idn_matches():
    assert looks_like_psu("KIPRIM,DC310S,25012662,FV:V5.2.0") is True


def test_owon_idn_matches():
    assert looks_like_psu("OWON,SPE3103,123,FV:1.0") is True


def test_match_is_case_insensitive():
    assert looks_like_psu("kiprim,dc310s") is True


def test_unrelated_device_does_not_match():
    assert looks_like_psu("Arduino Uno") is False


def test_empty_idn_does_not_match():
    assert looks_like_psu("") is False


def test_select_single_match_returns_its_port():
    port, error = select_port([("/dev/ttyUSB1", "KIPRIM,DC310S")], ["/dev/ttyUSB0", "/dev/ttyUSB1"])
    assert port == "/dev/ttyUSB1"
    assert error is None


def test_select_no_match_lists_probed_ports():
    port, error = select_port([], ["/dev/ttyUSB0", "/dev/ttyACM0"])
    assert port is None
    assert "/dev/ttyUSB0" in error and "/dev/ttyACM0" in error
    assert "--port" in error


def test_select_no_ports_at_all_explains_none_found():
    port, error = select_port([], [])
    assert port is None
    assert "no serial ports" in error.lower()


def test_select_multiple_matches_errors_and_lists_them():
    matches = [("/dev/ttyUSB0", "KIPRIM,DC310S"), ("/dev/ttyUSB1", "OWON,SPE3103")]
    port, error = select_port(matches, ["/dev/ttyUSB0", "/dev/ttyUSB1"])
    assert port is None
    assert "/dev/ttyUSB0" in error and "/dev/ttyUSB1" in error
    assert "KIPRIM,DC310S" in error and "OWON,SPE3103" in error
    assert "--port" in error


def test_parse_choice_maps_one_based_to_index():
    assert parse_choice("1", 2) == 0
    assert parse_choice("2", 2) == 1


def test_parse_choice_strips_whitespace():
    assert parse_choice("  2 ", 2) == 1


def test_parse_choice_rejects_out_of_range():
    assert parse_choice("0", 2) is None
    assert parse_choice("3", 2) is None


def test_parse_choice_rejects_non_numeric():
    assert parse_choice("q", 2) is None
    assert parse_choice("", 2) is None
    assert parse_choice("1a", 2) is None


def test_prompt_choice_returns_selected_device():
    answers = iter(["2"])
    assert prompt_choice(TWO, input_fn=lambda _: next(answers)) == "/dev/ttyUSB1"


def test_prompt_choice_reprompts_until_valid():
    answers = iter(["x", "9", "1"])
    assert prompt_choice(TWO, input_fn=lambda _: next(answers)) == "/dev/ttyUSB0"


def test_prompt_choice_quit_returns_none():
    assert prompt_choice(TWO, input_fn=lambda _: "q") is None


def test_prompt_choice_eof_returns_none():
    def raise_eof(_):
        raise EOFError

    assert prompt_choice(TWO, input_fn=raise_eof) is None
