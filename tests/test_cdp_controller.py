from im_archive_cli.cdp_plugin_controller import parse_extension_id_from_target_url


def test_parse_extension_id_from_target_url() -> None:
    url = "chrome-extension://abcdefghijklmnopabcdefghijklmnop/background.js"
    ext_id = parse_extension_id_from_target_url(url)
    assert ext_id == "abcdefghijklmnopabcdefghijklmnop"

