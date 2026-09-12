#!/usr/bin/env python3
"""Generate the gold set (fact patterns, Q/A pairs, conflict set).

Expected values are *computed here from the clause parameters*, not typed in by hand:
the guide's "number drift" failure mode is that a corpus claims one number and the
Catala module executes another. Every money/decimal expectation below is an expression
over the same constants the clauses state, so a wrong constant fails visibly in one
place. Re-run after editing a document that carries a rule.
"""
import json
from datetime import date, timedelta
from pathlib import Path

GOLD = Path(__file__).resolve().parent.parent / "gold"

# --- clause constants, transcribed once, per document -----------------------------
NW_ANNUAL_FEES = 240_000          # SYN-003 cover table
NW_MONTHLY_FEE = 20_000           # SYN-003 cover table
MSA_CAP_MULT, MSA_EXCL_MULT = 1.0, 2.0            # SYN-001 s 9.2 / s 9.3
OF_CAP_MULT, OF_EXCL_MULT = 3.0, 5.0              # SYN-003 s 5.1 / s 5.2
STD_TIERS = [(99.9, 0.10), (99.0, 0.25), (95.0, 1.00)]   # SYN-002 s A.3
ENT_TIERS = [(99.9, 0.15), (99.0, 0.30), (95.0, 1.00)]   # SYN-003 s 4.2
GBX_MONTHLY_FEE = 30_000          # SYN-009 s 1.2
HELIOS_MULT = 1.5                 # SYN-008 s 8.1
PIN_RATE, PIN_MAX_HOURS = 225, 500                # SYN-013 s 3.1 / s 3.2
PIN_MAX = PIN_RATE * PIN_MAX_HOURS                # s 3.2 states $112,500
PTO_FACTOR = {2: 0.0961, 7: 0.1154, 12: 0.1346}   # SYN-005 s 4.1.2 by years of service
HANDBOOK_SICK_DIVISOR = 40        # SYN-005 s 4.2.1
CA_SICK_DIVISOR, CA_ACCRUAL_CAP, CA_USE_CAP = 30, 80, 40   # Cal. Lab. Code s 246
CO_SICK_DIVISOR, CO_CAP = 30, 48                  # C.R.S. s 8-13.3-403
DXC_BASE, DXC_BONUS_RATE = 800_000, 1.35          # EDGAR-002 paras 2-3
REYES_BASE, REYES_BONUS_RATE = 165_000, 0.15      # SYN-014 s 2.2
TUITION_CAP = 5_250                               # REAL-009 / 26 U.S.C. s 127

assert PIN_MAX == 112_500, "SYN-013 s 3.2 states $112,500"
assert abs(40 * PTO_FACTOR[2] * 52 - 200) < 0.2, "SYN-005 s 4.1.2 claims ~200 h/year"


def usd(x):
    return f"${x:,.2f}"


def credit(tiers, uptime):
    """First tier whose lower bound the uptime falls below; 0 if the commitment is met."""
    for i, (bound, pct) in enumerate(tiers):
        lower = tiers[i + 1][0] if i + 1 < len(tiers) else 0.0
        if uptime < bound and (uptime >= lower or i == len(tiers) - 1):
            return pct
    return 0.0


FP = []


def fp(module, inputs, expected, branch, clauses, docs):
    FP.append({"id": f"FP-{len(FP) + 1:03d}", "module": module, "inputs": inputs,
               "expected": expected, "branch": branch, "clause_refs": clauses, "docs": docs})


# --- LiabilityCap ------------------------------------------------------------------
fp("LiabilityCap_AcmeNorthwind",
   {"claim_date": "2026-03-01", "fees_paid_last_12mo": usd(120_000),
    "annual_fees": usd(NW_ANNUAL_FEES), "claim_type": "Ordinary"},
   {"cap": usd(120_000 * MSA_CAP_MULT), "uncapped": False},
   "base definition (MSA s 9.2) — claim predates the Order Form",
   ["SYN-001 s 9.2"], ["SYN-001"])
fp("LiabilityCap_AcmeNorthwind",
   {"claim_date": "2026-03-01", "fees_paid_last_12mo": usd(120_000),
    "annual_fees": usd(NW_ANNUAL_FEES), "claim_type": "Confidentiality"},
   {"cap": usd(120_000 * MSA_EXCL_MULT), "uncapped": False},
   "exception: Excluded Claims (MSA s 9.3)", ["SYN-001 s 9.3"], ["SYN-001"])
fp("LiabilityCap_AcmeNorthwind",
   {"claim_date": "2026-09-15", "fees_paid_last_12mo": usd(NW_ANNUAL_FEES),
    "annual_fees": usd(NW_ANNUAL_FEES), "claim_type": "Ordinary"},
   {"cap": usd(NW_ANNUAL_FEES * OF_CAP_MULT), "uncapped": False},
   "exception: Order Form s 5.1 controls under MSA s 14.3",
   ["SYN-003 s 5.1", "SYN-001 s 14.3"], ["SYN-001", "SYN-003"])
fp("LiabilityCap_AcmeNorthwind",
   {"claim_date": "2026-09-15", "fees_paid_last_12mo": usd(NW_ANNUAL_FEES),
    "annual_fees": usd(NW_ANNUAL_FEES), "claim_type": "IpIndemnity"},
   {"cap": usd(NW_ANNUAL_FEES * OF_EXCL_MULT), "uncapped": False},
   "exception to the exception: Order Form s 5.2 (Excluded Claims supercap)",
   ["SYN-003 s 5.2"], ["SYN-001", "SYN-003"])
fp("LiabilityCap_AcmeNorthwind",
   {"claim_date": "2026-09-15", "fees_paid_last_12mo": usd(NW_ANNUAL_FEES),
    "annual_fees": usd(NW_ANNUAL_FEES), "claim_type": "GrossNegligence"},
   {"cap": None, "uncapped": True},
   "exception: MSA s 9.4 carve-out, preserved by Order Form s 5.3",
   ["SYN-001 s 9.4", "SYN-003 s 5.3"], ["SYN-001", "SYN-003"])
