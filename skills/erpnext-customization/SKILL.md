---
name: erpnext-customization
description: Use when the user asks how an ERPNext DocType is structured, what fields a form has, or wants to customize ERPNext - add a custom field, change a field property, or write a server script, client script, report or Frappe app code.
---

# ERPNext customization

## Understand first

- Fields of a form: `get_doctype_schema("Sales Order")` (includes child tables).
- Existing customizations: `list_docs("Custom Field", {"dt": "Sales Order"}, ["name", "fieldname", "label", "fieldtype", "insert_after"])`
  and `list_docs("Property Setter", {"doc_type": "Sales Order"}, ["field_name", "property", "value"])`.

## Add a custom field (no code)

1. Pick `fieldname` (snake_case), `label`, `fieldtype`, `options` (for Link/Select), `insert_after` (an existing
   fieldname from the schema).
2. Show the plan and get a yes.
3. `create_draft("Custom Field", {"dt": "Sales Order", "fieldname": "delivery_slot", "label": "Delivery Slot", "fieldtype": "Select", "options": "Morning\nAfternoon\nEvening", "insert_after": "delivery_date"})`.
   Custom Field is not submittable, so the field is live once saved. ERPNext may prefix the fieldname with
   `custom_` — use the name returned. Tell the user to reload the form.
   The plugin refuses HTML fields and any `eval:` or `<script>` value; give those to the user to set by hand.

## Change a field property

`create_draft("Property Setter", {"doc_type": DT, "field_name": F, "doctype_or_field": "DocField", "property": "reqd", "value": "1", "property_type": "Check"})`.

## Server Script, Client Script, Report, Print Format, DocType, Role changes

These and other code-running or permission DocTypes are **blocked** for writes by the plugin's safety rules.
Write the code for the user instead, explain where to paste it (Desk → Server Script / Client Script) and what
to test. For larger changes recommend a custom Frappe app (`bench new-app`) and give file-by-file code.

## Rules

- Never touch core files of the `frappe` or `erpnext` apps; customizations go in Custom Field/Property Setter
  or a custom app.
- Prefer configuration over code.
