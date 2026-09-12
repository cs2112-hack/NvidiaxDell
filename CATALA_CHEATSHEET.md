# Catala cheat sheet — load this before writing or repairing any Catala

Every agent and every runtime prompt loads this file. Catala is **never** hand-written and
never read back to a user: generate → `catala typecheck` → repair → `clerk test`.

Sourced from the compiler and example repos, both cloned 2026-09-12:

- `CatalaLang/catala` @ `b7623302` (2026-09-11) — `doc/syntax/syntax_en.catala_en` is the
  authoritative syntax list; `tests/` holds the real error messages quoted below.
- `CatalaLang/catala-examples` @ `ebe5e7c2` (2026-09-11) — `us_tax_code/`,
  `NSW_community_gaming/`, `clerk.toml`.
- Version is post-`1.2.0` (master `CHANGELOG.md` reads "Changes since 1.2.0"). **Pin the
  compiler version**; error text and `clerk.toml` fields have moved recently (the
  `target.dependencies` field and the removal of `target.include_sources` are post-1.2.0).
- The modules in `corpus/catala/` were built and verified on **stock catala/clerk 1.2.1**
  from opam. The project's `main` branch vendors the same upstream commit **with seven
  patches** (`catala/PATCHES.md`); when working there, prefer that compiler. Two patches
  matter while writing Catala: a fence label other than ```` ```catala ````/
  ```` ```catala-metadata ```` is silently treated as prose on stock (patched: it warns and
  names the label), and `catala proof --fail-on-unproven` gives static conflict/gap
  detection with counterexamples and a CI-gating exit code.

## 0. Non-negotiables

1. **Generated, then typechecked.** No module merges until `catala typecheck` is clean and
   `clerk test` is green.
2. **`money` for money, never decimal or float.** Rates are `decimal`, counts are `integer`,
   deadlines are `date`/`duration`. Mixed arithmetic is a type error, by design.
3. **One module per agreement, not per clause type.** Conflict detection is only honest if
   two documents that collide define the *same variable in the same scope*; two different
   agreements must live in two modules so they cannot collide.
4. **Clause text sits directly above the code it encodes**, as literate prose with the
   `doc_id` and section number. That prose is what the chat component quotes as the clause
   that decided an answer.
5. **A conflict with no precedence clause is left to fail.** Do not invent a priority to
   make the compiler happy — the error *is* the product feature.

## 1. File and block structure

````markdown
# Heading — law/contract text is plain markdown between the code blocks

> Module Liability_cap_acme_northwind     # this file is a module (file name must match)
> Using Us_tax_code as T                  # import another module under an alias
> Include: preamble.catala_en              # textual include (NOT for files declaring a module)

```catala-metadata
declaration structure ...                 # declarations only; shown in generated docs
```

```catala
scope ...                                 # code
```

```catala-test-cli
$ catala test-scope Test1
┌─[RESULT]─ Test1 ─
│ Computation successful!
└─
```
````

- `#` inside a code block is a comment. `#[...]` is an attribute (`#[test]`,
  `#[error.message = "…"]`, `#[debug.print = "…"]`).
- A file declaring a module must be reached with `Using`, never `> Include:` — otherwise:
  *"A file that declares a module cannot be used through the raw '> Include' directive."*
- Module name must match the file name, or: *"Module declared as X, which does not match
  the file name …"*.

## 2. Types and literals

```catala
declaration x content boolean equals true
declaration x content integer equals 65536
declaration x content decimal equals 65536.262144
declaration x content decimal equals 37%
declaration x content money equals $1,234,567.89
declaration x content date equals |2024-04-01|
declaration x content duration equals 254 day + -4 month + 1 year
declaration x content optional of money equals Present content $34
declaration x content optional of money equals Absent
declaration x content list of integer equals [ 12; 24; 36 ]
declaration x content (date,money,decimal) equals (|2024-04-01|, $30, 1%)
declaration x content Struct1 equals Struct1 { -- fld1: 9 -- fld2: 7% }
declaration x content Enum1 equals Case1 content 12
declaration x content Enum1 equals Case2
declaration f content decimal depends on x content money, y content decimal
  equals y * x / $12.0
```

