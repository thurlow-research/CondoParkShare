"""
accounts.admin — accounts app admin registrations.

All admin registrations for accounts models (UserAdmin, AdminAuditLogAdmin)
live in operator/admin.py so the operator console can co-locate them with
parking model registrations and apply the unscoped-manager pattern uniformly.

The pii_erasure bulk action is defined in operator/admin.py alongside UserAdmin.
"""