fp("LiabilityCap_AcmeHelios",
   {"fees_paid_last_12mo": usd(80_000), "claim_type": "Ordinary"},
   {"cap": usd(80_000 * HELIOS_MULT), "uncapped": False},
   "base definition (Helios MSA s 8.1) — a different agreement, not a conflict",
   ["SYN-008 s 8.1"], ["SYN-008"])
fp("LiabilityCap_AcmeHelios",
   {"fees_paid_last_12mo": usd(80_000), "claim_type": "UnpaidFees"},
   {"cap": None, "uncapped": True},
   "exception: Helios MSA s 8.2 carve-out", ["SYN-008 s 8.2"], ["SYN-008"])
fp("LiabilityCap_PinnacleAcme",
   {"sow_fees_paid_or_payable": usd(320 * PIN_RATE), "claim_type": "Ordinary"},
   {"cap": usd(320 * PIN_RATE), "uncapped": False},
   "base definition (SOW s 6.1)", ["SYN-013 s 6.1"], ["SYN-013"])
fp("LiabilityCap_PinnacleAcme",
   {"sow_fees_paid_or_payable": usd(320 * PIN_RATE), "claim_type": "Confidentiality"},
   {"cap": None, "uncapped": True},
   "exception: SOW s 6.2 carve-out (confidentiality, gross negligence, wilful misconduct)",
   ["SYN-013 s 6.2"], ["SYN-013"])
fp("LiabilityCap_InhibitorMsa",
   {"sow_fees_paid_or_payable": usd(45_000), "claim_type": "Ordinary"},
   {"cap": usd(45_000), "uncapped": False},
   "base definition (real EDGAR MSA s 16 — fees paid or payable under the relevant SOW)",
   ["EDGAR-001 s 16"], ["EDGAR-001"])
fp("LiabilityCap_InhibitorMsa",
   {"sow_fees_paid_or_payable": usd(45_000), "claim_type": "IpIndemnity"},
   {"cap": None, "uncapped": True},
   "exception: s 16 carves the s 15 indemnity obligations out of the cap",
   ["EDGAR-001 s 16", "EDGAR-001 s 15"], ["EDGAR-001"])

# --- ServiceCredit ----------------------------------------------------------------
for month, uptime, tiers, why, docs in [
    ("2026-03-01", 99.95, STD_TIERS, "commitment met — no credit", ["SYN-002"]),
    ("2026-03-01", 99.50, STD_TIERS, "base tier table (Exhibit A s A.3); Order Form not yet effective", ["SYN-002"]),
    ("2026-03-01", 98.70, STD_TIERS, "base tier table, second band", ["SYN-002"]),
    ("2026-08-01", 99.50, ENT_TIERS, "exception: Order Form s 4.2 replaces the tier table", ["SYN-002", "SYN-003"]),
    ("2026-08-01", 99.00, ENT_TIERS, "exception: Order Form s 4.2, boundary value (>= 99.0%)", ["SYN-002", "SYN-003"]),
    ("2026-08-01", 98.70, ENT_TIERS, "exception: Order Form s 4.2, second band", ["SYN-002", "SYN-003"]),
    ("2026-08-01", 94.00, ENT_TIERS, "exception: Order Form s 4.2, floor band (100% of monthly fee)", ["SYN-002", "SYN-003"]),
]:
    pct = credit(tiers, uptime)
    fp("ServiceCredit_AcmeNorthwind",
       {"month": month, "monthly_uptime": uptime, "monthly_service_fee": usd(NW_MONTHLY_FEE),
        "request_date": (date.fromisoformat(month) + timedelta(days=45)).isoformat()},
       {"credit_percentage": f"{pct:.0%}", "credit": usd(NW_MONTHLY_FEE * pct), "eligible": True},
       why, ["SYN-002 s A.3"] + (["SYN-003 s 4.2"] if tiers is ENT_TIERS else []), docs)
fp("ServiceCredit_AcmeNorthwind",
   {"month": "2026-08-01", "monthly_uptime": 98.70, "monthly_service_fee": usd(NW_MONTHLY_FEE),
    "request_date": "2026-10-15"},
   {"credit_percentage": "0%", "credit": usd(0), "eligible": False},
   "eligibility gate: request more than 30 days after month end (Exhibit A s A.4.1)",
   ["SYN-002 s A.4.1"], ["SYN-002", "SYN-003"])

# The Globex module is expected NOT to execute: two live definitions, no priority.
for uptime, va, vb in [(99.4, "10%", "20%"), (98.5, "25%", "40%")]:
    fp("ServiceCredit_AcmeGlobex",
       {"month": "2026-07-01", "monthly_uptime": uptime,
        "monthly_service_fee": usd(GBX_MONTHLY_FEE), "request_date": "2026-08-05"},
       {"error": "conflicting definitions", "candidates": [va, vb]},
       f"no single definition applies: SYN-009 s 2.1 gives {va} and SYN-010 s 2.1 gives {vb}, "
       "and SYN-010 s 4.1 refuses an order of precedence",
       ["SYN-009 s 2.1", "SYN-010 s 2.1", "SYN-010 s 4.1"], ["SYN-009", "SYN-010"])

