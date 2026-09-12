# Review packet: catala

target: {"module": "NdaSurvival", "path": "catala/modules/NdaSurvival.catala_en"}

## Source document (authoritative)

### NDA-MUT N-3.1 — Mutual Non-Disclosure Agreement (Standard Form) (v3.4, effective 2024-07-01)
Section N-3 Obligations of the Recipient

The Recipient will keep the Confidential Information confidential and
will not disclose it to any person except as permitted by this Agreement.

### NDA-MUT N-3.2 — Mutual Non-Disclosure Agreement (Standard Form) (v3.4, effective 2024-07-01)
Section N-3 Obligations of the Recipient

The Recipient will use the Confidential Information solely for the
Permitted Purpose.

### NDA-MUT N-5.1 — Mutual Non-Disclosure Agreement (Standard Form) (v3.4, effective 2024-07-01)
Section N-5 Term and survival

This Agreement takes effect on the Effective Date and continues for a
period of 2 years, unless terminated earlier by either party on 30 days' written
notice.

### NDA-MUT N-5.2 — Mutual Non-Disclosure Agreement (Standard Form) (v3.4, effective 2024-07-01)
Section N-5 Term and survival

The obligations of confidence in N-3 survive expiry or termination of
this Agreement and continue for a period of 3 years from the date of expiry or
termination.

### NDA-MUT N-5.3 — Mutual Non-Disclosure Agreement (Standard Form) (v3.4, effective 2024-07-01)
Section N-5 Term and survival

By way of exception to N-5.2, the obligations of confidence in respect
of Confidential Information which constitutes a trade secret continue for so long
as that information remains a trade secret.

### NDA-MUT N-5.4 — Mutual Non-Disclosure Agreement (Standard Form) (v3.4, effective 2024-07-01)
Section N-5 Term and survival

By way of exception to N-5.2, the obligations of confidence in respect
of personal data continue for so long as the Recipient holds that personal data.

### Not quoted by the artefact

The clauses above cross-refer to the provisions below, or use terms they define.

### NDA-MUT N-2.1 — Mutual Non-Disclosure Agreement (Standard Form) (v3.4, effective 2024-07-01)
Section N-2 Confidential Information

"Confidential Information" means all information disclosed by or on
behalf of the Discloser to the Recipient, whether before or after the date of
this Agreement, whether in writing, orally or by inspection of tangible objects,
which is designated as confidential or which by its nature or the circumstances
of its disclosure ought reasonably to be regarded as confidential.

### NDA-MUT N-3.3 — Mutual Non-Disclosure Agreement (Standard Form) (v3.4, effective 2024-07-01)
Section N-3 Obligations of the Recipient

The Recipient will protect the Confidential Information using no less
than the degree of care it applies to its own confidential information of like
importance, and in any event no less than a reasonable degree of care.

### NDA-MUT N-3.4 — Mutual Non-Disclosure Agreement (Standard Form) (v3.4, effective 2024-07-01)
Section N-3 Obligations of the Recipient

The Recipient may disclose Confidential Information to those of its
officers, employees, professional advisers and group companies who need to know
it for the Permitted Purpose, provided that the Recipient ensures each such
person is bound by obligations of confidence no less onerous than those in this
Agreement and remains liable for any breach by them.


## Artefact under review

```
# N-5

> Module NdaSurvival

##

```catala-metadata
declaration scope SurvivalEnd:
  input effective_date content date
  input termination_notice_date content optional of date
  input is_personal_data content boolean
  input date_ceased_to_hold content optional of date
  input information_is_trade_secret content boolean
  input information_remains_trade_secret content boolean
  input assessment_date content date
  output agreement_end_date content date
  output survival_end_date content optional of date
  output obligations_subsist content boolean
```

| NO-CLAUSE

```catala
scope SurvivalEnd:
  date round up
  assertion agreement_end_date >= effective_date
  assertion
    (match survival_end_date with pattern
     -- Absent: true
     -- Present content ends: ends >= agreement_end_date)
```

## N-3

| NDA-MUT N-3.1 (005-mutual-nda.md:45)
|
| The Recipient will keep the Confidential Information confidential and will
| not disclose it to any person except as permitted by this Agreement.

| NDA-MUT N-3.2 (005-mutual-nda.md:48)
|
| The Recipient will use the Confidential Information solely for the
| Permitted Purpose.

```catala
scope SurvivalEnd:
  label n3_in_term definition obligations_subsist
    under condition
      assessment_date >= effective_date
      and assessment_date <= agreement_end_date
    consequence equals true
```

| NDA-MUT N-5.1 (005-mutual-nda.md:71)
|
| This Agreement takes effect on the Effective Date and continues for a
| period of 2 years, unless terminated earlier by either party on 30 days'
| written notice.

```catala
scope SurvivalEnd:
  label n5_1_before_effect definition obligations_subsist
    under condition assessment_date < effective_date
    consequence equals false
