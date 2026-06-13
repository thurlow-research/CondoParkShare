"""
System tests — HOA/manager portal (SPEC-1 §8, §11).

Covers:
  1. HOA admin can see their building's residents; cannot see another building's residents.
  2. HOA admin can approve/block residents.
  3. HOA admin can view usage reports.
  4. HOA admin cannot access operator console.
  5. Regular resident (non-admin) cannot access HOA portal.
"""

import pytest
from django.test import Client, override_settings

from tests.system.conftest import (
    client_get,
    client_post,
    force_login_active,
    make_org,
    make_user,
    utc,
)

HOSTNAME_A = "hoaportal-a.parkshare.test"
HOSTNAME_B = "hoaportal-b.parkshare.test"


@pytest.fixture
def org_a(db):
    return make_org("HoaPortalOrgA", HOSTNAME_A)


@pytest.fixture
def org_b(db):
    return make_org("HoaPortalOrgB", HOSTNAME_B)


@pytest.fixture
def hoa_admin_a(org_a):
    return make_user(org_a, "hoa_a@hoaportal.test", is_hoa_admin=True, status="active")


@pytest.fixture
def resident_a(org_a):
    return make_user(org_a, "resident_a@hoaportal.test", status="active")


@pytest.fixture
def resident_b(org_b):
    return make_user(org_b, "resident_b@hoaportal.test", status="active")


# ---------------------------------------------------------------------------
# Test 1a — HOA admin can access their portal
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME_A])
def test_hoa_admin_can_access_portal(org_a, hoa_admin_a):
    """
    HOA admin for org A can GET /portal/ without permission error. (SPEC-1 §8)
    """
    client = Client()
    force_login_active(client, hoa_admin_a)
    response = client_get(client, HOSTNAME_A, "/portal/")
    assert response.status_code == 200, (
        f"Expected 200 for HOA admin portal access, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Test 1b — HOA admin cannot see another org's residents
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME_A, HOSTNAME_B])
def test_hoa_admin_cannot_see_other_org_resident(org_a, org_b, hoa_admin_a, resident_b):
    """
    HOA admin for org A cannot access org B's resident detail. (SPEC-1 §8 tenant-scoped)
    """
    client = Client()
    force_login_active(client, hoa_admin_a)

    # Try to access org B's resident from org A's portal (via org A hostname)
    response = client_get(client, HOSTNAME_A, f"/portal/residents/{resident_b.pk}/")
    # Should be 404 (resident not in this org) or 403
    assert response.status_code in (404, 403), (
        f"Expected 404 or 403 for cross-org resident access, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Test 2a — HOA admin can approve a pending resident
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME_A])
def test_hoa_admin_approve_resident(org_a, hoa_admin_a):
    """
    HOA admin approves a pending_approval resident → status becomes 'active'. (SPEC-1 §8)
    """
    pending = make_user(org_a, "pending@hoaportal.test", status="pending_approval")

    client = Client()
    force_login_active(client, hoa_admin_a)

    response = client_post(client, HOSTNAME_A, f"/portal/residents/{pending.pk}/approve/")
    assert response.status_code == 302, (
        f"Expected redirect after approval, got {response.status_code}"
    )

    pending.refresh_from_db()
    assert pending.status == "active", (
        f"Expected 'active' after approval, got '{pending.status}'"
    )


# ---------------------------------------------------------------------------
# Test 2b — HOA admin can block a resident
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME_A])
def test_hoa_admin_block_resident(org_a, hoa_admin_a, resident_a):
    """
    HOA admin blocks an active resident → status becomes 'blocked'. (SPEC-1 §8)
    """
    client = Client()
    force_login_active(client, hoa_admin_a)

    response = client_post(client, HOSTNAME_A, f"/portal/residents/{resident_a.pk}/block/")
    assert response.status_code == 302, (
        f"Expected redirect after block, got {response.status_code}"
    )

    resident_a.refresh_from_db()
    assert resident_a.status == "blocked", (
        f"Expected 'blocked', got '{resident_a.status}'"
    )


# ---------------------------------------------------------------------------
# Test 3 — HOA admin can view usage reports
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME_A])
def test_hoa_admin_can_view_reports(org_a, hoa_admin_a):
    """
    HOA admin can GET /portal/reports/ without error. (SPEC-1 §8)
    """
    client = Client()
    force_login_active(client, hoa_admin_a)
    response = client_get(client, HOSTNAME_A, "/portal/reports/")
    assert response.status_code == 200, (
        f"Expected 200 for reports page, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Test 4 — HOA admin cannot access operator console
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME_A])
def test_hoa_admin_cannot_access_operator_console(org_a, hoa_admin_a):
    """
    HOA admin cannot access /admin/ (operator console). (SPEC-1 §8)
    """
    client = Client()
    force_login_active(client, hoa_admin_a)
    response = client_get(client, HOSTNAME_A, "/admin/")
    # Should be redirected to login or denied (not 200)
    assert response.status_code in (302, 403), (
        f"Expected redirect or 403 for non-operator accessing /admin/, got {response.status_code}"
    )


# ---------------------------------------------------------------------------
# Test 5 — Regular resident cannot access HOA portal
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@override_settings(ALLOWED_HOSTS=[HOSTNAME_A])
def test_regular_resident_cannot_access_hoa_portal(org_a, resident_a):
    """
    Non-admin resident attempting to access /portal/ gets 403 PermissionDenied. (SPEC-1 §8)
    """
    client = Client()
    force_login_active(client, resident_a)
    response = client_get(client, HOSTNAME_A, "/portal/")
    assert response.status_code == 403, (
        f"Expected 403 for regular resident accessing /portal/, got {response.status_code}"
    )
