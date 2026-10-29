"""cPanel DNS publish — record mapping, add/delete/update planning, bulk ops."""
from kumo_automation.core import dnspublish as dp
from kumo_automation.core.models import DnsRecord


# --- record -> cPanel params ------------------------------------------------ #
def test_record_params_by_type():
    a = dp.record_params(DnsRecord(type="A", name="a.d.com", value="1.2.3.4"))
    assert a["name"] == "a.d.com." and a["address"] == "1.2.3.4" and a["class"] == "IN"

    txt = dp.record_params(DnsRecord(type="TXT", name="d.com", value="v=spf1 ip4:1.2.3.4 ~all"))
    assert txt["txtdata"] == "v=spf1 ip4:1.2.3.4 ~all" and "address" not in txt

    mx = dp.record_params(DnsRecord(type="MX", name="d.com", value="10 mail.d.com"))
    assert mx["priority"] == "10" and mx["exchange"] == "mail.d.com."


def test_creds_host_normalization():
    assert dp.CpanelCreds("srv.host.com", "u", "t").normalized_host() == "https://srv.host.com:2083"
    assert dp.CpanelCreds("https://x.com:2083", "u", "t").normalized_host() == "https://x.com:2083"


# --- add planning ----------------------------------------------------------- #
_EXISTING = [
    {"name": "a.d.com.", "type": "A", "value": "1.2.3.4", "line": 5},
    {"name": "d.com.", "type": "TXT", "value": "v=spf1 ip4:1.2.3.4 ~all", "line": 6},
]


def test_plan_add_already_todo_conflict():
    desired = [
        DnsRecord(type="A", name="a.d.com", value="1.2.3.4"),   # already
        DnsRecord(type="A", name="b.d.com", value="5.6.7.8"),   # new -> todo
        DnsRecord(type="A", name="a.d.com", value="9.9.9.9"),   # same name diff ip -> conflict
    ]
    todo, already, conflicts = dp.plan_add(_EXISTING, desired)
    assert [r.name for r in already] == ["a.d.com"]
    assert {r.name for r in todo} == {"b.d.com", "a.d.com"}
    assert conflicts and conflicts[0][0].value == "9.9.9.9"


def test_plan_add_txt_normalized_match():
    # spacing/quoting differences should still count as "already present"
    desired = [DnsRecord(type="TXT", name="d.com", value='v=spf1  ip4:1.2.3.4   ~all')]
    todo, already, _ = dp.plan_add(_EXISTING, desired)
    assert already and not todo


# --- delete planning -------------------------------------------------------- #
def test_plan_delete_by_range():
    existing = [
        {"name": "a.d.com.", "type": "A", "value": "69.4.94.5", "line": 1},
        {"name": "b.d.com.", "type": "A", "value": "8.8.8.8", "line": 2},
    ]
    matched = dp.plan_delete_by_range(existing, ["69.4.94.0/25"])
    assert len(matched) == 1 and matched[0]["value"] == "69.4.94.5"


def test_find_line():
    assert dp.find_line(_EXISTING, "a.d.com", "A") == 5
    assert dp.find_line(_EXISTING, "a.d.com", "A", "9.9.9.9") is None


# --- bulk ops with a fake client -------------------------------------------- #
class FakeClient:
    def __init__(self, existing):
        self.existing = existing
        self.added, self.removed, self.edited = [], [], []

    def fetch_zone(self, domain, rtype=None):
        return self.existing

    def add(self, domain, rec):
        self.added.append(rec)
        return True, "ok"

    def remove(self, domain, line):
        self.removed.append(line)
        return True, "ok"

    def edit(self, domain, line, rec):
        self.edited.append((line, rec))
        return True, "ok"


def test_publish_dry_run_then_apply():
    desired = [DnsRecord(type="A", name="b.d.com", value="5.6.7.8"),
               DnsRecord(type="A", name="a.d.com", value="1.2.3.4")]   # already
    c = FakeClient(list(_EXISTING))
    dry = dp.publish(c, "d.com", desired, dry_run=True, log=lambda *_: None)
    assert dry["todo"] == 1 and dry["already"] == 1 and not c.added
    live = dp.publish(c, "d.com", desired, dry_run=False, log=lambda *_: None)
    assert live["added"] == 1 and len(c.added) == 1 and c.added[0].name == "b.d.com"


def test_publish_skips_ptr():
    desired = [DnsRecord(type="PTR", name="1.2.3.4", value="a.d.com"),
               DnsRecord(type="A", name="z.d.com", value="7.7.7.7")]
    c = FakeClient([])
    res = dp.publish(c, "d.com", desired, dry_run=False, log=lambda *_: None)
    assert res["added"] == 1 and all(r.name != "1.2.3.4" for r in c.added)   # PTR not sent


