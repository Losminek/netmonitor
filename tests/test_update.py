from netmonitor.main import is_newer, parse_update_info


def test_github_release_format():
    assert parse_update_info({"tag_name": "v1.2.0", "html_url": "https://github.com/x/y/releases/tag/v1.2.0"}) == \
        ("1.2.0", "https://github.com/x/y/releases/tag/v1.2.0")


def test_custom_format_and_garbage():
    assert parse_update_info({"version": "1.0.5", "url": "u"}) == ("1.0.5", "u")
    assert parse_update_info({"message": "Not Found"}) is None
    assert parse_update_info([]) is None


def test_version_compare():
    assert is_newer("1.0.2", "1.0.1")
    assert is_newer("1.1", "1.0.9")
    assert not is_newer("1.0.1", "1.0.1")
    assert not is_newer("0.9.9", "1.0.1")
    assert is_newer("2.0.0-beta", "1.9.9")
