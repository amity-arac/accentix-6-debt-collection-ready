"""session_init: the response IS the context — no mapping layer."""
from demo.server.flow.session_init import audit_placeholders, fetch_context, flatten, substitute


def test_flat_response_passes_through():
    got = flatten({"doctor_name": "นพ.ก", "appointment_time": "10:00"})
    assert got == {"doctor_name": "นพ.ก", "appointment_time": "10:00"}


def test_nested_reachable_by_dotted_and_bare_name():
    got = flatten({"appointment": {"doctor": "นพ.ก", "date": "2026-05-20 (Wednesday)"}})
    assert got["appointment.doctor"] == "นพ.ก"
    assert got["doctor"] == "นพ.ก"          # bare leaf = why no `map` is needed


def test_toplevel_wins_over_deeper_leaf():
    # A top-level field must never be shadowed by something buried deeper, or a
    # nested `customer.name` would silently overwrite the real `name`.
    got = flatten({"name": "top", "customer": {"name": "deep"}})
    assert got["name"] == "top"
    assert got["customer.name"] == "deep"


def test_list_survives_whole():
    got = flatten({"slots": ["09:00", "10:00"]})
    assert got["slots"] == ["09:00", "10:00"]


def test_no_session_init_declared_is_a_noop():
    r = fetch_context({}, {"msisdn": "081"})
    assert r == {"declared": False, "ok": False, "data": {}, "error": None}


def test_unreachable_crm_reports_but_does_not_raise():
    # A live call must survive someone else's outage: port 9 discards silently.
    r = fetch_context({"session_init": {"url": "http://127.0.0.1:9/x", "timeout": 0.2}}, {})
    assert r["declared"] is True and r["ok"] is False and r["error"]


def test_tokens_filled_from_seed_and_kept_when_absent():
    assert substitute("/case/{msisdn}", {"msisdn": "081"}) == "/case/081"
    assert substitute("/case/{nope}", {}) == "/case/{nope}"   # visible, not blanked


def test_audit_names_only_unfillable_placeholders():
    missing = audit_placeholders(
        ["พบ [doctor_name] วันที่ [appointment_date]"],
        {"doctor_name": "นพ.ก"},
    )
    assert missing == ["appointment_date"]


def test_audit_flags_a_registry_slot_whose_field_is_absent():
    # The regression this exists for: [company_phone] IS a known SYSTEM placeholder,
    # so an audit that trusts the registry called it fine — and the agent read
    # "ติดต่อได้ที่ [company_phone]" to a customer. Resolvable means the DATA is there.
    assert audit_placeholders(["ติดต่อ [company_phone]"], {}) == ["company_phone"]
    assert audit_placeholders(["ติดต่อ [company_phone]"], {"company_phone": "02-1"}) == []


def test_audit_treats_model_supplied_dynamic_vars_as_covered():
    # fill_template degrades an absent dynamic var to a safe Thai phrase, so it can
    # never be spoken as a raw token — not something to report.
    assert audit_placeholders(["นัดชำระ [promised_date]"], {}) == []


def test_audit_known_overrides_the_computed_set():
    assert audit_placeholders(["[aa] [bb]"], {"aa": 1}, known={"bb"}) == ["aa"]


def test_audit_ignores_single_char_doc_references():
    # [A]/[B] point at the catalog's category prefixes in the v6 instructions;
    # they are documentation, not slots, so the two-char floor in the regex is
    # load-bearing rather than incidental.
    assert audit_placeholders(["หมวด [A] และ [B]"], {}) == []