# --- SickLeaveAccrual -------------------------------------------------------------
for hours, weeks, st, days, exempt, divisor, cap, why in [
    (1200, 30, "WA", 365, False, HANDBOOK_SICK_DIVISOR, None, "base definition (handbook s 4.2.1); WA statute states the same 1:40 ratio"),
    (1000, 25, "TX", 365, False, HANDBOOK_SICK_DIVISOR, None, "base definition (handbook s 4.2.1); no state accrual statute"),
    (1200, 30, "CA", 365, False, CA_SICK_DIVISOR, CA_ACCRUAL_CAP, "exception: Cal. Lab. Code s 246(b)(1) floor, via handbook s 4.6"),
    (1800, 45, "CA", 365, False, CA_SICK_DIVISOR, CA_ACCRUAL_CAP, "exception: Cal. Lab. Code s 246(b)(1)"),
    (3000, 75, "CA", 365, False, CA_SICK_DIVISOR, CA_ACCRUAL_CAP, "exception + accrual ceiling: s 246(j) 80-hour cap binds"),
    (1200, 30, "CO", 365, False, CO_SICK_DIVISOR, CO_CAP, "exception: C.R.S. s 8-13.3-403 1:30 floor"),
    (3000, 75, "CO", 365, False, CO_SICK_DIVISOR, CO_CAP, "exception + 48-hour Colorado cap binds"),
    (600, 15, "CA", 45, False, CA_SICK_DIVISOR, CA_ACCRUAL_CAP, "accrues from day one but not usable before day 90 (s 246(c))"),
    (840, 21, "CA", 200, True, CA_SICK_DIVISOR, CA_ACCRUAL_CAP, "exempt employee deemed to work 40 h/week (s 246(b)(2)); 21 weeks x 40 h"),
]:
    accrued = min(hours / divisor, cap) if cap else hours / divisor
    use_cap = CA_USE_CAP if st == "CA" else (CO_CAP if st == "CO" else 40)
    fp("SickLeaveAccrual_Acme",
       {"hours_worked": hours, "weeks_worked": weeks, "work_state": st,
        "days_employed": days, "exempt": exempt},
       {"accrual_rate": f"1/{divisor}", "accrual_divisor": float(divisor),
        "accrued_hours": round(float(accrued), 4),
        "annual_use_cap": float(use_cap), "usable": days >= 90},
       why, ["SYN-005 s 4.2", "SYN-005 s 4.6"] +
       (["REAL-001 s 246(b)(1)"] if st == "CA" else ["REAL-002"] if st == "CO" else []),
       ["SYN-005"] + (["REAL-001"] if st == "CA" else ["REAL-002"] if st == "CO" else
                      ["REAL-006"] if st == "WA" else []))

# --- PtoAccrual -------------------------------------------------------------------
for hours, yrs, period, why in [
    (40, 2, "week", "base definition (handbook s 4.1.2), under 5 years of service"),
    (48, 2, "week", "hours above 40 in a workweek are not counted (s 4.1.1)"),
    (40, 7, "week", "exception: 5-9 years of service factor"),
    (40, 12, "week", "exception: 10+ years of service factor"),
    (2080, 2, "year", "full year of 40-hour weeks — the ~200 hour figure in the table"),
]:
    counted = min(hours, 40) if period == "week" else hours   # the 40-hour cap is weekly
    fp("PtoAccrual_Acme",
       {"hours_worked_in_period": hours, "period": period, "years_of_service": yrs,
        "balance_at_year_end": None},
       {"accrued_hours": round(counted * PTO_FACTOR[yrs], 4)},
       why, ["SYN-005 s 4.1.1", "SYN-005 s 4.1.2"], ["SYN-005", "REAL-009"])
fp("PtoAccrual_Acme",
   {"hours_worked_in_period": 0, "period": "year", "years_of_service": 2,
    "balance_at_year_end": 320},
   {"carryover_hours": 300.0, "forfeited_hours": 20.0},
   "carryover ceiling: 1.5 x approximate annual accrual (s 4.1.4)",
   ["SYN-005 s 4.1.4"], ["SYN-005"])

# --- ExpenseApproval --------------------------------------------------------------
CORP = [(1_000, "Manager"), (5_000, "Director"), (25_000, "VicePresident"), (None, "ChiefFinancialOfficer")]
ENG = [(2_500, "Manager"), (None, "Director")]
RANK = ["Manager", "Director", "VicePresident", "ChiefFinancialOfficer"]


def level(table, amount):
    for bound, lv in table:
        if bound is None or amount <= bound:
            return lv


for amount, dept, why in [
    (800, "Engineering", "both policies agree"),
    (1_000, "Sales", "base definition, boundary value ($1,000 or less)"),
    (2_000, "Engineering", "handbook s 7.1: corporate Director is more restrictive than department Manager"),
    (2_500, "Engineering", "boundary: corporate Director beats department Manager"),
    (6_000, "Engineering", "handbook s 7.1: corporate VP is more restrictive than department Director"),
    (30_000, "Engineering", "handbook s 7.1: corporate CFO controls"),
    (6_000, "Sales", "base definition — no department policy for Sales"),
    (50, "Marketing", "below the $75 receipt threshold (SYN-006 s 3.1)"),
]:
    corp = level(CORP, amount)
    dept_lv = level(ENG, amount) if dept == "Engineering" else None
    strictest = max([corp] + ([dept_lv] if dept_lv else []), key=RANK.index)
    fp("ExpenseApproval_Acme", {"amount": usd(amount), "department": dept},
       {"approval_level": strictest, "receipt_required": amount > 75}, why,
       ["SYN-006 s 2.1"] + (["SYN-007 s 2.1", "SYN-005 s 7.1"] if dept == "Engineering" else []),
       ["SYN-006"] + (["SYN-007", "SYN-005"] if dept == "Engineering" else []))

