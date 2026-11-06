import pytest

from netmonitor.utils.validation import host_kind, is_domain, is_ipv4, is_ipv6, is_private_address


@pytest.mark.parametrize("v", ["8.8.8.8", "192.168.0.1", "10.0.1.138", "0.0.0.0", "255.255.255.255", " 1.1.1.1 "])
def test_ipv4_valid(v):
    assert is_ipv4(v)


@pytest.mark.parametrize("v", ["256.1.1.1", "1.2.3", "1.2.3.4.5", "abc", "", "1.2.3.-1", "01.2.3.4x"])
def test_ipv4_invalid(v):
    assert not is_ipv4(v)


@pytest.mark.parametrize("v", ["::1", "2001:4860:4860::8888", "fe80::1", "[2001:db8::1]", "::ffff:192.0.2.1"])
def test_ipv6_valid(v):
    assert is_ipv6(v)


@pytest.mark.parametrize("v", ["2001:::1", "gggg::1", "1.2.3.4", "", "2001:db8::1::2"])
def test_ipv6_invalid(v):
    assert not is_ipv6(v)


@pytest.mark.parametrize("v", ["zelina.eu", "seznam.cz", "localhost", "a-b.c-d.example", "příklad.cz", "xn--pklad-zsa.cz",
                               "host.", "a" * 63 + ".com"])
def test_domain_valid(v):
    assert is_domain(v)


@pytest.mark.parametrize("v", ["-bad.cz", "bad-.cz", "a..b", "", "8.8.8.8", "1.2.3", "a" * 64 + ".com",
                               "spa ce.cz", "under_score.cz", "x" * 254])
def test_domain_invalid(v):
    assert not is_domain(v)


def test_host_kind():
    assert host_kind("8.8.8.8") == "ipv4"
    assert host_kind("::1") == "ipv6"
    assert host_kind("seznam.cz") == "domain"
    assert host_kind("bad host") == "invalid"


def test_private_addresses():
    for a in ("192.168.1.1", "10.0.0.138", "172.16.0.1", "100.64.1.1", "127.0.0.1", "fe80::1", "fd00::1"):
        assert is_private_address(a), a
    for a in ("8.8.8.8", "160.218.161.60", "2001:4860:4860::8888"):
        assert not is_private_address(a), a
