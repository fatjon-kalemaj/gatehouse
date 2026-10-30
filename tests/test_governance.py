from gatehouse.governance import Governance
from gatehouse.ledger import Ledger


def gov(repo):
    return Governance.from_ledger(Ledger(repo))


def decide(g, user="maya.patel@example.com", text="clean input", size=1e8, runs_today=0, ticket=None):
    return g.authorize(user, "deal-intake", 6.0, {"deal_size_usd": size}, text, runs_today, ticket)


def test_allowlisted_user_is_allowed(ledger_repo):
    d = decide(gov(ledger_repo))
    assert d.allowed and d.groups == ["deal-team"] and d.max_cost_usd == 6.0


def test_unknown_user_is_denied(ledger_repo):
    d = decide(gov(ledger_repo), user="stranger@example.com")
    assert d.status == "denied"
    assert [v.code for v in d.violations] == ["access.not_allowlisted"]


def test_daily_quota(ledger_repo):
    d = decide(gov(ledger_repo), user="noah.kim@example.com", runs_today=6)
    assert d.status == "denied" and d.violations[0].code == "quota.daily_runs"


def test_input_screening_blocks_and_never_logs_the_value(ledger_repo):
    d = decide(gov(ledger_repo), text="Owner SSN 123-45-6789 attached")
    assert d.status == "denied"
    assert d.violations[0].code == "pii.ssn"
    assert "123-45-6789" not in d.violations[0].message


def test_restricted_list_and_mnpi(ledger_repo):
    g = gov(ledger_repo)
    assert decide(g, text="re: Project Halcyon teaser").violations[0].code == "restricted.list"
    assert decide(g, text="contains MNPI from the CFO").violations[0].code == "mnpi.marker"


def test_large_deal_needs_a_ticket(ledger_repo):
    g = gov(ledger_repo)
    assert decide(g, size=1.2e9).status == "pending_approval"
    assert decide(g, size=1.2e9, ticket="APR-1").allowed