# --- FinalPaycheckDeadline --------------------------------------------------------
LAST_DAY, NEXT_PAYROLL = date(2026, 3, 10), date(2026, 3, 20)
for sep, st, due, payout, why in [
    ("InvoluntaryTermination", "CA", LAST_DAY, True, "exception: Cal. Lab. Code s 201 — due immediately, via handbook s 9.5"),
    ("QuitWithoutNotice", "CA", LAST_DAY + timedelta(days=3), True, "exception: Cal. Lab. Code s 202 — within 72 hours"),
    ("QuitWithNotice", "CA", LAST_DAY, True, "exception: s 202 — 72+ hours notice means wages are due at quitting"),
    ("InvoluntaryTermination", "CO", LAST_DAY, True, "exception: C.R.S. s 8-4-109 — due immediately"),
    ("InvoluntaryTermination", "TX", NEXT_PAYROLL, False, "base definition (handbook s 9.3) — no stricter state rule"),
    ("QuitWithoutNotice", "WA", NEXT_PAYROLL, False, "base definition (handbook s 9.3) — WA requires next regular payday"),
]:
    fp("FinalPaycheckDeadline_Acme",
       {"last_day": LAST_DAY.isoformat(), "separation": sep, "work_state": st,
        "next_regular_payroll_date": NEXT_PAYROLL.isoformat()},
       {"due_date": due.isoformat(), "pto_payout_required": payout}, why,
       ["SYN-005 s 9.3", "SYN-005 s 9.5"] +
       (["REAL-003 s 201", "REAL-005 s 227.3"] if st == "CA" and sep == "InvoluntaryTermination" else
        ["REAL-004 s 202", "REAL-005 s 227.3"] if st == "CA" else ["REAL-002"] if st == "CO" else []),
       ["SYN-005"] + (["REAL-003", "REAL-004", "REAL-005"] if st == "CA" else
                      ["REAL-002"] if st == "CO" else []))

# --- ConsultingFees ---------------------------------------------------------------
for hours, invoice_date, month, why in [
    (320, "2026-04-08", "2026-04-01", "base definition (s 3.1) — under the 80% notice threshold"),
    (420, "2026-06-09", "2026-06-01", "80% notice threshold crossed (s 3.3)"),
    (520, "2026-07-08", "2026-07-01", "ceiling: fees above $112,500 need a change order (s 3.2)"),
]:
    fees = hours * PIN_RATE
    fp("ConsultingFees_PinnacleAcme",
       {"hours": hours, "month_of_service": month, "invoice_date": invoice_date},
       {"fees": usd(fees), "fees_payable": usd(min(fees, PIN_MAX)),
        "change_order_required": fees > PIN_MAX,
        "eighty_percent_notice_required": fees >= 0.8 * PIN_MAX, "invoice_payable": True},
       why, ["SYN-013 s 3.1", "SYN-013 s 3.2", "SYN-013 s 3.3"], ["SYN-013"])
fp("ConsultingFees_PinnacleAcme",
   {"hours": 100, "month_of_service": "2026-04-01", "invoice_date": "2026-08-15"},
   {"fees": usd(100 * PIN_RATE), "fees_payable": usd(0), "invoice_payable": False},
   "eligibility gate: invoiced more than 90 days after month end (s 5.3)",
   ["SYN-013 s 5.3"], ["SYN-013"])

# --- smaller modules --------------------------------------------------------------
fp("ConfidentialityDuration",
   {"disclosure_date": "2026-06-01", "agreement": "NorthwindMsa", "is_trade_secret": False},
   {"obligation_end": "2029-06-01", "indefinite": False},
   "base definition (MSA s 11.2) — 3 years from disclosure", ["SYN-001 s 11.2"], ["SYN-001"])
fp("ConfidentialityDuration",
   {"disclosure_date": "2026-06-01", "agreement": "VertexNda", "is_trade_secret": False},
   {"obligation_end": "2031-06-01", "indefinite": False},
   "exception: the Vertex NDA s 4.2 sets 5 years", ["SYN-011 s 4.2"], ["SYN-011"])
fp("ConfidentialityDuration",
   {"disclosure_date": "2026-06-01", "agreement": "NorthwindMsa", "is_trade_secret": True},
   {"obligation_end": "2029-06-01", "indefinite": True},
   "exception: trade secrets survive indefinitely (s 11.2)", ["SYN-001 s 11.2"], ["SYN-001"])
fp("BreachNotice_AcmeNorthwind", {"awareness_date": "2026-05-04"},
   {"notify_by": "2026-05-07"}, "base definition (DPA s 5.1) — 72 hours",
   ["SYN-012 s 5.1"], ["SYN-012"])
fp("IncentiveComp_Acme",
   {"annual_base_salary": usd(REYES_BASE), "target_bonus_rate": f"{REYES_BONUS_RATE:.0%}"},
   {"target_bonus": usd(REYES_BASE * REYES_BONUS_RATE)},
   "base definition (offer letter s 2.2)", ["SYN-014 s 2.2"], ["SYN-014"])
fp("IncentiveComp_DxcSideLetter",
   {"annual_base_salary": usd(DXC_BASE), "target_bonus_rate": f"{DXC_BONUS_RATE:.0%}"},
   {"target_bonus": usd(DXC_BASE * DXC_BONUS_RATE)},
   "base definition (real EDGAR side letter, paras 2-3)", ["EDGAR-002 para 2", "EDGAR-002 para 3"], ["EDGAR-002"])
for hours, rate, why in [(46, 30, "overtime band: 1.5x over 40 hours"), (38, 30, "no overtime under 40 hours")]:
    ot = max(hours - 40, 0)
    fp("Overtime_Flsa", {"hours_worked_in_week": hours, "regular_rate": usd(rate)},
       {"overtime_pay": usd(ot * rate * 1.5), "total_pay": usd(min(hours, 40) * rate + ot * rate * 1.5)},
       why, ["REAL-007 s 207(a)(1)"], ["REAL-007"])
for cost, grade, lvl, ok, why in [
    (6_000, "B", "graduate", True, "ceiling: statutory $5,250 exclusion caps the reimbursement"),
    (3_000, "C", "undergraduate", True, "base definition — cost below the cap"),
    (3_000, "C", "graduate", False, "grade gate: graduate coursework requires B or better"),
]:
    fp("TuitionReimbursement_Reference", {"eligible_cost": usd(cost), "grade": grade, "level": lvl},
       {"reimbursement": usd(min(cost, TUITION_CAP) if ok else 0), "eligible": ok},
       why, ["REAL-009"], ["REAL-009"])

