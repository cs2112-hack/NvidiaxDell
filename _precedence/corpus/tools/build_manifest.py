#!/usr/bin/env python3
"""Write manifest.csv -- the license-hygiene record and the doc_id -> path map.

Kept as code rather than a hand-edited CSV so that (a) every path is verified to exist
on write, and (b) the doc ids the gold set references cannot drift from the files.
"""
import csv, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
E = "raw/real/edgar/"

# doc_id, path, title, kind, rule_dense, modules, conflicts_with, license, notes
ROWS = [
    # ---- authored synthetic: the controlled half, where the conflicts are planted ----
    ("SYN-001", "raw/synthetic/acme_msa_2026.md", "Acme/Northwind Master Services Agreement",
     "synthetic", "partial", "LiabilityCap_AcmeNorthwind;ConfidentialityDuration",
     "SYN-003", "authored for this corpus; internal use",
     "Base of the liability-cap exception chain; s 14.3 is the precedence hook. ss 12-15 are vector-only."),
    ("SYN-002", "raw/synthetic/acme_sla_exhibit_a.md", "Exhibit A - standard SLA",
     "synthetic", "yes", "ServiceCredit_AcmeNorthwind", "SYN-003",
     "authored for this corpus; internal use", "10/25/100 tier table, 30-day claim window, 100% ceiling."),
    ("SYN-003", "raw/synthetic/northwind_orderform_q3_2026.md", "Order Form No. 1 (Enterprise)",
     "synthetic", "yes", "LiabilityCap_AcmeNorthwind;ServiceCredit_AcmeNorthwind",
     "SYN-001;SYN-002;SYN-004", "authored for this corpus; internal use",
     "Resolvable conflict with SYN-001/SYN-002; hard conflict with SYN-004."),
    ("SYN-004", "raw/synthetic/northwind_amendment_one_2026.md", "Amendment No. 1",
     "synthetic", "yes", "LiabilityCap_AcmeNorthwind", "SYN-003",
     "authored for this corpus; internal use",
     "BLOCKER: s 3.2 disclaims precedence, so the $500k cap and the 300% cap both apply."),
    ("SYN-005", "raw/synthetic/acme_employee_handbook_2026.md", "US Employee Handbook (excerpt)",
     "synthetic", "yes",
     "PtoAccrual_Acme;SickLeaveAccrual_Acme;FinalPaycheckDeadline_Acme;ExpenseApproval_Acme",
     "REAL-001;REAL-002;REAL-003;SYN-007", "authored for this corpus; internal use",
     "s 4.6 and s 9.5 are the statutory-floor hooks; s 7.1 is the more-restrictive-controls rule."),
    ("SYN-006", "raw/synthetic/acme_expense_policy_2026.md", "Corporate Expense Reimbursement Policy",
     "synthetic", "yes", "ExpenseApproval_Acme", "SYN-007",
     "authored for this corpus; internal use", "Four-tier approval ladder; $75 receipt threshold."),
    ("SYN-007", "raw/synthetic/acme_eng_dept_expense_policy_2026.md", "Engineering Department Expense Policy",
     "synthetic", "yes", "ExpenseApproval_Acme", "SYN-006;SYN-005",
     "authored for this corpus; internal use", "Overlapping thresholds with SYN-006; resolved by SYN-005 s 7.1."),
    ("SYN-008", "raw/synthetic/helios_msa_2025.md", "Acme/Helios Master Services Agreement",
     "synthetic", "yes", "LiabilityCap_AcmeHelios", "",
     "authored for this corpus; internal use",
     "CALIBRATION: same variables, different agreement. Must not be flagged as a conflict."),
    ("SYN-009", "raw/synthetic/globex_msa_sla_exhibit.md", "Acme/Globex Service Level Exhibit",
     "synthetic", "yes", "ServiceCredit_AcmeGlobex", "SYN-010",
     "authored for this corpus; internal use", "10/25/100 tiers at a $30,000 monthly fee."),
    ("SYN-010", "raw/synthetic/globex_sla_addendum_2026.md", "Acme/Globex Service Level Addendum",
     "synthetic", "yes", "ServiceCredit_AcmeGlobex", "SYN-009",
     "authored for this corpus; internal use",
     "BLOCKER: 20/40/100 tiers, and s 4.1 states no order of precedence was agreed."),
    ("SYN-011", "raw/synthetic/vertex_mutual_nda_2026.md", "Acme/Vertex Mutual NDA",
     "synthetic", "partial", "ConfidentialityDuration", "",
     "authored for this corpus; internal use",
     "Only the 5-year survival and 30-day return deadline are rule-shaped; the rest is vector-only."),
    ("SYN-012", "raw/synthetic/acme_dpa_2026.md", "Acme/Northwind Data Processing Addendum",
     "synthetic", "partial", "BreachNotice_AcmeNorthwind", "",
     "authored for this corpus; internal use", "72-hour breach notice; security obligations are vector-only."),
    ("SYN-013", "raw/synthetic/pinnacle_consulting_sow_2026.md", "Pinnacle SOW No. 2",
     "synthetic", "yes", "ConsultingFees_PinnacleAcme;LiabilityCap_PinnacleAcme", "",
     "authored for this corpus; internal use",
     "Acme is the customer here - tests that role is part of module identity."),
    ("SYN-014", "raw/synthetic/acme_offer_letter_reyes_2026.md", "Offer letter - Dana Reyes (California)",
     "synthetic", "yes", "IncentiveComp_Acme;SickLeaveAccrual_Acme;PtoAccrual_Acme", "",
     "authored for this corpus; fictional individual",
     "Supplies work_state = CA, exempt = true: turns the handbook/statute conflict into an instance."),
    ("SYN-015", "raw/synthetic/acme_code_of_conduct_excerpt.md", "Code of Business Conduct (excerpt)",
     "synthetic", "no", "", "", "authored for this corpus; internal use",
     "CALIBRATION: zero rule-shaped clauses. Everything routes to the vector store."),
    # ---- real public-domain statutes (verbatim, fetched) ----
    ("REAL-001", "raw/real/ca_labor_code_246_sick_leave.txt", "Cal. Labor Code s 246 - paid sick days",
     "real_statute", "yes", "SickLeaveAccrual_Acme", "SYN-005",
     "public domain (government edicts doctrine)", "Verbatim. 1:30 accrual floor, 80h accrual cap, 40h use cap."),
    ("REAL-002", "raw/real/co_crs_8_13_3_403_sick_leave_digest.md",
     "C.R.S. ss 8-13.3-403 and 8-4-109 (digest)", "real_digest", "yes",
     "SickLeaveAccrual_Acme;FinalPaycheckDeadline_Acme", "SYN-005",
     "public domain statute; paraphrased digest (fetch returned HTTP 403)",
     "GAP: verbatim text not retrieved. Numbers verified against the statute, prose paraphrased."),
    ("REAL-003", "raw/real/ca_labor_code_201_final_pay.txt", "Cal. Labor Code s 201 - discharge",
     "real_statute", "yes", "FinalPaycheckDeadline_Acme", "SYN-005",
     "public domain (government edicts doctrine)", "Verbatim. Final wages due immediately on discharge."),
    ("REAL-004", "raw/real/ca_labor_code_202_final_pay_quit.txt", "Cal. Labor Code s 202 - quitting",
     "real_statute", "yes", "FinalPaycheckDeadline_Acme", "",
     "public domain (government edicts doctrine)", "Verbatim. 72-hour rule for an employee who quits without notice."),
    ("REAL-005", "raw/real/ca_labor_code_227_3_vacation_payout.txt", "Cal. Labor Code s 227.3 - vacation",
     "real_statute", "yes", "FinalPaycheckDeadline_Acme", "SYN-005",
     "public domain (government edicts doctrine)", "Verbatim. Vested vacation is wages payable on separation."),
    ("REAL-006", "raw/real/wa_rcw_49_46_210_sick_leave.txt", "RCW 49.46.210 - paid sick leave",
     "real_statute", "yes", "SickLeaveAccrual_Acme", "",
     "public domain (government edicts doctrine)",
     "Verbatim. CALIBRATION: 1:40 matches the handbook exactly - same variable, same value, no conflict."),
    ("REAL-007", "raw/real/flsa_29_usc_207_overtime.txt", "29 U.S.C. s 207 - maximum hours",
     "real_statute", "yes", "Overtime_Flsa", "",
     "public domain (US Code, via Cornell LII)", "Verbatim. Only s 207(a)(1) is encoded."),
    ("REAL-008", "raw/real/cloud_sla_credit_tiers_facts.md", "Cloud SLA threshold/credit tables (facts)",
     "real_digest", "yes", "ServiceCredit_Reference", "",
     "uncopyrightable facts restated; vendor prose NOT reproduced",
     "Provenance for the SLA tier shape, incl. the real AWS 30%-vs-25% discrepancy."),
    ("REAL-009", "raw/real/university_pto_and_tuition_digest.md", "Public-university HR rules (digest)",
     "real_digest", "yes", "PtoAccrual_Acme;TuitionReimbursement_Reference", "",
     "published public-institution policy; paraphrased",
     "Provenance for the 0.0961/0.1154/0.1346 accrual factors and the $5,250 tuition cap."),
    # ---- real EDGAR exhibits ----
    ("EDGAR-001", E + "edgar_inhibitor_therapeutics_inc_inti_cik_0001_0001493152-25-019381_ex10-1.txt",
     "Inhibitor Therapeutics - Master Services Agreement (EX-10.1, 2025-10-27)",
     "real_contract", "partial", "LiabilityCap_InhibitorMsa", "",
     "public SEC disclosure (EDGAR)",
     "Real cap clause: aggregate liability <= fees paid or payable under the relevant SOW (s 16)."),
    ("EDGAR-002", E + "edgar_dxc_technology_co_dxc_cik_0001688568__0001688568-25-000028_a107-delbenesideletter.txt",
     "DXC Technology - CFO compensation side letter (EX-10.7, 2025)",
     "real_contract", "yes", "IncentiveComp_DxcSideLetter", "",
     "public SEC disclosure (EDGAR)",
     "Real numbers: $800,000 base, 135% target bonus, 35-mile relocation trigger (vector-only)."),
    ("EDGAR-003", E + "edgar_bitcoin_infrastructure_acquisition_corp__0001829126-25-010212_bitcoininfra_ex10-1.txt",
     "Bitcoin Infrastructure Acquisition Corp - Consulting Services Agreement (EX-10.1, 2025-12-22)",
     "real_contract", "partial", "", "", "public SEC disclosure (EDGAR)",
     "Retrieval-only. Monthly fee and term are rule-shaped if a fourth module is needed."),
    ("EDGAR-004", E + "edgar_johnson_johnson_jnj_cik_0000200406__0001193125-23-138287_d301195dex103.txt",
     "Johnson & Johnson - agreement exhibit (EX-10.3, 2023)",
     "real_contract", "no", "", "", "public SEC disclosure (EDGAR)",
     "Retrieval-only by design: ~79k chars of real enterprise drafting. Messiness anchor."),
    ("EDGAR-005", E + "edgar_kenvue_inc_kvue_cik_0001944048__0001628280-23-016423_exhibit103-8xk.txt",
     "Kenvue - agreement exhibit (EX-10.3, 2023)",
     "real_contract", "no", "", "", "public SEC disclosure (EDGAR)", "Retrieval-only. Same role as EDGAR-004."),
    # ---- CC-licensed standard templates ----
    ("TPL-001", "raw/templates/commonpaper_cloud_service_agreement.txt",
     "Common Paper Cloud Service Agreement (standard terms + Cover Page)",
     "template", "yes", "", "", "CC BY 4.0 (Common Paper) - attribution required",
     "Cover Page variables (General Cap, Increased Claims, Increased Cap) are the drafting component's schema."),
    ("TPL-002", "raw/templates/commonpaper_mutual_nda.txt", "Common Paper Mutual NDA",
     "template", "partial", "", "", "CC BY 4.0 (Common Paper) - attribution required",
     "Few-shot anchor for NDA generation; term and survival are the rule-shaped fields."),
    ("TPL-003", "raw/templates/commonpaper_dpa.txt", "Common Paper Data Processing Agreement",
     "template", "partial", "", "", "CC BY 4.0 (Common Paper) - attribution required",
     "Breach-notice and subprocessor windows are rule-shaped; the rest is narrative."),
    ("TPL-004", "raw/templates/onenda.txt", "oneNDA standard NDA", "template", "partial", "", "",
     "CC BY 4.0 (oneNDA)", "Shortest clean NDA in the corpus; good roundtrip-drafting target."),
    ("TPL-005", "raw/templates/onesaas.txt", "oneSaaS standard terms", "template", "partial", "", "",
     "CC BY 4.0 (oneSaaS)", "Stand-in for oneSLA, which is not published at the documented URL (404)."),
    ("TPL-006", "raw/templates/yc_postmoney_safe_valuation_cap_only.txt",
     "Y Combinator post-money SAFE (valuation cap only)", "template", "yes", "", "",
     "CC BY-ND 4.0 (Y Combinator) - use as-is, do NOT distribute modified versions",
     "Conversion-price arithmetic is highly Catala-shaped but intentionally not encoded: ND licence."),
    ("TPL-007", "raw/templates/bonterms_sla_digest.md", "Bonterms SLA/PSA field map (digest)",
     "template_digest", "yes", "ServiceCredit_Reference", "",
     "CC BY 4.0 (Bonterms); this file is a paraphrased field map",
     "GAP: download centre is JS-driven, so no verbatim text was fetched."),
    ("TPL-008", "raw/templates/yc_pro_rata_side_letter.txt", "Y Combinator pro-rata side letter",
     "template", "partial", "", "", "CC BY-ND 4.0 (Y Combinator) - use as-is", "Short companion to TPL-006."),
    # ---- litigation / discovery PDFs: retrieval + demo assets ----
    ("DISC-001", "raw/discovery/ftc_v_facebook_amended_complaint_redacted.pdf",
     "FTC v. Facebook - amended complaint (public redacted)", "discovery", "no", "", "",
     "US government work (public domain)", "Retrieval-only. Long real pleading with numbered allegations."),
    ("DISC-002", "raw/discovery/fb_six4three_internal_emails_engineers_concern.pdf",
     "Internal Facebook emails produced in Six4Three (engineer concerns)", "discovery", "no", "", "",
     "publicly released court record; underlying material is third-party",
     "The 'bunch of internal papers' case: email threads, no clause structure at all."),
    ("DISC-003", "raw/discovery/fb_instagram_internal_emails.pdf",
     "Facebook/Instagram internal emails (produced)", "discovery", "no", "", "",
     "publicly released court record; underlying material is third-party", "Retrieval-only, 16 pages."),
    ("DISC-004", "raw/discovery/meta_instagram_multistate_ag_complaint_redacted_2024.pdf",
     "Multistate AG complaint against Meta/Instagram (redacted, 2024-01-30)", "discovery", "no", "", "",
     "public court record", "123 pages. Stress-tests chunking and page-level citation."),
    ("DISC-005", "raw/discovery/kadrey_v_meta_complaint.pdf", "Kadrey v. Meta - complaint",
     "discovery", "no", "", "", "public court record", "Retrieval-only."),
    ("DISC-006", "raw/discovery/kadrey_v_meta_complaint_exhibits.pdf", "Kadrey v. Meta - complaint exhibits",
     "discovery", "no", "", "", "public court record", "Retrieval-only; exhibit-style attachments."),
    ("DISC-007", "raw/discovery/kadrey_v_meta_motion_for_summary_judgment.pdf",
     "Kadrey v. Meta - motion for summary judgment", "discovery", "no", "", "",
     "public court record", "53 pages of argument prose - the least rule-shaped text in the corpus."),
]
HEADER = ["doc_id", "path", "title", "kind", "rule_dense", "modules", "conflicts_with",
          "license", "notes"]


def main():
    missing = [r[1] for r in ROWS if not (ROOT / r[1]).exists()]
    if missing:
        print("missing files:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 1
    with (ROOT / "manifest.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(ROWS)
    kinds = {}
    for r in ROWS:
        kinds[r[3]] = kinds.get(r[3], 0) + 1
    print(f"manifest.csv: {len(ROWS)} documents  " +
          "  ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