Money literals use `$` and comma groups. Dates are `|YYYY-MM-DD|`. Percentages are decimals.

## 3. Declarations

```catala
declaration structure Struct1:
  data fld1 content integer
  data fld2 content decimal

declaration enumeration Enum1:
  -- Case1 content integer
  -- Case2

## Doc comment for the scope
declaration scope Scope1:
  internal var1 content integer
    state before
    state after
  internal var2 condition          # a boolean defined with `rule`, not `definition`
  sub1 scope Scope0                # sub-scope instance
  output var3 content integer
  input var4 content integer
  input output var5 content integer
  context var6 content integer     # a default that the caller may override
  context output var7 content integer
  output sub2 scope Scope0

declaration const content decimal equals 17.1
declaration square content decimal depends on x content decimal equals x * x
```

Qualifier picks itself: **`input`** = supplied by the caller (this is the input signature the
chat component elicits), **`output`** = returned, **`internal`** = neither, **`context`** =
overridable default, **`condition`** = boolean defined by `rule … consequence fulfilled`.

## 4. Operators

```catala
not a        a and b       a or b      # "or otherwise"    a xor b
- a          a + b         a - b       a * b               a / b
a = b        a != b        a < b       a <= b   a > b   a >= b
decimal of 44        money of 23.15        round of $9.99
Date.get_month of |2003-01-02|      Date.first_day_of_month of |2003-01-02|
a +! b   # integer      a +. b   # decimal      a +$ b   # money      a +^ b   # duration
```

The typed variants (`+!`, `+.`, `+$`, `+^`) exist to disambiguate; reach for them when the
plain operator gives an "Incompatible types" error.

## 5. Definitions, rules, exceptions

```catala
scope Scope1:
  definition x equals 0

scope Scope1 under condition var1 >= 2:        # condition applying to the whole block

  definition var1 under condition 0 consequence equals 0

  rule var2 under condition var1 >= 2 consequence fulfilled
  rule var2 under condition false consequence not fulfilled

  definition f of x, y equals 0                # function definition

  label lbl1 definition var1 equals 0          # name a definition
  exception lbl1 definition var1 equals 0      # exception to that named definition
  exception definition var1 equals 0           # exception to the single other definition

  definition var1 state before equals 0
  assertion 0
  date round down                              # or: date round up
```

**Execution semantics** (this is the whole engine): gather every definition of a variable,
keep the ones whose condition holds. Zero → `no applicable rule to define this variable`.
One → use it. Several → if exactly one is an exception to all the others, use it; otherwise
`conflict between multiple valid consequences`.

Contract mapping: base clause → `definition`; carve-out → `exception` on the base's `label`;
carve-out to the carve-out → `exception` on the carve-out's label; order-of-precedence
clause → the authority for making the superseding document's definition the exception.

## 6. Expressions

```catala
let x equals 36 - 5 in ...
match expr with pattern
  -- Case1 content x : 0
  -- Case2 : 0
  -- anything : 0                  # wildcard must come last
expr with pattern Case1            # boolean test
expr with pattern Case1 content x and x >= 2
if cond then a else b
struc1 but replace { -- fld2: 8% }
struc1.fld2        tuple1.2        sub1.var0
f of $44.50, 1/3
output of Scope1 with { -- fld1: 9 -- fld2: 15% }
var1 state before
assertion x > 0
#[error.message = "err"] impossible
#[debug.print = "message"] 0
```

## 7. Lists (condensed)