```

## N-5

| NDA-MUT N-5.1 (005-mutual-nda.md:71)
|
| This Agreement takes effect on the Effective Date and continues for a
| period of 2 years, unless terminated earlier by either party on 30 days'
| written notice.

```catala
scope SurvivalEnd:
  label n5_1_term definition agreement_end_date
    equals effective_date + 2 year

  label n5_1_notice exception n5_1_term definition agreement_end_date
    under condition
      (match termination_notice_date with pattern
       -- Absent: false
       -- Present content notice:
            notice >= effective_date
            and notice + 30 day < effective_date + 2 year)
    consequence equals
      (match termination_notice_date with pattern
       -- Absent: effective_date + 2 year
       -- Present content notice: notice + 30 day)
```

| NDA-MUT N-5.2 (005-mutual-nda.md:75)
|
| The obligations of confidence in N-3 survive expiry or termination of this
| Agreement and continue for a period of 3 years from the date of expiry or
| termination.

```catala
scope SurvivalEnd:
  label n5_2 definition survival_end_date
    equals Present content (agreement_end_date + 3 year)

  label n5_2_subsist definition obligations_subsist
    under condition assessment_date > agreement_end_date
    consequence equals assessment_date <= agreement_end_date + 3 year
```

| NDA-MUT N-5.3 (005-mutual-nda.md:79)
|
| By way of exception to N-5.2, the obligations of confidence in respect of
| Confidential Information which constitutes a trade secret continue for so
| long as that information remains a trade secret.

```catala
scope SurvivalEnd:
  label n5_3 exception n5_2 definition survival_end_date
    under condition
      information_is_trade_secret and not is_personal_data
    consequence equals Absent

  label n5_3_subsist exception n5_2_subsist definition obligations_subsist
    under condition
      assessment_date > agreement_end_date
      and information_is_trade_secret
      and not is_personal_data
    consequence equals information_remains_trade_secret
```

| NDA-MUT N-5.4 (005-mutual-nda.md:83)
|
| By way of exception to N-5.2, the obligations of confidence in respect of
| personal data continue for so long as the Recipient holds that personal
| data.

```catala
scope SurvivalEnd:
  label n5_4 exception n5_2 definition survival_end_date
    under condition
      is_personal_data and not information_is_trade_secret
    consequence equals
      (match date_ceased_to_hold with pattern
       -- Absent: Absent
       -- Present content ceased:
            Present content (Date.max of agreement_end_date, ceased))

  label n5_4_subsist exception n5_2_subsist definition obligations_subsist
    under condition
      assessment_date > agreement_end_date
      and is_personal_data
      and not information_is_trade_secret
    consequence equals
      (match date_ceased_to_hold with pattern
       -- Absent: true
       -- Present content ceased:
            assessment_date <= (Date.max of agreement_end_date, ceased))
```

## N-5.3 N-5.4

| NDA-MUT N-5.3 (005-mutual-nda.md:79)
|
| By way of exception to N-5.2, the obligations of confidence in respect of
| Confidential Information which constitutes a trade secret continue for so
| long as that information remains a trade secret.

| NDA-MUT N-5.4 (005-mutual-nda.md:83)
|
| By way of exception to N-5.2, the obligations of confidence in respect of
| personal data continue for so long as the Recipient holds that personal
| data.

```catala
scope SurvivalEnd:
  label n5_34 exception n5_2 definition survival_end_date
    under condition
      information_is_trade_secret and is_personal_data
    consequence equals
      (if information_remains_trade_secret then Absent
       else
         (match date_ceased_to_hold with pattern
          -- Absent: Absent
          -- Present content ceased:
               Present content (Date.max of agreement_end_date, ceased)))

  label n5_34_subsist exception n5_2_subsist definition obligations_subsist
    under condition
      assessment_date > agreement_end_date
      and information_is_trade_secret
      and is_personal_data
    consequence equals
      information_remains_trade_secret
      or (match date_ceased_to_hold with pattern
          -- Absent: true
          -- Present content ceased:
               assessment_date <= (Date.max of agreement_end_date, ceased))
```

```

## How to execute the artefact

{
  "how": "lks.catala_runner.run_scope(path, scope, inputs) -> output dict",
  "path": "catala/modules/NdaSurvival.catala_en",
  "scopes": {
    "SurvivalEnd": {
      "input": [
        "effective_date",
        "termination_notice_date",
        "is_personal_data",
        "date_ceased_to_hold",
        "information_is_trade_secret",
        "information_remains_trade_secret",
        "assessment_date"
      ],
      "output": [
        "agreement_end_date",
        "survival_end_date",
        "obligations_subsist"
      ],
      "internal": [],
      "context": []
    }
  }
}