def test_delete_range_confirm_descending_lines():
    existing = [
        {"name": "a.d.com.", "type": "A", "value": "69.4.94.5", "line": 3},
        {"name": "b.d.com.", "type": "A", "value": "69.4.94.6", "line": 7},
        {"name": "c.d.com.", "type": "A", "value": "8.8.8.8", "line": 9},
    ]
    c = FakeClient(existing)
    res = dp.delete_range(c, "d.com", ["69.4.94.0/25"], confirm=True, log=lambda *_: None)
    assert res["deleted"] == 2
    assert c.removed == [7, 3]        # descending line order


def test_update_edits_in_place():
    c = FakeClient(list(_EXISTING))
    res = dp.update_record(c, "d.com", DnsRecord(type="A", name="a.d.com", value="9.9.9.9"),
                           dry_run=False, log=lambda *_: None)
    assert res["ok"] and c.edited and c.edited[0][0] == 5 and c.edited[0][1].value == "9.9.9.9"


def test_verify_counts_published_and_missing():
    desired = [DnsRecord(type="A", name="a.d.com", value="1.2.3.4"),   # published
               DnsRecord(type="A", name="new.d.com", value="2.2.2.2")]  # missing
    res = dp.verify(FakeClient(list(_EXISTING)), "d.com", desired)
    assert res["published"] == 1 and res["missing"] == 1


# --- Excel intake + domain grouping + batch publish ------------------------- #
def test_domain_of():
    assert dp.domain_of("host.d.com") == "d.com"
    assert dp.domain_of("_dmarc.d.com") == "d.com"
    assert dp.domain_of("s1._domainkey.d.com") == "d.com"
    # known-domain match wins (handles deeper names)
    assert dp.domain_of("a.b.shop.co", known=["shop.co"]) == "shop.co"


def test_group_by_domain():
    recs = [DnsRecord(type="A", name="h1.d1.com", value="1.1.1.1"),
            DnsRecord(type="TXT", name="d1.com", value="v=spf1 ~all"),
            DnsRecord(type="A", name="h2.d2.com", value="2.2.2.2")]
    g = dp.group_by_domain(recs)
    assert set(g) == {"d1.com", "d2.com"} and len(g["d1.com"]) == 2


def test_parse_download_center_xlsx_roundtrip(tmp_path):
    # build a sheet in the app's Download-Center layout, then parse it back
    from kumo_automation.core import dnsgen
    recs = [DnsRecord(type="A", name="h1.d1.com", value="1.1.1.1"),
            DnsRecord(type="TXT", name="d1.com", value="v=spf1 ip4:1.1.1.1 ~all")]
    path = tmp_path / "dns-records.xlsx"
    dnsgen.records_to_xlsx(recs, path)
    parsed = dp.parse_records_xlsx(str(path))
    assert len(parsed) == 2
    a = next(r for r in parsed if r.type == "A")
    assert a.name == "h1.d1.com" and a.value == "1.1.1.1"
    txt = next(r for r in parsed if r.type == "TXT")
    assert txt.value == "v=spf1 ip4:1.1.1.1 ~all"


def test_publish_many_routes_and_flags_unrouted():
    d1 = FakeClient([]); d2 = FakeClient([])
    routes = {"d1.com": d1}                      # d2.com has no account
    recs = [DnsRecord(type="A", name="h.d1.com", value="1.1.1.1"),
            DnsRecord(type="A", name="h.d2.com", value="2.2.2.2"),
            DnsRecord(type="PTR", name="1.1.1.1", value="h.d1.com")]  # PTR skipped
    res = dp.publish_many(routes, recs, dry_run=False, log=lambda *_: None)
    assert res["unrouted"] == ["d2.com"]
    assert res["results"]["d1.com"]["added"] == 1
    assert len(d1.added) == 1 and d1.added[0].name == "h.d1.com"


def test_no_mx_generated(monkeypatch):
    from kumo_automation.core import dnsgen
    from kumo_automation.core.models import Environment, IPEntry, ServerProfile, SSHTarget
    monkeypatch.setattr(dnsgen, "existing_spf", lambda d: None)   # avoid live DNS
    p = ServerProfile(name="s", environment=Environment.production, ssh=SSHTarget(host="1.1.1.1"),
                      ips=[IPEntry(ip="1.2.3.4", hostname="a.d.com", username="a", domain="d.com")])
    recs, _ = dnsgen.generate_records(p)
    assert all(r.type != "MX" for r in recs)      # MX no longer generated