```catala
lst contains 3                                  exists x among lst such that x > 2
for all x among lst we have x > 2               map each x among lst to x + 2
list of x among lst such that x > 2             map each x among lst such that x > 2 to x - 2
map each (x, y) among (lst1, lst2) to x + y     lst1 ++ lst2
sum integer of lst                              number of lst
maximum of lst or if list empty then -1
content of x among lst such that x.fld1 = 0
content of x among lst such that x * x is minimum or if list empty then -1
sort lst in decreasing order
sort all x among lst in increasing order of x.fld2 and then -x.fld1
combine all x among lst in acc initially 0 with acc + x
```

## 8. Three golden examples from the official repo

### (a) Law text above code, and an exception with a condition

`catala-examples/us_tax_code/section_121.catala_en` — I.R.C. § 121(b)(2)(A), the closest
structural analog to a contract clause with a carve-out. Note the statute text immediately
above the block, the `rule … consequence fulfilled` for the condition variable, and the two
`exception`s that fire together off one condition.

```catala
# ##### (A) $500,000 Limitation for certain joint returns
#
# Paragraph (1) shall be applied by substituting "$500,000" for "$250,000" if—
# (i) either spouse meets the ownership requirements of subsection (a) …
# (ii) both spouses meet the use requirements of subsection (a) …
# (iii) neither spouse is ineligible for the benefits of subsection (a) … by reason of
#       paragraph (3).

scope Section121TwoPersons:
  rule section_121_b_2_A_condition
  under condition
    (return_type with pattern JointReturn)
    and                                               # i)
    ( section121Person1.requirements_ownership_met
      or section121Person2.requirements_ownership_met )
    and                                               # ii)
    ( section121Person1.requirements_usage_met
      and section121Person2.requirements_usage_met )
    and (not (section121Person1.section_121_b_3_applies))   # iii)
    and (not (section121Person2.section_121_b_3_applies))
  consequence fulfilled

  exception
  rule section121a_requirements_met
  under condition section_121_b_2_A_condition
  consequence fulfilled

  exception
  definition gain_cap
  under condition section_121_b_2_A_condition
  consequence equals
    $500,000
```

Module assembly, from `us_tax_code/us_tax_code.catala_en`:

```catala
> Module Us_tax_code
> Include: preamble.catala_en
> Include: section_121.catala_en
> Include: section_132.catala_en
```

### (b) The conflict, which is our detector

`catala/tests/exception/bad/two_exceptions.catala_en` — two exceptions to the same base with
no relative priority. **This is exactly the MSA-vs-Amendment shape.** The compiler emits a
static warning *and* a runtime error, and both name every colliding location:

```catala
declaration scope A:
  output x content integer

scope A:
  label base_x
  definition x equals 0

  exception base_x
  definition x equals 1

  exception base_x
  definition x equals 2
```

```text
┌─[WARNING]─
│  Multiple conflicting definitions:
│  these have the same conditions and will always trigger a conflict at runtime.
├─➤ …:12.14-15:   definition x equals 1
├─➤ …:15.14-15:   definition x equals 2
┌─[ERROR]─
│  During evaluation: conflict between multiple valid consequences for
│  assigning the same variable.
├─➤ …:12.23-24 and …:15.23-24
#return code 123#
```

Note the actual wording: the runtime error is **"conflict between multiple valid
consequences for assigning the same variable"**, and the compile-time signal is the
**"Multiple conflicting definitions"** warning. Match on those strings, not on the phrase
"conflicting definitions" alone.

### (c) A test scope, in English, contract-shaped

`catala-examples/NSW_community_gaming/tests/test_nsw_art_union.catala_en` — `#[test]` on the
declaration, a sub-scope instance, a struct literal of inputs, an `assertion`, and the
expected output captured in a `catala-test-cli` block:

````markdown
> Include: ../nsw_art_union.catala_en

## Test1
```catala
#[test]
declaration scope Test1:
  my_gaming scope GamingAuthorized

scope Test1:
  definition
    my_gaming.artUnion
  equals
    ArtUnion {
      -- grossProceeds: $50000
      -- typeOrg: NonProfit
      -- proceedsToBenefitingOrg: $25000
      -- totalValueOfThePrizes: $45000
      -- maxAmountMoneyAsSeparatePrize: $100
      -- holdsAuthority: true
    }
  assertion my_gaming.authorized = true
```