GOLD.mkdir(exist_ok=True)
(GOLD / "fact_patterns.jsonl").write_text("".join(json.dumps(r) + "\n" for r in FP))
print(f"fact_patterns.jsonl: {len(FP)} patterns over "
      f"{len({r['module'] for r in FP})} modules")

# ==================================================================================
# Q/A pairs -- the accuracy metric. `engine` is the label the chat component must
# print; `expected_missing_inputs` marks the elicitation cases, where the correct
# behaviour is to ask, not to guess.
# ==================================================================================
QA = []


def qa(question, engine, expected, **kw):
    QA.append({"id": f"QA-{len(QA) + 1:03d}", "question": question, "engine": engine,
               "expected_answer": expected, **kw})


qa("Northwind's platform was up 98.7% in August 2026 and they pay $20,000 a month. "
   "What service credit do they get?", "catala", usd(NW_MONTHLY_FEE * 0.30),
   module="ServiceCredit_AcmeNorthwind",
   inputs={"month": "2026-08-01", "monthly_uptime": 98.7,
           "monthly_service_fee": usd(NW_MONTHLY_FEE), "request_date": "2026-09-05"},
   expected_branch="Order Form s 4.2 replaces the Exhibit A tier table (30%, not 25%)",
   must_cite=["SYN-003 s 4.2", "SYN-001 s 14.3"], docs=["SYN-002", "SYN-003"])
qa("What is our liability cap if Northwind brings an ordinary breach claim in September 2026?",
   "catala", usd(NW_ANNUAL_FEES * OF_CAP_MULT), module="LiabilityCap_AcmeNorthwind",
   inputs={"claim_date": "2026-09-15", "annual_fees": usd(NW_ANNUAL_FEES),
           "fees_paid_last_12mo": usd(NW_ANNUAL_FEES), "claim_type": "Ordinary"},
   expected_branch="Order Form s 5.1 (300% of annual fees) as an exception to MSA s 9.2",
   must_cite=["SYN-003 s 5.1", "SYN-001 s 9.2", "SYN-001 s 14.3"], docs=["SYN-001", "SYN-003"],
   notes="Amendment No. 1 ($500,000) also defines this variable; the answer is only "
         "available after the SYN-003/SYN-004 conflict is resolved by the parties.")
qa("And if the same claim is for breach of confidentiality?", "catala",
   usd(NW_ANNUAL_FEES * OF_EXCL_MULT), module="LiabilityCap_AcmeNorthwind",
   inputs={"claim_date": "2026-09-15", "annual_fees": usd(NW_ANNUAL_FEES),
           "fees_paid_last_12mo": usd(NW_ANNUAL_FEES), "claim_type": "Confidentiality"},
   expected_branch="Order Form s 5.2 supercap (500%) — exception to the Excluded Claims exception",
   must_cite=["SYN-003 s 5.2", "SYN-001 s 9.3"], docs=["SYN-001", "SYN-003"])
qa("Dana Reyes works out of San Francisco and has worked 1,200 hours so far this year. "
   "How much paid sick leave has she accrued?", "catala", "40 hours",
   module="SickLeaveAccrual_Acme",
   inputs={"hours_worked": 1200, "weeks_worked": 30, "work_state": "CA",
           "days_employed": 365, "exempt": True},
   expected_branch="Cal. Lab. Code s 246(b)(1) 1:30 floor displaces the handbook's 1:40, "
                   "via handbook s 4.6",
   must_cite=["REAL-001 s 246(b)(1)", "SYN-005 s 4.2.1", "SYN-005 s 4.6"],
   docs=["SYN-005", "SYN-014", "REAL-001"],
   notes="Handbook alone would give 30 hours. The 10-hour gap is the compliance bug.")
qa("An engineer wants to buy $6,000 of test hardware. Who has to approve it?", "catala",
   "Vice President", module="ExpenseApproval_Acme",
   inputs={"amount": usd(6000), "department": "Engineering"},
   expected_branch="handbook s 7.1 — corporate VP threshold is more restrictive than the "
                   "department's Director threshold",
   must_cite=["SYN-006 s 2.1", "SYN-007 s 2.1", "SYN-005 s 7.1"], docs=["SYN-005", "SYN-006", "SYN-007"])
qa("We are terminating a California employee on 10 March 2026. When must final wages be paid, "
   "and do we owe accrued PTO?", "catala", "2026-03-10; accrued PTO must be paid out",
   module="FinalPaycheckDeadline_Acme",
   inputs={"last_day": "2026-03-10", "separation": "InvoluntaryTermination",
           "work_state": "CA", "next_regular_payroll_date": "2026-03-20"},
   expected_branch="Cal. Lab. Code s 201 (immediately) and s 227.3 (vacation is wages) "
                   "displace handbook s 9.3 and s 9.4, via s 9.5",
   must_cite=["REAL-003 s 201", "REAL-005 s 227.3", "SYN-005 s 9.3"],
   docs=["SYN-005", "REAL-003", "REAL-005"])
qa("Pinnacle has billed 520 hours on SOW No. 2. How much of that can we pay?", "catala",
   f"{usd(PIN_MAX)} (a change order is required for the rest)",
   module="ConsultingFees_PinnacleAcme",
   inputs={"hours": 520, "month_of_service": "2026-07-01", "invoice_date": "2026-08-05"},
   expected_branch="s 3.2 ceiling binds; change_order_required = true",
   must_cite=["SYN-013 s 3.1", "SYN-013 s 3.2"], docs=["SYN-013"])
