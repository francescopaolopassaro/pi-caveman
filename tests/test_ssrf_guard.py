# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# (c) 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""Tests for synthelion/ssrf_guard.py."""
from __future__ import annotations

import pytest

from synthelion.ssrf_guard import find_ssrf_target


class TestMetadataEndpoints:
    @pytest.mark.parametrize("url", [
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/role",
        "http://169.254.170.2/v2/credentials/uuid",
        "http://metadata.google.internal/computeMetadata/v1/",
        "https://metadata.azure.com/metadata/instance",
        "http://100.100.100.200/latest/meta-data/",
    ])
    def test_cloud_metadata_detected(self, url):
        assert find_ssrf_target(url) == "cloud-metadata-endpoint"


class TestLoopback:
    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/admin",
        "http://127.0.0.1:8080/",
        "http://localhost:3000/internal",
        "http://0.0.0.0:9000/",
        "http://[::1]:8000/",
    ])
    def test_loopback_detected(self, url):
        assert find_ssrf_target(url) == "loopback-address"


class TestPrivateRanges:
    @pytest.mark.parametrize("url", [
        "http://10.0.0.5/internal-api",
        "http://172.16.0.1/",
        "http://172.31.255.254/",
        "http://192.168.1.1/router-config",
    ])
    def test_private_range_detected(self, url):
        assert find_ssrf_target(url) == "private-network-range"

    def test_public_range_that_looks_similar_not_flagged(self):
        # 172.32.x.x is outside the 172.16-31 private range — must not match.
        assert find_ssrf_target("http://172.32.0.1/") is None


class TestDangerousSchemes:
    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "gopher://internal-host:70/",
        "dict://internal-host:2628/",
    ])
    def test_dangerous_scheme_detected(self, url):
        assert find_ssrf_target(url) == "dangerous-scheme"


class TestOrdinaryUrlsNotFlagged:
    @pytest.mark.parametrize("url", [
        "https://example.com/article",
        "https://api.anthropic.com/v1/messages",
        "https://172.example-vendor.com/webhook",  # not a private-range IP
    ])
    def test_public_url_allowed(self, url):
        assert find_ssrf_target(url) is None

    def test_empty_text_allowed(self):
        assert find_ssrf_target("") is None
        assert find_ssrf_target(None) is None

    def test_prose_mention_without_scheme_not_flagged(self):
        # No scheme ("xxx://") — conservative-by-design, avoids flagging a
        # plain-prose mention of an internal IP in a chat message.
        assert find_ssrf_target("our internal server is at 192.168.1.1, port 8080") is None