```catala-test-cli
$ catala test-scope Test1
┌─[RESULT]─ Test1 ─
│ Computation successful!
└─
```
````

Calling a scope directly instead of instantiating it (`us_tax_code/tests/…` also does this):

```catala
scope Test:
  definition computation equals
    output of IncomeTaxComputation with {
      -- individual: Individual { -- income: $20,000 -- number_of_children: 0 }
    }
```

## 9. Testing and project layout

- **`#[test]` and the `catala-test-cli` block are both current** — the attribute marks the
  scope, the fenced block records the expected output. (The field guide's claim that
  `#[test]` does not exist is wrong for this compiler version: see
  `catala/tests/attributes/good/*.catala_en` and all of `NSW_community_gaming/tests/`.)
- **`#[test]` means "this scope must evaluate cleanly".** A test whose expected result *is*
  an error — a conflict, for instance — carries only the `catala-test-cli` block, with no
  `#[test]` on the scope. With the attribute, clerk runs the scope separately and the
  non-zero exit fails the suite even though the recorded output matches.
- Workflow, verbatim from `catala/tests/README.md`: write the test, add a
  ```` ```catala-test-cli ```` block containing only `$ catala test-scope A`, then run
  `clerk test <file> --reset` to populate the expected output, which later runs diff against.
- Commands: `clerk test` (whole suite) · `clerk test <file>` · `clerk test <file> --reset` ·
  `clerk test --reset` (mass reset — review the diff) · `clerk run <file> --scope=Test` ·
  `clerk runtest <file>` (print, no diff) · `clerk build` · `clerk report` · `clerk ci` ·
  `clerk clean` · `clerk list-vars`. Compiler: `catala typecheck` · `catala test-scope S` ·
  `catala Interpret -s S` (`test-scope` behaves like `Interpret -s` but accepts test flags).
- **`--message-format=gnu` is the repair loop's friend**: errors collapse to one parseable
  line, `file:line.col-col: [ERROR] message`, instead of the boxed display.
- Real `clerk.toml` shape (from `catala-examples`):

```toml
[project]
name = "catala-examples"
include_dirs = [ "us_tax_code", "smic" ]

