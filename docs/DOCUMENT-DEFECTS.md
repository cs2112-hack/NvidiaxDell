# Document defects and genuine ambiguities

Findings about the SOURCE DOCUMENTS, not about the code. Each was produced by
an adversarial reviewer that saw the clause text and the compiled artefact but
not the implementer's reasoning, and each is a place where the text does not
determine an answer — so no encoding can be "correct" and the encoding chosen
is recorded alongside the open question.

These are the findings a lawyer should see. They are not bugs to be fixed in
Catala; fixing them means amending the document.

## AMB-01 — EMP-ANNEX-C C-4.2, EMP-ANNEX-C C-7.1, EMP-ANNEX-C C-7.2

**Fact pattern.** A Grade 3 Employee works 49 hours in a Payroll Week and the 49th hour is worked on a Gazetted Public Holiday. C-7.2 purports to create an exception to C-7.1 for exactly this case: the rate is 'the greater of (a) 2.0 times the Base Hourly Rate and (b) the rate determined under C-4.2 plus 0.25'.

**Why the text does not settle it.** This is a finding about the DOCUMENT, not the module. Evaluate C-7.2's two limbs on the figures actually drafted. Limb (a) is 2.0. Limb (b) is 'the rate determined under C-4.2 plus 0.25'; C-4.2 fixes that rate at 1.5, so limb (b) is 1.75. Since 1.75 < 2.0 unconditionally, and neither limb has any variable in it, limb (a) wins in every case in which C-7.2 is capable of applying. C-7.2 is therefore inoperative: it is expressed as an exception to C-7.1 but can never alter the rate C-7.1 already produces. Either the drafter mis-stated a figure (a premium of 0.25 over C-4.2 was presumably intended to exceed 2.0, which would require a C-4.2 rate above 1.75, or an addend above 0.5), or C-7.2 was intended to add 0.25 cumulatively to the 2.0 holiday rate rather than to the C-4.2 rate, or limb (b) was meant to be read with the C-6 night premium folded in (1.5 + 0.15 + 0.25 = 1.9, still short of 2.0). On the text as it stands no reading makes limb (b) bite. The module mirrors the defect faithfully - it encodes the comparison as a test between two literal constants, 2.0 against 1.5 + 0.25 - so the else-branch of that test is dead code and the whole c7_2 exception is a branch that can never change an output. Faithful to the source, but the source needs amending; no input can expose a wrong number here, which is exactly why it should be raised against the document before a future amendment to C-4.2 silently activates it.

**What the encoding currently does.** Observed `{"multiplier": 2.0, "night_premium": 0.0, "total_rate": 2.0, "accrues_toil": false}`; the
competing reading would give `{"multiplier": 2.0, "night_premium": 0.0, "total_rate": 2.0, "accrues_toil": false, "note": "2.0 is correct, but only because C-7.2 cannot change the C-7.1 answer on the numbers as drafted"}`.

## AMB-02 — EMP-ANNEX-C C-4.1, EMP-ANNEX-C C-5.1, EMP-ANNEX-C C-8.1

**Fact pattern.** A Grade 5 Employee works the 45th hour of a Payroll Week. It is not Critical Incident Response, not a Gazetted Public Holiday and not a Night Hour. C-4.1 would give 1.25; C-5.1 says there is no entitlement to an overtime payment. C-8.1 says that where two or more provisions of C-4, C-5 or C-7 would apply to the same hour, only the single highest applicable multiplier applies.