qa("What is the target annual bonus for the DXC CFO under the April 2025 side letter?",
   "catala", usd(DXC_BASE * DXC_BONUS_RATE), module="IncentiveComp_DxcSideLetter",
   inputs={"annual_base_salary": usd(DXC_BASE), "target_bonus_rate": "135%"},
   expected_branch="base definition — 135% of an $800,000 base",
   must_cite=["EDGAR-002 para 2", "EDGAR-002 para 3"], docs=["EDGAR-002"],
   notes="Computed from a real SEC-filed document, not a synthetic one.")
qa("A non-exempt employee worked 46 hours in a week at $30 an hour. What are we required to pay?",
   "catala", usd(40 * 30 + 6 * 45), module="Overtime_Flsa",
   inputs={"hours_worked_in_week": 46, "regular_rate": usd(30)},
   expected_branch="29 U.S.C. s 207(a)(1) — 1.5x for the 6 hours over 40",
   must_cite=["REAL-007 s 207(a)(1)"], docs=["REAL-007"])
qa("We disclosed roadmap material to Northwind on 1 June 2026. When do our confidentiality "
   "obligations end?", "catala", "2029-06-01 (indefinitely for anything that is a trade secret)",
   module="ConfidentialityDuration",
   inputs={"disclosure_date": "2026-06-01", "agreement": "NorthwindMsa",
           "is_trade_secret": False},
   expected_branch="MSA s 11.2 — 3 years from the date of disclosure",
   must_cite=["SYN-001 s 11.2"], docs=["SYN-001"])

# --- elicitation cases ------------------------------------------------------------
qa("What's our liability cap on the Northwind contract?", "catala",
   "cannot answer yet — ask for the missing inputs", module="LiabilityCap_AcmeNorthwind",
   inputs={}, expected_missing_inputs=["claim_date", "claim_type", "annual_fees",
                                       "fees_paid_last_12mo"],
   must_cite=["SCOPES.md LiabilityCap_AcmeNorthwind"], docs=["SYN-001", "SYN-003"],
   notes="The date matters because the Order Form only governs claims from 2026-07-01; "
         "the claim type selects the cap branch. Listing the scope's input signature is "
         "the correct answer, not a guess.")
qa("How much sick leave has this employee accrued?", "catala",
   "cannot answer yet — ask for the missing inputs", module="SickLeaveAccrual_Acme",
   inputs={}, expected_missing_inputs=["hours_worked", "work_state", "days_employed", "exempt"],
   must_cite=["SCOPES.md SickLeaveAccrual_Acme"], docs=["SYN-005"])
qa("Does Globex get a service credit for a 99.4% month in July 2026?", "catala",
   "blocked — ServiceCredit_AcmeGlobex has conflicting definitions and cannot be executed",
   module="ServiceCredit_AcmeGlobex",
   inputs={"month": "2026-07-01", "monthly_uptime": 99.4,
           "monthly_service_fee": usd(GBX_MONTHLY_FEE), "request_date": "2026-08-05"},
   expected_conflict="CONF-002",
   must_cite=["SYN-009 s 2.1", "SYN-010 s 2.1", "SYN-010 s 4.1"], docs=["SYN-009", "SYN-010"],
   notes="10% of $30,000 and 20% of $30,000 both apply and neither is an exception. The "
         "answer is the conflict report with both clause texts, never one of the numbers.")

# --- vector-store cases -----------------------------------------------------------
qa("What law governs the Northwind agreement and where would a dispute be heard?", "vector",
   "California law, excluding conflict-of-laws rules; exclusive jurisdiction in the state and "
   "federal courts of San Francisco County, California",
   must_quote=["SYN-001 s 13.1"], docs=["SYN-001"])
qa("If a labor strike stops Acme from delivering, are we excused?", "vector",
   "Force majeure covers a labor dispute if prompt notice is given and commercially reasonable "
   "efforts are used to resume; it never excuses a failure to pay",
   must_quote=["SYN-001 s 15.1"], docs=["SYN-001"],
   notes="'Commercially reasonable efforts' is an open-textured standard — quote it, do not "
         "compute it.")
qa("What counts as Confidential Information under the Vertex NDA, and what doesn't?", "vector",
   "Non-public information marked confidential or reasonably understood to be confidential, "
   "excluding public, previously possessed, independently developed, and lawfully received "
   "information",
   must_quote=["SYN-011 s 2.1", "SYN-011 s 2.2"], docs=["SYN-011"])
qa("A vendor offered me playoff tickets. Can I accept?", "vector",
   "Judgment call under the Code of Conduct: gifts must be modest, infrequent, customary, and "
   "never intended to influence a decision; when in doubt decline and ask",
   must_quote=["SYN-015 s 2.2"], docs=["SYN-015"],
   notes="No rule-shaped content anywhere in SYN-015. A computed answer here is a failure.")
qa("What do the produced Meta documents say about internal research on teen users?", "vector",
   "Retrieval with citations into raw/discovery only — quote the produced pages, attribute each "
   "quotation to document and page, and compute nothing",
   must_quote=["DISC-002", "DISC-003", "DISC-004"],
   docs=["DISC-001", "DISC-002", "DISC-003", "DISC-004"],
   notes="Long, messy, real litigation PDFs. Tests that retrieval cites pages and that the "
         "Catala engine stays out of it.")

# --- both engines, labelled separately --------------------------------------------
qa("If we breach confidentiality with Northwind, what is our exposure and what does the clause "
   "actually say?", "both",
   f"catala: {usd(NW_ANNUAL_FEES * OF_EXCL_MULT)} cap; vector: the text of the confidentiality "
   "obligation and the definition it turns on", module="LiabilityCap_AcmeNorthwind",
   inputs={"claim_date": "2026-09-15", "annual_fees": usd(NW_ANNUAL_FEES),
           "fees_paid_last_12mo": usd(NW_ANNUAL_FEES), "claim_type": "Confidentiality"},
   must_cite=["SYN-003 s 5.2"], must_quote=["SYN-001 s 11.1", "SYN-001 s 11.2", "SYN-001 s 11.3"],
   docs=["SYN-001", "SYN-003"],
   notes="Each half must carry its own engine label. Silent blending is the failure mode.")