[[target]]
name = "us-tax-code"
modules = ["Us_tax_code"]
backends = ["ocaml"]
tests = ["us_tax_code/tests/"]
dependencies = ["smic"]        # post-1.2.0 field
```

## 10. Compiler errors → cause → fix

All messages below are quoted from `catala/tests/**`; the left column is what to match on.

| Message | Cause | Fix |
|---|---|---|
| `During evaluation: conflict between multiple valid consequences for assigning the same variable.` | Two definitions apply and neither is an exception to the other | If the documents state a precedence, make the superseding one an `exception` to the other's `label`. If they don't, **stop and report the conflict** with both locations. |
| `Multiple conflicting definitions: these have the same conditions and will always trigger a conflict at runtime.` (WARNING) | Same, caught statically | Same. Treat this warning as a blocking finding at ingest. |
| `During evaluation: no applicable rule to define this variable in this situation.` | Every condition was false and there is no unconditional base | Add the base `definition` (the clause's default), or widen the last condition. |
| `This exception can refer to several definitions. Try using labels to disambiguate.` | Bare `exception` with more than one candidate definition | Add `label` to the intended base and write `exception <label>`. |
| `This exception does not have a corresponding definition.` | `exception` with no base definition in scope | Add the base `definition`, or drop the `exception` keyword. |
| `Unknown label for the scope variable x: "L".` | `exception L` where `L` was never declared | Declare `label L` on the base, or fix the typo. |
| `Cannot define rule as an exception to itself` | `label L` and `exception L` on the same definition | Remove one; exceptions point at a *different* definition. |
| `Exception cycle detected when defining x: each of these N exceptions applies over the previous one, and the first applies over the last.` | Circular exception chain | Break the cycle; exception order must be a strict priority. |
| `Incompatible types: got decimal, expected integer` (also money/bool/enum variants) | Mixed numeric types | Convert explicitly (`decimal of`, `money of`) or use the typed operator (`+$`, `+.`, `+!`, `+^`). Never silently coerce money. |
| `Comparison cannot be resolved at this point: the type of the operands is not fully known.` | Polymorphic comparison with no annotation | Annotate the variable's `content` type. |
| `During evaluation: ambiguous comparison between durations in different units (e.g. months vs. days).` | Comparing `1 month` with `30 day` | Normalise to days, or restate the rule in one unit. |
| `During evaluation: ambiguous date computation with no rounding mode specified.` | Date arithmetic that can land on a nonexistent day (e.g. +1 month from Jan 31) | Add `date round down` (or `up`) to the scope. |
| `During evaluation: a value is being used as denominator in a division and it computed to zero.` | Divide by zero, e.g. a ratio over a zero base | Guard with a condition, or restate the rule to avoid the division. |
| `During evaluation: an assertion doesn't hold.` | An `assertion` failed | The inputs violate an invariant the clause assumes — usually the test is right and the encoding is wrong. |
| `Wildcard must be the last match case.` | `-- anything :` before other cases | Move the wildcard last. |
| `"x": unknown identifier for a variable of scope S` | `definition x` where `x` is not declared in the scope | Declare it in `declaration scope S` with the right qualifier. |
| `Duplicate constructor X in enumeration E` | Repeated enum case | Rename or delete the duplicate. |
| `Function argument name mismatch between declaration ('x') and definition ('y').` | `definition f of y` against `depends on x` | Use the declared parameter names. |
| `The variable f is used in one of its definitions (Catala doesn't support recursion).` | Recursive definition | Restructure; use a list fold (`combine all …`) instead. |
| `Cyclic dependency detected between the following variables of scope A: …` | Variables defined in terms of each other | Break the cycle, or use `state before`/`state after`. |
| `Type M.E is private and cannot be used here.` | Using a type from another module that isn't exported | Export it from the module, or re-declare locally. |
| `Syntax error at "X": » expected 'under condition' followed by a condition, 'equals' followed by the definition …` | Malformed `definition` | Shape is `definition v under condition C consequence equals E` or `definition v equals E`. |
| `Please add parentheses to explicit which of these operators should be applied first.` | Mixed `and`/`or` without parens | Parenthesise. |
| `The standard library module Stdlib_en could not be found at "_build/libcatala".` | `catala` run directly in a project that has never been built | Run `clerk start` once, then `clerk build`/`clerk test`; prefer clerk over bare `catala`. |
| `Unbound module "M.SomeType"` from `ocamlc` during `clerk test` | The type was declared in a ```` ```catala ```` block, so the module does not export it | Move every `declaration` a caller needs into a ```` ```catala-metadata ```` block. |
| `Parsing error after token " ": what comes after could not be recognised` on a line starting `>` | A markdown blockquote — `>` at line start is a Catala directive (`> Module`, `> Using`, `> Include:`) | Indent quoted clause text by two spaces instead (four would make it a code block). |
| `Syntax error at "month": unexpected token. Those are valid at this point: "sum", "output".` | A variable named after a duration unit (`day`, `month`, `year`) | Rename it (`service_month`). |
| `Unused variable: x does not contribute to computing any of scope S outputs.` (WARNING) | An `input` nothing reads | Delete the input or use it; clerk surfaces this on every build. |
| `Unexpected error: Invalid_argument("Json_encoding.construct: consequence of non exhaustive Json_encoding.string_enum …")` | **catala 1.2.1 bug**: `--output-format=json` cannot serialise an enum-valued output | Parse the human `┌─[RESULT]─` block instead, or move past 1.2.1 (fixed upstream in PR #1058). |

## 11. Running a scope, and getting the branch that fired

These four commands are the whole runtime surface of the Catala half. All of them are
compiler output, so nothing in an answer has to be trusted to a model.

```bash
# the input signature -- this is what "missing facts" should be elicited from
clerk json-schema src/liability_cap_acme_northwind.catala_en --scope=LiabilityCap

# execute with typed inputs
clerk run src/liability_cap_acme_northwind.catala_en --scope=LiabilityCap \
  --input='{"fees_paid_last_12mo": 240000, "annual_fees": 240000,
            "claim_type": "Confidentiality", "claim_date": "2026-09-15"}'
# ┌─[RESULT]─ LiabilityCap ─  │ cap = $1,200,000.00  │ uncapped = false

# which definition fired, with the law heading above it
catala interpret src/liability_cap_acme_northwind.catala_en \
  --stdlib=_build/libcatala -I src --scope=LiabilityCap --input='…' --trace
# [LOG] ☛ Definition applied: … └─ SYN-003 § 5.2 — the Order Form's supercap
# [LOG] ≔  LiabilityCap.cap: $1,200,000.00

# the whole exception tree, machine-readable (labels, positions, law_headings, conditions)
clerk exceptions src/liability_cap_acme_northwind.catala_en \
  -s LiabilityCap -v cap --output-format=json
```

`--input` also accepts a file, or `-` for stdin. `--output-format=json` on `clerk run` is
the clean path for values but see the enum bug in § 10.

## 12. clerk.toml as 1.2.1 actually accepts it

```toml
[project]
include_dirs = ["src"]          # allowed: build_dir, catala_exe, catala_opts,
                                # default_targets, include_dirs, target_dir
                                # NOT `name` -- that is a post-1.2.0 field

[[target]]
name     = "liability"
modules  = ["Legal_types", "Liability_cap_acme_northwind"]
backends = ["ocaml"]
tests    = ["tests/"]
```

`clerk start` once per project creates `_build/libcatala`. Subcommands: `build`, `test`,
`run`, `runtest`, `typecheck`, `exceptions`, `json-schema`, `report`, `ci`, `clean`,
`list-vars`.

## 13. Modelling rules that the compiler taught us

- **Sibling exceptions to the same label must have mutually exclusive conditions.** Two
  exceptions to one base that can both hold produce a runtime conflict, not a priority
  order. Either guard the conditions (`… and not excluded_claim`) or chain the exceptions
  (`exception` on the *other exception's* label).
- **A base definition must always apply**, or you get "no applicable rule": write the
  unconditional base first, then narrow with exceptions.
- **`optional of T` works** (`Present content 80.0` / `Absent`, matched with
  `match x with pattern -- Present content c: … -- Absent: …`). It is the clean way to say
  "this document sets no ceiling".
- **Booleans as `condition` + `rule … consequence fulfilled`** default to false when no
  rule applies; `content boolean` + `definition` does not.
- **`money * decimal` is fine**; `decimal of <integer>` converts. Never coerce money
  through a float.
- **Dates have no time of day.** "Within 72 hours" becomes `+ 3 day`; say so in the prose
  above the code. Month arithmetic on a first-of-month date needs no rounding mode.

## 14. The repair loop

1. Generate the module with the clause text above each block, one module per agreement.
2. `catala typecheck --message-format=gnu` → parse `file:line.col-col: [ERROR] msg`.
3. Look the message up in § 10, apply the fix, retry. Cap the attempts (25 per module) and
   record attempts / counterexamples / fixed for the demo counter.
4. `clerk test <file> --reset` once, then `clerk test` from then on — a reset that changes an
   existing expected output is a regression until proven otherwise.
5. **Never** resolve a `conflict between multiple valid consequences` by inventing a
   priority. If no document states precedence, that error is the answer.
