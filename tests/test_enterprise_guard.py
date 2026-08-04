# Synthelion — Python port of Caveman (https://github.com/francescopaolopassaro/caveman)
# (c) 2026 Passaro Francesco Paolo — Digitalsolutions.it
"""Tests for EnterpriseGuard: outbound DLP (credential-shaped content) and
file-path "security zone" blocking (synthelion/enterprise_guard.py)."""
from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

import pytest

from synthelion.enterprise_guard import EnterpriseGuard, EnterpriseGuardBlockedError, GuardResult


def _guard(**overrides):
    cfg = {
        "enabled": True,
        "content_categories": {
            "cloud_credentials": True,
            "database_connections": True,
            "ftp_credentials": True,
            "git_credentials": True,
            "private_keys": True,
            "api_tokens": True,
            "dotenv_bulk": True,
        },
        "blocked_paths": [],
        "use_default_blocked_paths": True,
    }
    cfg.update(overrides)
    return EnterpriseGuard(cfg)


class TestContentDetection:
    def test_clean_text_not_blocked(self):
        result = _guard().check_text("The quarterly report shows a 12% increase in revenue.")
        assert result.blocked is False

    def test_empty_text_not_blocked(self):
        assert _guard().check_text("").blocked is False

    def test_aws_access_key_blocked(self):
        result = _guard().check_text("here is my key AKIAIOSFODNN7EXAMPLE for the deploy")
        assert result.blocked is True
        assert result.category == "cloud_credentials"

    def test_aws_secret_line_blocked(self):
        result = _guard().check_text("aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY123")
        assert result.blocked is True
        assert result.category == "cloud_credentials"

    def test_azure_connection_string_blocked(self):
        text = (
            "DefaultEndpointsProtocol=https;AccountName=mystorageacct;"
            "AccountKey=abcd1234ABCD5678efgh9012EFGH3456ijkl==;EndpointSuffix=core.windows.net"
        )
        result = _guard().check_text(text)
        assert result.blocked is True
        assert result.category == "cloud_credentials"

    def test_private_key_block_blocked(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----"
        result = _guard().check_text(text)
        assert result.blocked is True
        assert result.category == "private_keys"

    def test_ftp_credentials_url_blocked(self):
        result = _guard().check_text("connect to ftp://deployuser:sup3rSecret@ftp.example.com/releases")
        assert result.blocked is True
        assert result.category == "ftp_credentials"

    def test_postgres_connection_string_blocked(self):
        result = _guard().check_text("DATABASE_URL=postgres://admin:hunter2@db.internal.example.com:5432/prod")
        assert result.blocked is True
        assert result.category == "database_connections"

    def test_mongodb_connection_string_blocked(self):
        result = _guard().check_text("mongodb+srv://svc_user:p4ssw0rd@cluster0.mongodb.net/accounting")
        assert result.blocked is True
        assert result.category == "database_connections"

    def test_ado_connection_string_blocked(self):
        text = "Server=sqlserver01;Database=Accounting;User Id=sa;Password=Str0ngP@ss;"
        result = _guard().check_text(text)
        assert result.blocked is True
        assert result.category == "database_connections"

    def test_jdbc_connection_string_blocked(self):
        result = _guard().check_text("jdbc:mysql://db.internal:3306/erp?user=root&password=secret")
        assert result.blocked is True
        assert result.category == "database_connections"

    def test_git_remote_with_credentials_blocked(self):
        result = _guard().check_text("git remote add origin https://ghp_faketoken123:x-oauth-basic@github.com/acme/private-repo.git")
        assert result.blocked is True
        assert result.category == "git_credentials"

    def test_github_token_blocked(self):
        result = _guard().check_text("token: ghp_1234567890abcdefghijklmnopqrstuvwxyz")
        assert result.blocked is True
        assert result.category == "api_tokens"

    def test_bulk_dotenv_dump_blocked(self):
        text = "DB_SECRET=abc123\nAPI_TOKEN=xyz789\nADMIN_PASSWORD=letmein\n"
        result = _guard().check_text(text)
        assert result.blocked is True
        assert result.category == "dotenv_bulk"

    def test_single_dotenv_style_line_not_blocked(self):
        # One KEY=value line alone is common/ordinary output — not alarming.
        result = _guard().check_text("STATUS_TOKEN=set\nsome other unrelated log line\n")
        assert result.blocked is False


class TestCategoryToggles:
    def test_disabled_category_not_blocked(self):
        result = _guard(content_categories={
            "cloud_credentials": False, "database_connections": True, "ftp_credentials": True,
            "git_credentials": True, "private_keys": True, "api_tokens": True, "dotenv_bulk": True,
        }).check_text("AKIAIOSFODNN7EXAMPLE")
        assert result.blocked is False

    def test_master_switch_disables_everything(self):
        result = _guard(enabled=False).check_text("AKIAIOSFODNN7EXAMPLE")
        assert result.blocked is False


class TestPathBlocking:
    def test_dotenv_path_blocked_by_default(self):
        assert _guard().check_path("/home/user/project/.env").blocked is True

    def test_git_config_blocked_by_default(self):
        assert _guard().check_path("C:/repo/.git/config").blocked is True

    def test_ssh_private_key_blocked_by_default(self):
        assert _guard().check_path("/home/user/.ssh/id_rsa").blocked is True

    def test_ordinary_source_file_not_blocked(self):
        assert _guard().check_path("synthelion/core.py").blocked is False

    def test_user_defined_pattern_blocked(self):
        result = _guard(blocked_paths=["**/fatture/**", "**/payroll/*.xlsx"]).check_path(
            "C:/Sorgenti/accounting/fatture/2026/invoice_042.pdf"
        )
        assert result.blocked is True
        assert result.category == "blocked_path"

    def test_user_defined_pattern_does_not_affect_unrelated_paths(self):
        result = _guard(blocked_paths=["**/fatture/**"]).check_path("synthelion/core.py")
        assert result.blocked is False

    def test_disabling_default_patterns_allows_env_path(self):
        result = _guard(use_default_blocked_paths=False).check_path("/home/user/project/.env")
        assert result.blocked is False

    def test_empty_path_not_blocked(self):
        assert _guard().check_path("").blocked is False


class TestToolCallChecking:
    def test_read_tool_blocked_file_path(self):
        result = _guard().check_tool_call("Read", {"file_path": "/home/user/.ssh/id_rsa"})
        assert result.blocked is True

    def test_read_tool_ordinary_file_allowed(self):
        result = _guard().check_tool_call("Read", {"file_path": "synthelion/core.py"})
        assert result.blocked is False

    def test_bash_command_referencing_blocked_path(self):
        result = _guard().check_tool_call("Bash", {"command": "cat /home/user/project/.env"})
        assert result.blocked is True

    def test_bash_command_with_inline_credential(self):
        result = _guard().check_tool_call("Bash", {"command": "export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"})
        assert result.blocked is True

    def test_bash_ordinary_command_allowed(self):
        result = _guard().check_tool_call("Bash", {"command": "ls -la synthelion/"})
        assert result.blocked is False

    def test_write_tool_blocked_content(self):
        result = _guard().check_tool_call("Write", {"file_path": "notes.txt", "content": "AKIAIOSFODNN7EXAMPLE"})
        assert result.blocked is True

    def test_unrelated_tool_arguments_allowed(self):
        result = _guard().check_tool_call("Grep", {"pattern": "TODO", "path": "synthelion/"})
        assert result.blocked is False

    def test_master_switch_disables_tool_call_check(self):
        result = _guard(enabled=False).check_tool_call("Read", {"file_path": "/home/user/.ssh/id_rsa"})
        assert result.blocked is False


class TestGuardResultAndError:
    def test_default_guard_result_not_blocked(self):
        result = GuardResult(blocked=False)
        assert result.category is None
        assert result.reason is None

    def test_blocked_error_carries_result(self):
        result = GuardResult(blocked=True, category="cloud_credentials", rule_name="AWS access key", reason="Blocked: x")
        err = EnterpriseGuardBlockedError(result)
        assert err.result is result
        assert str(err) == "Blocked: x"


class TestConfigIntegration:
    def test_default_config_has_enterprise_guard_section(self):
        from synthelion.config import enterprise_guard_config
        cfg = enterprise_guard_config({})
        assert cfg["enabled"] is True
        assert cfg["content_categories"]["cloud_credentials"] is True
        assert cfg["blocked_paths"] == []
        assert cfg["use_default_blocked_paths"] is True

    def test_partial_override_merges_with_defaults(self):
        from synthelion.config import enterprise_guard_config
        cfg = enterprise_guard_config({
            "enterprise_guard": {
                "content_categories": {"cloud_credentials": False},
                "blocked_paths": ["**/fatture/**"],
            }
        })
        assert cfg["content_categories"]["cloud_credentials"] is False
        # Untouched categories keep their default (True), proving a deep merge
        # rather than the user's partial dict replacing the whole sub-object.
        assert cfg["content_categories"]["database_connections"] is True
        assert cfg["blocked_paths"] == ["**/fatture/**"]

    def test_guard_constructed_from_real_config_helper(self):
        from synthelion.config import enterprise_guard_config
        guard = EnterpriseGuard(enterprise_guard_config({}))
        assert guard.check_text("AKIAIOSFODNN7EXAMPLE").blocked is True


class TestFirewallCheckCli:
    """`synthelion firewall-check` — the PreToolUse-hookable CLI command,
    mirroring `loop-check`'s exit-code contract (0 = allow, 2 = block)."""

    def _run(self, args: list[str]) -> tuple[str, str, int]:
        from synthelion.cli import main
        with patch("sys.argv", ["synthelion"] + args):
            with patch("sys.stdout", new_callable=StringIO) as mock_out:
                with patch("sys.stderr", new_callable=StringIO) as mock_err:
                    code = 0
                    try:
                        main()
                    except SystemExit as e:
                        code = e.code or 0
                    return mock_out.getvalue(), mock_err.getvalue(), code

    def test_allows_ordinary_tool_call(self):
        args_json = json.dumps({"file_path": "synthelion/core.py"})
        out, err, code = self._run(["firewall-check", "--tool", "Read", "--args", args_json])
        assert code == 0

    def test_blocks_protected_file_path(self):
        args_json = json.dumps({"file_path": "/home/user/.ssh/id_rsa"})
        out, err, code = self._run(["firewall-check", "--tool", "Read", "--args", args_json])
        assert code == 2
        assert "BLOCK" in err

    def test_json_output_reports_blocked_fields(self):
        args_json = json.dumps({"file_path": "/home/user/.env"})
        out, err, code = self._run(["firewall-check", "--tool", "Read", "--args", args_json, "--json"])
        data = json.loads(out.strip())
        assert data["blocked"] is True
        assert data["category"] == "blocked_path"
        assert code == 2

    def test_invalid_json_args_errors_cleanly(self):
        out, err, code = self._run(["firewall-check", "--tool", "Read", "--args", "{not valid json"])
        assert code == 1
        assert "ERROR" in err

    def test_no_args_allows(self):
        out, err, code = self._run(["firewall-check", "--tool", "SomeTool"])
        assert code == 0


class TestCompressBlockedByEnterpriseGuard:
    """`synthelion compress --json` refuses to compress/emit credential-shaped
    content, mirroring privacy.block_on_risk's block-not-mask posture."""

    def _run_compress(self, text: str) -> dict:
        from synthelion.cli import main
        with patch("sys.argv", ["synthelion", "compress", "--text", text, "--json"]):
            with patch("sys.stdout", new_callable=StringIO) as mock_out:
                with patch("sys.stderr", new_callable=StringIO):
                    try:
                        main()
                    except SystemExit:
                        pass
                    return json.loads(mock_out.getvalue().strip())

    def test_ordinary_text_not_blocked(self):
        data = self._run_compress("Please summarize this quarterly report for the board.")
        assert data["blocked"] is False
        assert data["compressed"]

    def test_aws_key_blocks_compression(self):
        data = self._run_compress("deploy key: AKIAIOSFODNN7EXAMPLE")
        assert data["blocked"] is True
        assert data["enterprise_guard_blocked"] is True
        assert data["compressed"] == ""

    def test_db_connection_string_blocks_compression(self):
        data = self._run_compress("DATABASE_URL=postgres://admin:hunter2@db.internal.example.com:5432/prod")
        assert data["blocked"] is True
        assert data["enterprise_guard_blocked"] is True
