from utils import B, KB, chunk, esc, fmt_curr, fmt_short, normalize_support, pager_row, short


def test_html_is_escaped_and_none_is_safe():
    assert esc("<Nexora & friends>") == "&lt;Nexora &amp; friends&gt;"
    assert esc(None) == ""


def test_currency_formatters_handle_zero_and_decimals():
    assert fmt_curr(5) == "₹5.00"
    assert fmt_curr(None) == "₹0.00"
    assert fmt_short(5.5) == "₹5.5"


def test_short_keeps_short_text_and_truncates_long_text():
    assert short("Nexora", 10) == "Nexora"
    assert short("Nexora service menu", 8) == "Nexora…"


def test_keyboard_builder_skips_empty_rows():
    markup = KB([B("Home", "back_main")], [], [B("Help", "help")])
    assert len(markup.inline_keyboard) == 2
    assert markup.inline_keyboard[0][0].callback_data == "back_main"


def test_chunk_handles_empty_and_partial_batches():
    assert chunk([], 2) == []
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_pager_row_has_correct_navigation_edges():
    assert pager_row(0, 1, lambda p: f"page:{p}", "noop") == []
    first = pager_row(0, 3, lambda p: f"page:{p}", "noop")
    assert [b.callback_data for b in first] == ["noop", "page:1"]
    middle = pager_row(1, 3, lambda p: f"page:{p}", "noop")
    assert [b.callback_data for b in middle] == ["page:0", "noop", "page:2"]
    last = pager_row(2, 3, lambda p: f"page:{p}", "noop")
    assert [b.callback_data for b in last] == ["page:1", "noop"]


def test_support_links_are_normalized():
    assert normalize_support("@nexora_help") == "https://t.me/nexora_help"
    assert normalize_support("t.me/nexora_help") == "https://t.me/nexora_help"
    assert normalize_support("https://example.com/help") == "https://example.com/help"
    assert normalize_support("") == ""


def test_button_builder_supports_callback_and_url():
    callback = B("Open", "open_menu")
    url = B("Support", url="https://t.me/nexora_help")
    assert callback.callback_data == "open_menu"
    assert url.url == "https://t.me/nexora_help"
