"""Tests for privacy guardrails — sensitive content detection."""

import pytest
from mycellm.privacy import scan_sensitive, format_warning, TRUST_LEVELS


# --- API key detection ---

def test_detects_openai_key():
    matches = scan_sensitive("Here is my key: sk-abc123def456ghi789jklmnop")
    assert any(m.type == "api_key" for m in matches)


def test_detects_github_token():
    matches = scan_sensitive("Use ghp_1234567890abcdefghijklmnopqrstuvwxyz for auth")
    assert any(m.type == "api_key" for m in matches)


def test_detects_aws_key():
    matches = scan_sensitive("AWS_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE")
    assert any(m.type == "api_key" for m in matches)


def test_detects_anthropic_key():
    matches = scan_sensitive("sk-ant-api03-abcdef1234567890abcdef")
    assert any(m.type == "api_key" for m in matches)


def test_detects_hf_token():
    matches = scan_sensitive("export HF_TOKEN=hf_abcdefghijklmnopqrstuvwx")
    assert any(m.type == "api_key" for m in matches)


# --- Private keys ---

def test_detects_rsa_private_key():
    matches = scan_sensitive("-----BEGIN RSA PRIVATE KEY-----\nMIIE...")
    assert any(m.type == "private_key" for m in matches)


def test_detects_ed25519_private_key():
    matches = scan_sensitive("-----BEGIN ED25519 PRIVATE KEY-----")
    assert any(m.type == "private_key" for m in matches)


# --- Passwords ---

def test_detects_password_assignment():
    matches = scan_sensitive("password=MyS3cr3tP@ss!")
    assert any(m.type == "password" for m in matches)


def test_detects_password_mention():
    matches = scan_sensitive("my password is hunter2")
    assert any(m.type == "password" for m in matches)


# --- Financial ---

def test_detects_credit_card():
    matches = scan_sensitive("Card: 4532-1234-5678-9012")
    assert any(m.type == "credit_card" for m in matches)


def test_detects_ssn():
    matches = scan_sensitive("SSN: 123-45-6789")
    assert any(m.type == "ssn" for m in matches)


# --- Connection strings ---

def test_detects_postgres_connection():
    matches = scan_sensitive("postgresql://admin:pass@db.example.com/mydb")
    assert any(m.type == "connection_string" for m in matches)


# --- False negatives (should NOT match) ---

def test_no_match_normal_text():
    matches = scan_sensitive("What is the weather like today?")
    assert len(matches) == 0


def test_no_match_code_discussion():
    matches = scan_sensitive("Write a Python function to sort a list")
    assert len(matches) == 0


def test_no_match_short_password_context():
    matches = scan_sensitive("Set a strong password for your account")
    assert len(matches) == 0


# --- Formatting ---

def test_format_warning_empty():
    assert format_warning([]) == ""


def test_format_warning_with_matches():
    matches = scan_sensitive("sk-abc123def456ghi789jklmnop")
    warning = format_warning(matches)
    assert "Sensitive content detected" in warning
    assert "API key" in warning


# --- Trust levels ---

def test_trust_levels_exist():
    assert "untrusted" in TRUST_LEVELS
    assert "trusted" in TRUST_LEVELS
    assert "full" in TRUST_LEVELS
    assert "local" in TRUST_LEVELS


def test_untrusted_says_do_not_send():
    assert "Do not send" in TRUST_LEVELS["untrusted"]["sensitive_data"]


def test_full_trust_permits_data():
    assert "Permitted" in TRUST_LEVELS["full"]["sensitive_data"]


# --- Redaction ---

def test_redaction_in_match():
    matches = scan_sensitive("sk-abc123def456ghi789jklmnop")
    assert matches[0].pattern.startswith("sk-a")
    assert "..." in matches[0].pattern
    # Full value should NOT appear
    assert "abc123def456ghi789jklmnop" not in matches[0].pattern