**Why the text does not settle it.** A finding about the DOCUMENT. Two provisions bear on this hour: C-4.1, which 'would apply' to give 1.25, and C-5.1, which gives nil. C-8.1 is drafted as a free-standing precedence rule over 'two or more provisions of C-4, C-5 or C-7' and directs that 'only the single highest applicable multiplier applies'. Applied literally, the highest of {1.25, nil} is 1.25, so C-8.1 would defeat C-5.1 on its own facts - and since C-4.1 or C-4.2 always applies wherever C-5.1 applies (both are keyed to hours in excess of 40), and their multipliers always exceed nil, C-8.1 would render C-5.1 wholly nugatory for every Grade 5 Employee. That cannot have been intended, so C-8.1 must be confined to cases where provisions compete without an express exception relationship, C-5.1's opening words 'By way of exception to C-4' taking it outside C-8.1's reach. The module adopts that confined reading (its 1.25 rule is subordinated to the grade rule). I do not challenge the outcome: 0.0 with TOIL is the right answer. But the reading that produces it is an implicit repair of C-8.1, not something C-8.1's words deliver, and it is the same latent tension that has to be resolved to decide the C-7-versus-C-5.1 interaction in my BREAK findings. C-8.1 should be amended to say that it operates subject to the express exceptions stated in C-4 and C-5, or to speak only of the multipliers under C-4.1, C-4.2, C-5.2, C-7.1 and C-7.2.

**What the encoding currently does.** Observed `{"multiplier": 0.0, "night_premium": 0.0, "total_rate": 0.0, "accrues_toil": true}`; the
competing reading would give `{"multiplier": 0.0, "night_premium": 0.0, "total_rate": 0.0, "accrues_toil": true, "note": "the module's answer; the point is that C-8.1 read literally compels 1.25 instead and thereby destroys C-5.1"}`.

## AMB-03 — EMP-ANNEX-C C-6.1, EMP-ANNEX-C C-5.1, EMP-ANNEX-C C-6.2, EMP-ANNEX-C C-8.2

**Fact pattern.** A Grade 5 Employee works the 45th hour of a Payroll Week between 22:00 and 06:00, so it is a Night Hour. It is not Critical Incident Response and not a Gazetted Public Holiday, and there is no standing shift allowance. Under C-5.1 the hour attracts no overtime payment at all; the Employee accrues an hour of time off in lieu instead. Is the 0.15 night premium nonetheless payable?

**Why the text does not settle it.** A finding about the DOCUMENT. C-6.1's first sentence is a free-standing entitlement - 'A premium of 0.15 times the Base Hourly Rate is payable in respect of each Night Hour worked' - conditioned on nothing but the hour being a Night Hour, and the only stated exception to it is C-6.2 (standing shift allowance), which is not engaged. On that reading the premium is payable and the module is right. But the second sentence enumerates what the premium is payable in addition to: 'any amount payable under C-4, C-5.2 or C-7'. C-5.1 is conspicuously absent from a list that otherwise names every payment-conferring provision in the Annex, and C-8.2 repeats the same three-item list. An employer could argue that the enumeration is exhaustive of the cases in which the premium arises, that a 'premium' presupposes a principal sum to be a premium upon, and that where C-5.1 applies there is no amount payable for the hour at all - so 0.15 times the Base Hourly Rate would be the Employee's entire cash entitlement for an hour otherwise compensated only in leave. Which construction governs decides a real amount of money and cannot be settled from the text. The module has silently taken the first (better, in my view, and the one C-1.2 favours) but the document should say so expressly - either by adding C-5.1 to C-6.1's list, or by stating that the premium is payable whether or not any other amount is payable for the hour.

**What the encoding currently does.** Observed `{"multiplier": 0.0, "night_premium": 0.15, "total_rate": 0.15, "accrues_toil": true}`; the
competing reading would give `{"multiplier": 0.0, "night_premium": 0.15, "total_rate": 0.15, "accrues_toil": true, "note": "the module's answer; flagged because the document's second sentence of C-6.1 pulls the other way"}`.

## AMB-04 — EMP-ANNEX-C C-3.1, EMP-ANNEX-C C-7.1, EMP-ANNEX-C C-4.1, EMP-ANNEX-C C-2.1

