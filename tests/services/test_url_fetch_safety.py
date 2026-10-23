"""
Tests for app/services/url_fetch.py's SSRF-mitigation logic. Verified locally before being
formalized here (19 checks - see CHANGELOG.md). Loopback/localhost/IP-literal cases don't
require real network access to test (resolved via /etc/hosts or parsed directly from the URL);
public-hostname DNS-resolution cases are not included here since they'd require live DNS,
which this environment doesn't have - the classification logic they'd exercise is already
covered by the IP-literal cases, which test the same _is_safe_ip() function directly.
"""
import pytest

from app.services.url_fetch import UnsafeURLError, _is_safe_ip, validate_url_is_safe


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "gopher://example.com",
    ],
)
def test_disallowed_schemes_rejected(url):
    with pytest.raises(UnsafeURLError):
        validate_url_is_safe(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://127.0.0.1/",
        "http://127.0.0.1:8080/admin",
        "http://10.0.0.1/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata endpoint - the single
        # most common real-world SSRF target; explicitly covered, not incidental.
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[fc00::1]/",
    ],
)
def test_private_and_internal_addresses_rejected(url):
    with pytest.raises(UnsafeURLError):
        validate_url_is_safe(url)


def test_url_with_no_hostname_rejected():
    with pytest.raises(UnsafeURLError):
        validate_url_is_safe("http://")


@pytest.mark.parametrize(
    "ip",
    ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"],
)
def test_public_ip_addresses_classified_as_safe(ip):
    assert _is_safe_ip(ip) is True


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fe80::1"],
)
def test_private_ip_addresses_classified_as_unsafe(ip):
    assert _is_safe_ip(ip) is False

