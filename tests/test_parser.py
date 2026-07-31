from cliradar.parser import ParserProfile, parse_context_help

# Some platforms name commands `error` and list options with no
# description. Both readings are unsafe elsewhere, so they are opt-in and
# every test below states which one it is exercising.
PERMISSIVE = ParserProfile(
    accept_undescribed_options=True,
    error_words_are_commands=True,
)


def test_parses_cisco_style_context_help() -> None:
    output = """
switch# show ?
  interfaces      Interface status and configuration
  ip              IP information
  version         System hardware and software status
switch# show
"""
    options = parse_context_help(output, "show ")

    assert [(item.token, item.kind) for item in options] == [
        ("interfaces", "keyword"),
        ("ip", "keyword"),
        ("version", "keyword"),
    ]
    assert options[0].description == "Interface status and configuration"


def test_marks_parameters_and_cr() -> None:
    output = """
  <1-4094>        VLAN identifier
  WORD            Interface name
  <cr>            Execute command
"""
    options = parse_context_help(output)

    assert [item.kind for item in options] == ["parameter", "parameter", "cr"]


def test_ignores_device_error_messages() -> None:
    output = """
% Invalid input detected at '^' marker.
Error: unknown command
"""

    assert parse_context_help(output) == []


def test_parses_junos_style_headers_and_enter_marker() -> None:
    output = """
user@router> show ?
Possible completions:
  <[Enter]>
  interfaces          Show interface information
  route               Show routing table
"""

    options = parse_context_help(output, "show ")

    assert [(item.token, item.kind) for item in options] == [
        ("<[Enter]>", "cr"),
        ("interfaces", "keyword"),
        ("route", "keyword"),
    ]


def test_ignores_pagination_and_prompt_echo() -> None:
    output = """
switch# show ?
  version             Display version
---- More ( Press 'Q' to break ) ----
switch#
"""

    options = parse_context_help(output, "show ")

    assert [item.token for item in options] == ["version"]


def test_keeps_a_command_actually_named_error() -> None:
    """`error` is a real token on some CLIs, not an error message."""
    output = """
   reset stg 
  error   Error Packet
switch#reset stg 
"""

    assert [i.token for i in parse_context_help(output, "reset stg ", PERMISSIVE)] == [
        "error"
    ]
    # The strict default reads the same line as the device refusing the query.
    assert parse_context_help(output, "reset stg ") == []


def test_keeps_hyphenated_tokens_that_start_like_an_error_word() -> None:
    output = """
  error-down           Error-down
  unknown-unicast      Unknown unicast address
  unknown-multicast    Unknown multicast address
  invalid-key          Invalid key handling
"""

    assert parse_context_help(output) == []

    tokens = [item.token for item in parse_context_help(output, '', PERMISSIVE)]

    assert tokens == [
        "error-down",
        "unknown-unicast",
        "unknown-multicast",
        "invalid-key",
    ]


def test_keeps_options_the_device_listed_without_a_description() -> None:
    output = """
  no debug udld 
  all
  event
  sync
  <cr>
"""

    options = parse_context_help(output, "no debug udld ", PERMISSIVE)

    assert [i.token for i in options] == ["all", "event", "sync", "<cr>"]
    # Without the profile a lone indented word stays untrusted.
    strict = parse_context_help(output, "no debug udld ")
    assert [i.token for i in strict] == ["<cr>"]


def test_keeps_an_option_whose_description_ends_like_a_prompt() -> None:
    output = """
  COMMUNITYSTR          The string aa<0-65535>:nn<0-65535>
  <cr>  
switch(config)#ip community-filter 1 deny 
"""

    options = parse_context_help(output, "ip community-filter 1 deny ")

    assert [(item.token, item.kind) for item in options] == [
        ("COMMUNITYSTR", "parameter"),
        ("<cr>", "cr"),
    ]


def test_wrapped_description_text_does_not_become_a_command() -> None:
    output = """
  remote-id   Configure the remote id, which supports iftype, mac, slot.
              Please refer to the user command manual for more information.
  <cr>
"""

    assert [item.token for item in parse_context_help(output)] == ["remote-id", "<cr>"]


def test_strict_default_does_not_invent_a_command_from_a_status_line() -> None:
    """The secure default: a lone indented word is not evidence of a command."""
    output = """
  Initializing
  Incomplete
"""

    assert parse_context_help(output) == []
    assert [i.token for i in parse_context_help(output, "", PERMISSIVE)] == [
        "Initializing",
        "Incomplete",
    ]
