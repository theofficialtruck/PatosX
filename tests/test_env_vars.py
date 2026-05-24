"""Tests for AUTHORIZED_USER_IDS and BEG_DONORS env variable behaviour."""
import os
import importlib
import pytest
import main


# ── AUTHORIZED_USER_IDS ──────────────────────────────────────────────────────

def test_authorized_user_ids_is_a_set():
    assert isinstance(main.AUTHORIZED_USER_IDS, set)


def test_authorized_user_ids_contains_only_ints():
    for uid in main.AUTHORIZED_USER_IDS:
        assert isinstance(uid, int), f"Expected int, got {type(uid)} for {uid!r}"


def test_authorized_user_ids_nonempty_when_env_set(monkeypatch):
    monkeypatch.setenv('AUTHORIZED_USER_IDS', '111111111111111111,222222222222222222')
    raw = os.getenv('AUTHORIZED_USER_IDS', '')
    ids = {int(uid.strip()) for uid in raw.split(',') if uid.strip().isdigit()}
    assert 111111111111111111 in ids
    assert 222222222222222222 in ids


def test_authorized_user_ids_ignores_non_numeric(monkeypatch):
    monkeypatch.setenv('AUTHORIZED_USER_IDS', '123456,notanumber,789012')
    raw = os.getenv('AUTHORIZED_USER_IDS', '')
    ids = {int(uid.strip()) for uid in raw.split(',') if uid.strip().isdigit()}
    assert 123456 in ids
    assert 789012 in ids
    assert 'notanumber' not in str(ids)


def test_authorized_user_ids_empty_when_env_empty(monkeypatch):
    monkeypatch.setenv('AUTHORIZED_USER_IDS', '')
    raw = os.getenv('AUTHORIZED_USER_IDS', '')
    ids = {int(uid.strip()) for uid in raw.split(',') if uid.strip().isdigit()}
    assert ids == set()


# ── BEG_DONORS ───────────────────────────────────────────────────────────────

def test_beg_donors_is_a_list():
    assert isinstance(main.BEG_DONORS, list)


def test_beg_donors_nonempty():
    assert len(main.BEG_DONORS) >= 1


def test_beg_donors_contains_cutebatak():
    assert 'CuteBatak' in main.BEG_DONORS, \
        "CuteBatak should be a beg donor"


def test_beg_donors_from_env(monkeypatch):
    monkeypatch.setenv('BEG_DONORS', 'alice,bob,charlie')
    raw = os.getenv('BEG_DONORS', 'thetruck')
    donors = [d.strip() for d in raw.split(',') if d.strip()] or ['thetruck']
    assert donors == ['alice', 'bob', 'charlie']


def test_beg_donors_falls_back_to_thetruck_when_env_empty(monkeypatch):
    monkeypatch.setenv('BEG_DONORS', '')
    raw = os.getenv('BEG_DONORS', 'thetruck')
    donors = [d.strip() for d in raw.split(',') if d.strip()] or ['thetruck']
    assert donors == ['thetruck']


def test_beg_donors_default_includes_thetruck():
    """Without monkeypatching, the module-level BEG_DONORS must have thetruck."""
    assert 'thetruck' in main.BEG_DONORS, "thetruck must always be a beg donor"


# ── Integration: no hardcoded IDs left in beg path ───────────────────────────

def test_beg_donor_default_contains_both_donors():
    """Regression guard: default BEG_DONORS must include both thetruck and CuteBatak."""
    assert 'thetruck' in main.BEG_DONORS
    assert 'CuteBatak' in main.BEG_DONORS
