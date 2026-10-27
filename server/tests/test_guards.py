import json

import pytest

from erpnext_mcp.config import Profile
from erpnext_mcp.errors import GuardError
from erpnext_mcp.guards import (
    WRITE_BLOCKLIST,
    audit,
    check_draft_update,
    check_submit_allowed,
    check_writable,
    sanitize_new_doc,
)


@pytest.mark.parametrize("dt", ["User", "Role", "Server Script", "System Settings", "DocType"])
def test_blocklisted_doctype_refused(dt):
    with pytest.raises(GuardError):
        check_writable(dt)


@pytest.mark.parametrize("dt", ["Sales Order", "Custom Field", "Item", "Customer"])
def test_normal_doctype_allowed(dt):
    check_writable(dt)


def test_blocklist_matches_spec():
    for dt in ["User", "Role", "Role Profile", "Has Role", "DocType", "Custom DocPerm", "DocPerm",
               "System Settings", "OAuth Client", "OAuth Bearer Token", "Server Script",
               "Scheduled Job Type", "User Permission", "Module Def", "Website Settings"]:
        assert dt in WRITE_BLOCKLIST


def test_sanitize_forces_draft():
    data = {"docstatus": 1, "customer": "A"}
    out = sanitize_new_doc(data)
    assert out["docstatus"] == 0
    assert out["customer"] == "A"
    assert data["docstatus"] == 1


def test_update_refused_when_submitted():
    with pytest.raises(GuardError):
        check_draft_update({"docstatus": 1}, {"qty": 2})
    with pytest.raises(GuardError):
        check_draft_update({"docstatus": 2}, {"qty": 2})


def test_update_refuses_docstatus_change():
    with pytest.raises(GuardError):
        check_draft_update({"docstatus": 0}, {"docstatus": 1})


def test_update_draft_ok():
    check_draft_update({"docstatus": 0}, {"qty": 2})


@pytest.mark.parametrize("confirm,allow,ok", [(False, False, False), (True, False, False),
                                              (False, True, False), (True, True, True)])
def test_submit_gate(confirm, allow, ok):
    prof = Profile(name="t", url="http://x", api_key="k", api_secret="s", allow_submit=allow)
    if ok:
        check_submit_allowed(prof, confirm)
    else:
        with pytest.raises(GuardError) as e:
            check_submit_allowed(prof, confirm)
        assert "ask the user" in str(e.value)


def test_audit_writes_jsonl(home):
    audit("dev", "create_draft", "Sales Order", "SO-1", "ok")
    audit("dev", "submit_doc", "Sales Order", "SO-1", "refused")
    lines = (home / "audit.log").read_text().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert set(rec) == {"ts", "profile", "tool", "doctype", "name", "outcome"}
    assert rec["outcome"] == "ok"
    text = (home / "audit.log").read_text()
    assert "api_key" not in text and "api_secret" not in text


def test_audit_never_raises(home, monkeypatch):
    home.mkdir(parents=True, exist_ok=True)
    (home / "audit.log").mkdir()  # make the log path unwritable as a file
    audit("dev", "create_draft", "Item", None, "ok")


# ---- final review fixes ------------------------------------------------------

from erpnext_mcp.guards import check_safe_values


@pytest.mark.parametrize("dt", ["Client Script", "Report", "Webhook", "Notification", "Email Queue",
                                "Custom Role", "Role Permission for Page and Report", "Social Login Key",
                                "LDAP Settings", "Web Page", "Web Form", "Print Format", "Auto Email Report",
                                "Data Import"])
def test_code_and_side_effect_doctypes_blocked(dt):
    with pytest.raises(GuardError):
        check_writable(dt)


@pytest.mark.parametrize("dt", ["user", "User ", " server script", "SYSTEM SETTINGS"])
def test_blocklist_ignores_case_and_spaces(dt):
    with pytest.raises(GuardError):
        check_writable(dt)


@pytest.mark.parametrize("data", [
    {"fieldtype": "HTML", "options": "<b>hi</b>"},
    {"fieldtype": "Data", "depends_on": "eval:frappe.call('x')"},
    {"fieldtype": "Small Text", "options": "<SCRIPT>alert(1)</script>"},
    {"property": "mandatory_depends_on", "value": " eval:doc.x"},
])
def test_custom_field_code_values_refused(data):
    with pytest.raises(GuardError):
        check_safe_values("Custom Field", data)
    with pytest.raises(GuardError):
        check_safe_values("Property Setter", data)


def test_custom_field_plain_values_ok():
    check_safe_values("Custom Field", {"fieldtype": "Select", "options": "Morning\nEvening", "depends_on": "customer"})
    check_safe_values("Sales Order", {"remarks": "eval: not code here"})


@pytest.mark.parametrize("key", ["name", "doctype", "owner", "creation", "modified", "amended_from"])
def test_update_refuses_identity_keys(key):
    with pytest.raises(GuardError):
        check_draft_update({"docstatus": 0}, {key: "X"})