**Fact pattern.** A Grade 3 Employee works the 20th hour of a Payroll Week on a Gazetted Public Holiday - an hour well within the 40-hour ordinary working week fixed by C-3.1, so no overtime payment arises under C-4 at all. C-7.1 says each hour worked on a Gazetted Public Holiday 'is calculated at 2.0 times the Base Hourly Rate ... in substitution for, and not in addition to, any amount otherwise payable under C-4'.

**Why the text does not settle it.** A finding about the DOCUMENT. C-7.1 is unqualified as to when in the week the holiday hour falls, so it plainly reaches ordinary-time hours; the difficulty is what the 2.0 rate displaces when there is nothing under C-4 to displace. Two readings compete. On the first, 2.0 times the Base Hourly Rate is the total pay for the hour, so a holiday hour inside the first 40 is worth 2.0 and the Employee's salary for that hour is subsumed - which makes the substitution proviso do real work for hours above 40 and nothing for hours below it. On the second, C-7.1 confers a payment of 2.0 times the Base Hourly Rate over and above the salary that C-2.1 and the ordinary week already cover, so the holiday hour is worth 3.0 in total. The module reports 2.0 for an hour within ordinary time and 0.0 for an ordinary non-holiday hour, and its weekly scope sums rate times Base Hourly Rate into overtime_payment - which is the second reading, and it is not obviously the one C-7.1's words carry, since a clause that fixes what an hour 'is calculated at' reads more naturally as fixing the whole rate for the hour than as fixing a top-up. The choice moves 1.0 times the Base Hourly Rate per holiday hour for every Employee who works a bank holiday, and Annex C does not resolve it. C-7.1 should state whether the 2.0 rate is inclusive of ordinary salary for the hour.

**What the encoding currently does.** Observed `{"multiplier": 2.0, "night_premium": 0.0, "total_rate": 2.0, "accrues_toil": false}`; the
competing reading would give `{"multiplier": 2.0, "night_premium": 0.0, "total_rate": 2.0, "accrues_toil": false, "note": "the module's answer on its own convention; the ambiguity is what the 2.0 is measured against for an ordinary-time hour"}`.

## AMB-05 — EMP-ANNEX-C C-5.1, C-7.1, C-8.1: does a public holiday displace time off in lieu?

**This is the strongest evidence of a document defect in the corpus, because two
independent blind reviewers read the same three clauses and reached opposite
conclusions.**

**Fact pattern.** A Grade 5 Employee works the 41st hour of a Payroll Week on a
Gazetted Public Holiday. It is not Critical Incident Response.

**Reading A (round 1 reviewer, and what the module implements).** The hour is
paid at C-7.1's 2.0 *and* accrues an hour of time off in lieu under the second
sentence of C-5.1. C-7.1 substitutes its rate for "any amount otherwise payable
under **C-4**" and says nothing about C-5. C-8.1 provides that "only the single
highest applicable **multiplier** applies" — it selects among multipliers and
does not purport to disapply the losing provision in every respect, and a leave
accrual is not a multiplier. C-5.2, by contrast, opens "Notwithstanding C-5.1",
which displaces C-5.1 as a whole including its accrual limb — so the asymmetry
between C-5.2 and C-7.1 is textually grounded rather than accidental.

**Reading B (round 3 reviewer).** C-8.1 is the governing precedence rule and it
selects C-7.1 as the provision that "applies to that hour", displacing C-5.1
entirely; a provision that does not apply cannot confer an accrual. On this
reading the module's two chains contradict each other, since the C-5.2 path
does suppress the accrual.

**Resolution adopted.** Reading A, on the ground that C-8.1's operative words
are confined to the multiplier and C-7.1's substitution is expressly confined
to C-4. It is recorded as a permanent test (`tests/counterexamples/CE-0001.yaml`).

**What the Company should do.** Amend C-8.1 to say whether it displaces a
provision entirely or only its multiplier, and amend C-7.1 to say whether it
substitutes for C-5 as well as C-4. As drafted, an employee's leave balance
turns on a question the Annex does not answer, and reasonable readers disagree.
Worth one hour of leave per public-holiday overtime hour for every Grade 5+
employee.
