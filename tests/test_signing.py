"""Tests for HMAC plan signing."""

from __future__ import annotations

from bekas.signing import sign_plan, verify_plan


def test_sign_and_verify():
    plan_data = {"audit_id": "ad1", "candidates": []}
    sig = sign_plan(plan_data)
    assert verify_plan(plan_data, sig) is True


def test_tampered_plan_fails():
    plan_data = {"audit_id": "ad1", "candidates": []}
    sig = sign_plan(plan_data)
    tampered = {"audit_id": "ad2", "candidates": []}
    assert verify_plan(tampered, sig) is False


def test_wrong_signature_fails():
    plan_data = {"audit_id": "ad1", "candidates": []}
    assert verify_plan(plan_data, "00000000000000000000000000000000") is False


def test_empty_signature_fails():
    plan_data = {"audit_id": "ad1", "candidates": []}
    assert verify_plan(plan_data, "") is False