qa("Is our sick leave policy compliant for California employees?", "both",
   "catala: the handbook's 1:40 accrual is below the 1:30 statutory floor, so California "
   "employees accrue at 1:30 (40 hours at 1,200 hours worked, not 30); vector: the handbook "
   "savings clause that makes that substitution lawful",
   module="SickLeaveAccrual_Acme",
   inputs={"hours_worked": 1200, "weeks_worked": 30, "work_state": "CA",
           "days_employed": 365, "exempt": False},
   must_cite=["REAL-001 s 246(b)(1)", "SYN-005 s 4.2.1"], must_quote=["SYN-005 s 4.6"],
   docs=["SYN-005", "REAL-001"])

(GOLD / "qa_pairs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in QA))
print(f"qa_pairs.jsonl:      {len(QA)} pairs "
      f"({sum(1 for r in QA if r['engine'] == 'catala')} catala, "
      f"{sum(1 for r in QA if r['engine'] == 'vector')} vector, "
      f"{sum(1 for r in QA if r['engine'] == 'both')} both, "
      f"{sum(1 for r in QA if 'expected_missing_inputs' in r)} elicitation)")

# ==================================================================================
# Conflict set -- precision/recall ground truth for ingestion.
#   expect = "block"       two definitions apply, no priority -> Catala conflict, merge blocked
#   expect = "resolve"     a precedence clause exists -> encode as exception, answer computable
#   expect = "no_conflict" calibration: looks like a collision, is not one
# ==================================================================================
CONF = [
    dict(doc_a="SYN-003", doc_b="SYN-004", module="LiabilityCap_AcmeNorthwind",
         variable="cap (claim_type = Ordinary)",
         value_a=f"300% of annual fees = {usd(NW_ANNUAL_FEES * OF_CAP_MULT)} (SYN-003 s 5.1)",
         value_b=f"{usd(500_000)} flat for all claims (SYN-004 s 2.1)",
         expect="block", precedence_clause=None,
         why="SYN-004 s 3.2 puts the Amendment and any Order Form at equal rank and switches "
             "off MSA s 14.3 between them, so neither definition is an exception to the other.",
         surfaces=["SYN-003 s 5.1", "SYN-004 s 2.1", "SYN-004 s 3.2"]),
    dict(doc_a="SYN-009", doc_b="SYN-010", module="ServiceCredit_AcmeGlobex",
         variable="credit_percentage (uptime < 99.9% and >= 99.0%)",
         value_a="10% of the monthly fee (SYN-009 s 2.1)",
         value_b="20% of the monthly fee (SYN-010 s 2.1)",
         expect="block", precedence_clause=None,
         why="SYN-010 s 4.1 states the parties have not agreed an order of precedence.",
         surfaces=["SYN-009 s 2.1", "SYN-010 s 2.1", "SYN-010 s 4.1"]),
    dict(doc_a="SYN-001", doc_b="SYN-003", module="LiabilityCap_AcmeNorthwind",
         variable="cap (claim_type = Ordinary)",
         value_a="1x trailing 12-month fees (SYN-001 s 9.2)",
         value_b=f"300% of annual fees = {usd(NW_ANNUAL_FEES * OF_CAP_MULT)} (SYN-003 s 5.1)",
         expect="resolve", precedence_clause="SYN-001 s 14.3 (reaffirmed by SYN-003 s 6.1)",
         resolved_value=usd(NW_ANNUAL_FEES * OF_CAP_MULT),
         why="Order of precedence clause: the Order Form controls for the subject matter it "
             "addresses, so SYN-003 s 5.1 is encoded as an exception to SYN-001 s 9.2.",
         surfaces=["SYN-001 s 9.2", "SYN-003 s 5.1", "SYN-001 s 14.3"]),
    dict(doc_a="SYN-002", doc_b="SYN-003", module="ServiceCredit_AcmeNorthwind",
         variable="credit_percentage (all uptime bands)",
         value_a="10% / 25% / 100% (SYN-002 s A.3)",
         value_b="15% / 30% / 100% (SYN-003 s 4.2)",
         expect="resolve", precedence_clause="SYN-001 s 14.3 and SYN-003 s 6.1",
         resolved_value="15% / 30% / 100% for months from 2026-07-01; the Exhibit A table "
                        "still governs earlier months",
         why="The exception is date-scoped, so both tables stay live for their own periods.",
         surfaces=["SYN-002 s A.3", "SYN-003 s 4.2", "SYN-001 s 14.3"]),
    dict(doc_a="SYN-005", doc_b="REAL-001", module="SickLeaveAccrual_Acme",
         variable="accrual_rate (work_state = CA)",
         value_a="1 hour per 40 hours worked (SYN-005 s 4.2.1)",
         value_b="not less than 1 hour per 30 hours worked (Cal. Lab. Code s 246(b)(1))",
         expect="resolve", precedence_clause="SYN-005 s 4.6 (statutory floor savings clause)",
         resolved_value="1 per 30 for California employees",
         why="A statutory floor overrides an employer policy. This is the compliance bug the "
             "system is meant to catch: HQ-state policy applied to a stricter state.",
         surfaces=["SYN-005 s 4.2.1", "REAL-001 s 246(b)(1)", "SYN-005 s 4.6"]),
    dict(doc_a="SYN-005", doc_b="REAL-002", module="SickLeaveAccrual_Acme",
         variable="accrual_rate and annual cap (work_state = CO)",
         value_a="1 per 40, 40-hour annual use cap (SYN-005 s 4.2)",
         value_b="1 per 30, 48-hour annual cap (C.R.S. s 8-13.3-403)",
         expect="resolve", precedence_clause="SYN-005 s 4.6",
         resolved_value="1 per 30 with a 48-hour cap for Colorado employees",
         why="Same statutory-floor pattern with different numbers, so the exception chain has "
             "to be per-state rather than a single override.",
         surfaces=["SYN-005 s 4.2", "REAL-002", "SYN-005 s 4.6"]),
    dict(doc_a="SYN-005", doc_b="REAL-003", module="FinalPaycheckDeadline_Acme",
         variable="due_date (work_state = CA, separation = InvoluntaryTermination)",
         value_a="next regular payroll date (SYN-005 s 9.3)",
         value_b="immediately on termination (Cal. Lab. Code s 201)",
         expect="resolve", precedence_clause="SYN-005 s 9.5",
         resolved_value="the last day of employment",
         why="Date-arithmetic version of the same floor pattern; s 202 adds the 72-hour branch "
             "for an employee who quits without notice.",
         surfaces=["SYN-005 s 9.3", "REAL-003 s 201", "REAL-004 s 202", "SYN-005 s 9.5"]),
    dict(doc_a="REAL-004", doc_b="SYN-005", module="FinalPaycheckDeadline_Acme",
         variable="due_date (work_state = CA, separation = QuitWithoutNotice)",
         value_a="within 72 hours of quitting (Cal. Lab. Code s 202)",
         value_b="next regular payroll date (SYN-005 s 9.3)",
         expect="resolve", precedence_clause="SYN-005 s 9.5",
         resolved_value="last day + 72 hours",
         why="Second branch of the same statutory-floor pattern as CONF-007, on a "
             "different separation type. Found by the ingestion pre-check, not by hand.",
         surfaces=["REAL-004 s 202", "SYN-005 s 9.3", "SYN-005 s 9.5"]),
    dict(doc_a="SYN-006", doc_b="SYN-007", module="ExpenseApproval_Acme",
         variable="approval_level ($5,000 < amount <= $25,000, department = Engineering)",
         value_a="Vice President (SYN-006 s 2.1)", value_b="Director (SYN-007 s 2.1)",
         expect="resolve", precedence_clause="SYN-005 s 7.1 (more restrictive controls)",
         resolved_value="Vice President",
         why="Resolution is by a comparison rule rather than by document rank, so the exception "
             "has to encode the ordering Manager < Director < VP < CFO.",
         surfaces=["SYN-006 s 2.1", "SYN-007 s 2.1", "SYN-005 s 7.1"]),
    dict(doc_a="SYN-001", doc_b="SYN-004", module="LiabilityCap_AcmeNorthwind",
         variable="cap (claim_type = Ordinary)",
         value_a="1x trailing 12-month fees (SYN-001 s 9.2)",
         value_b=f"{usd(500_000)} flat (SYN-004 s 2.1)",
         expect="resolve", precedence_clause="SYN-004 s 2.1 ('Section 9.2 of the MSA is "
                                             "deleted and replaced')",
         resolved_value=usd(500_000),
         why="The Amendment expressly replaces MSA s 9.2, so as between these two "
             "documents there is an order of precedence. The s 3.2 equal-rank disclaimer "
             "is scoped to Order Forms only -- which the keyword pre-check cannot tell, so "
             "it conservatively labels this pair 'block'. Catala's own check is the "
             "authority, and it resolves once the exception is declared.",
         surfaces=["SYN-001 s 9.2", "SYN-004 s 2.1", "SYN-004 s 3.2"]),
    dict(doc_a="SYN-008", doc_b="SYN-003", module=None,
         variable="liability cap — same variable name, different agreement",
         value_a="150% of trailing fees, ACME-HEL-MSA-2025 (SYN-008 s 8.1)",
         value_b="300% of annual fees, ACME-NW-MSA-2026 (SYN-003 s 5.1)",
         expect="no_conflict", precedence_clause=None, resolved_value=None,
         why="Different agreements and different counterparties. Modules are scoped per "
             "agreement, so these never meet. Flagging this is a false positive.",
         surfaces=[]),
    dict(doc_a="SYN-001", doc_b="SYN-011", module=None,
         variable="confidentiality survival period",
         value_a="3 years from disclosure (SYN-001 s 11.2)",
         value_b="5 years from disclosure (SYN-011 s 4.2)",
         expect="no_conflict", precedence_clause=None, resolved_value=None,
         why="Different agreements with different counterparties (Northwind vs Vertex); "
             "ConfidentialityDuration takes agreement_id as an input.",
         surfaces=[]),
    dict(doc_a="SYN-005", doc_b="REAL-006", module="SickLeaveAccrual_Acme",
         variable="accrual_rate (work_state = WA)",
         value_a="1 per 40 (SYN-005 s 4.2.1)",
         value_b="at least 1 per 40 (RCW 49.46.210(1)(a))",
         expect="no_conflict", precedence_clause=None, resolved_value="1 per 40",
         why="Two documents defining the same variable with the same value. A detector that "
             "keys on 'two documents mention accrual' fires here; a correct one does not.",
         surfaces=[]),
    dict(doc_a="SYN-013", doc_b="SYN-001", module=None,
         variable="liability cap tied to fees",
         value_a="fees paid under the SOW, Acme as customer (SYN-013 s 6.1)",
         value_b="1x trailing fees, Acme as provider (SYN-001 s 9.2)",
         expect="no_conflict", precedence_clause=None, resolved_value=None,
         why="Different agreements and opposite roles. Tests that role and agreement are part "
             "of the module identity.",
         surfaces=[]),
]
for i, c in enumerate(CONF, 1):
    c["id"] = f"CONF-{i:03d}"
(GOLD / "conflicts.jsonl").write_text("".join(json.dumps(c) + "\n" for c in CONF))
print(f"conflicts.jsonl:     {len(CONF)} pairs "
      f"({sum(1 for c in CONF if c['expect'] == 'block')} block, "
      f"{sum(1 for c in CONF if c['expect'] == 'resolve')} resolve, "
      f"{sum(1 for c in CONF if c['expect'] == 'no_conflict')} calibration)")
