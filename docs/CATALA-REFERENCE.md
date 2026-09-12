# Catala 1.2.1 — operational reference

Verified against Catala **1.2.1** (released 2026-07-06) by reading the Catala
Book (`book.catala-lang.org`), the compiler source at tag `1.2.1`, and the
compiler's own test suite. Everything here is what an implementer in this repo
needs; the "Traps" section is the part that will actually bite you.

## Files and literate structure

A `.catala_en` file is Pandoc-flavoured Markdown. Prose is law, code is fenced.

    ```catala           <- code, PRIVATE to the module
    ```catala-metadata   <- code, PUBLIC (exported in the module interface)
    ```catala-test-cli   <- cram test: a command plus its expected output

Fence rules are strict: ```` ```catala ```` at column 0, no space before
`catala`, newline immediately after, closing ```` ``` ```` alone on its line,
no nesting.

Markdown `#`/`##`/`###` headings are **retained by the compiler** and attached
to every source position, surfacing as `law_headings` in JSON output. This is
how an executed result traces back to a clause — mirror the source document's
heading structure.

`foo.catala_en` ≡ `foo.catala_en.md`. Textual inclusion, in free text:

    > Include: other.catala_en

## Modules

    > Module Overtime          <- first thing in the file, free text
    > Using Definitions
    > Using Definitions as D

Module name is CamelCase and must match the filename up to casing
(`overtime.catala_en` ↔ `Overtime`). Reference as `Definitions.Grade`.
Everything is **private** unless it is in a ```` ```catala-metadata ```` block —
the usual confusing failure ("Catala pretends it doesn't know my type") is an
imported type that was declared private.

`> Include:` is textual copy-paste into the *same* module. `> Using` imports a
*separate* module's public interface. Do not confuse them.

## Types

| Type | Literals | Notes |
|---|---|---|
| `boolean` | `true`, `false` | |
| `integer` | `564,614`, `-2` | arbitrary precision; `,` is cosmetic |
| `decimal` | `0.21`, `30%` | **exact rationals**, not floats; `30% = 0.30` |
| `money` | `$12.36` | precision fixed at the cent |
| `date` | `\|1930-09-11\|` | ISO-8601 in vertical bars |
| `duration` | `254 day`, `4 month`, `1 year` | **days and months are incomparable** |
| `list of T` | `[1; 6; -4]` | **semicolon**-separated |
| `optional of T` | `Absent`, `Present content x` | built in |

Casts are always explicit: `integer of x`, `decimal of x`, `money of x`.
decimal→integer truncates; decimal→money rounds to nearest cent.

`integer / integer` yields **`decimal`** (rational division). `date - date`
yields a `duration` in days. `money * decimal` → `money`.

**Date arithmetic is deliberately non-total**: `|2025-01-31| + 1 month` raises
`AmbiguousDateComputation` unless the scope declares `date round up` or
`date round down`. Use the `Date` stdlib (`Date.add_round_down`,
`Date.is_old_enough_rounding_up`, …) rather than hand-rolling `18 * 365 day`.

## Declarations

    declaration structure Individual:
      data birth_date content date
      data income content money

    declaration enumeration TaxCredit:
      -- NoTaxCredit
      -- TaxCreditForIndividual content Individual

    declaration scope OvertimePay:
      input hours_worked content decimal
      input base_hourly_rate content money
      internal tier content decimal
      output overtime_payment content money

Construction / update / access:

    Individual { -- birth_date: |1930-09-11| -- income: $100,000 }
    individual.income
    foo but replace { -- income: $5 }

Pattern matching is exhaustiveness-checked, **outermost-only** (no nested
patterns), `-- anything:` is the catch-all and must be last:

    match credit with pattern
    -- NoTaxCredit: $0
    -- TaxCreditForIndividual content i: i.income * 10%

Structures and enumerations are **not recursive**. `declaration`s cannot be
split across files (only *definitions* can) — keep declarations in a prologue.

## Scope qualifiers

| Qualifier | Semantics |
|---|---|
| `input` | required argument; **cannot be defined inside the scope** |
| `output` | defined inside; returned as a field of the output struct |
| `internal` | neither in nor out |
| `context` | optional input **with an in-scope default**; caller's value wins |
| `input output` / `context output` | argument echoed into the result |

`context` is the sanctioned way to add an exception to a scope variable **from
outside** that scope.

Every scope induces a struct of the same name holding exactly its `output`
variables, usable as a type.

`condition` variables are sugar for a boolean defaulting to `false`, defined
with `rule`/`fulfilled`:

    internal eligible condition
    ...
    rule eligible under condition grade >= 5 consequence fulfilled

which desugars to `definition eligible equals false` plus an *exception* for
the true case. So **every `rule` is already an exception to a `false` base.**

Variable states run in declaration order; inside a definition the bare name
means the **previous** state:

    internal pay content money
      state base
      state after_cap
    ...
    definition pay state after_cap equals if pay > cap then cap else pay

Sub-scopes (static, and the preferred form because their inputs can themselves
carry conditional definitions and exceptions):

    declaration scope Household:
      input individual content Individual
      tax scope IncomeTax            # or: output tax scope IncomeTax
      output total content money
    ...
    scope Household:
      definition tax.individual equals individual
      definition total equals tax.income_tax

Direct call (use inside list iteration):

    output of IncomeTax with { -- individual: i -- current_date: today }

Scope-wide guard and assertions:

    scope Foo under condition current_date >= |2025-01-01|:
      assertion bar + 2 = fizz * 4

## Exception hierarchies — the core

Full form of a definition:

    [label <name>]
    [exception <parent_label>]
    definition <var> [of <params>] [state <s>]
      [under condition <expr> consequence]
      equals <expr>

Evaluation, exactly:

1. Gather every definition of the variable.
2. Keep those whose condition is `true`.
3. **None apply** → runtime error `NoValue` ("no applicable rule").
4. **Exactly one** → use it.
5. **Several** → if one is an exception to all the others that apply, use it;
   otherwise runtime error `Conflict` ("conflict between multiple valid
   consequences").

Multi-level hierarchy via labels. Reusing a label **groups** definitions into
one node (mutually-exclusive siblings at that node):

    label art_2 definition tax_rate
      under condition current_date < |2000-01-01| consequence equals 20%

    label art_2 definition tax_rate            # same label -> same node
      under condition current_date >= |2000-01-01| consequence equals 21%

    label art_3 exception art_2 definition tax_rate
      under condition children >= 2 consequence equals 15%

    label art_4 exception art_3 definition tax_rate
      under condition income <= $10,000 consequence equals 0%

Tree: `{art_2a, art_2b} <- art_3 <- art_4`.

Labels are scoped **per variable**; `exception` cannot point at another
variable's label. Exception cycles are a static error.

## Traps that will bite

1. **Exceptions do NOT inherit their parent's condition.** If the base applies
   `under condition income > $100,000` and you write the exception `under
   condition overseas` alone, the exception fires for *everyone* overseas. You
   must **repeat the parent's condition** in the child. This is the single
   highest-yield encoding bug in this repo's domain and the adversarial
   reviewer is instructed to hunt for it.
2. **Catala is not lazy.** Values not needed for the result are still computed,
   so `impossible` is unsafe as a struct-field placeholder.
3. **Sibling exceptions with overlapping conditions are legal at compile time**
   and blow up at runtime with `Conflict`. The compiler does not prove mutual
   exclusivity. Two definitions with *identical* conditions do get a static
   warning.
4. **Unlabeled exceptions get compiler-generated names** (`exception_to_<parent>`).
   Always write explicit `label`s, or exception-tree diffs are unstable.
5. **Date addition raises** rather than silently rounding. Declare the rounding
   mode per scope.
6. `integer / integer` is decimal division, not integer division.
7. Toplevel `declaration ... equals` constants/functions **cannot carry
   exceptions**. Anything with an exception structure must be a scope.

## CLI

Typecheck:

    catala typecheck FILE.catala_en [-I DIR]
    catala typecheck FILE --check-invariants
    clerk typecheck [FILE|DIR]            # project-wide, builds deps first

Execute a scope with inputs, machine-readably — this is how the chat engine
answers rule questions:

    catala interpret FILE -s ScopeName --input '<json>' -F json --quiet
    catala interpret FILE -s ScopeName --input=-      # stdin
    clerk run FILE --scope=S --input in.json

`--input` accepts a file path, a literal JSON string, or `-` for stdin. With
`--input` present exactly one scope must resolve. Without it, `interpret` runs
every `#[test]` scope. `-F json --quiet` yields the output struct as pure JSON:
`{ "income_tax": 4200.0 }`.

JSON value mapping: `integer` int or string; `decimal` int/number/string
(`"1/3"` exact); `money` int/number/string; `date` `"2026-01-31"` or
`{"year":..,"month":..,"day":..}`; `duration` `{"years":1,"months":2}`; enum
`"A"` or `{"B": 3}`; struct → object.

Input/output JSON Schema (two-element array `[input, output]`):

    catala json-schema FILE -s ScopeName

**Exception tree as structured JSON** — the diffable representation of the
legal structure:

    catala exceptions FILE -s Scope -v variable -F json

Shape: `{scope, variable, is_condition, trees: [{label, rules: [{pos:
{filename, start_line, ..., law_headings}, condition_pos, condition_text}],
exceptions: [ ...recursive... ]}]}`. Root of each tree is a **base case**;
`exceptions` holds its children. `condition_text`/`condition_pos` are absent
for unconditional definitions. Both `-s` and `-v` are required, so this must
be invoked once per (scope, variable).

Tracing: `--trace`, `--trace-format=json` (which rules fired),
`--message-format=gnu`, `#[debug.print]` with `--debug`.

Backends: `catala ocaml|python|java|c`. Exit codes: `0` ok, `123` reported
error, `124` CLI parse error, `125` internal bug.

## Testing

Idiomatic and recommended: a test is a **scope with no inputs** that calls the
scope under test with literal values and asserts:

    #[test]
    declaration scope TestOvertime_Grade5_CIR:
      output result content OvertimePay

    scope TestOvertime_Grade5_CIR:
      definition result equals
        output of OvertimePay with {
          -- hours_worked: 52.0
          -- base_hourly_rate: $30.00
          -- grade: 5
        }
      assertion result.overtime_payment = $450.00

A `#[test]` scope may have only `internal`, `output` and `context` variables.
**A `#[test]` with no assertion always passes** — never emit one.

Run: `clerk test`, `clerk test --verbose`, `clerk test --xml` (JUnit),
`clerk test --backend=python`, `clerk test --reset` (accept current output —
review the diff).

Cram tests (```` ```catala-test-cli ````) are for what assertions cannot
express: error cases, exact traces. `$ catala test-scope Test` is a
clerk-only alias rewritten to `interpret --scope=Test`.

`--autotest` pins compiled-backend results to interpreter results.

## clerk.toml

    [project]
    include_dirs = ["catala/modules"]
    build_dir = "_build"

    [[target]]
    name = "lks"
    modules = ["Definitions", "Overtime"]   # module NAMES
    tests = ["catala/tests/test_overtime.catala_en"]   # FILEs
    backends = ["ocaml"]

## Known gaps in 1.2.1 (verified absent, do not design around them)

- **No JSON/S-expression dump of the surface, desugared, scopelang, dcalc,
  lcalc or scalc AST.** `catala scopelang`/`dcalc` print a *textual* debugging
  verbatim (deterministic and diffable, with default-logic terms shown as
  `⟨cond ⊢ val | … ⊢ ∅⟩`), but it is not structured data. See
  `docs/DECISIONS.md` for how this repo handles AST diffing as a result.
- `catala dependency-graph` does emit real JSON, but its node ids are
  `Hashtbl.hash` of internal UIDs and are **not stable across compilations** —
  canonicalise on node name strings.
- JSON input/output is **interpreter-only**, not wired into compiled backends.
- `#[test]` + `catala-metadata` visibility: the clerk chapter says a test scope
  must be public, but the compiler's own suite uses `#[test]` in a plain
  ```` ```catala ```` block. Resolved empirically in this repo — see
  `docs/DECISIONS.md`.

## Verified compiler bug: an enum-typed output cannot be JSON-encoded

**Catala 1.2.1 cannot serialise a scope output whose type is an enumeration**,
and this makes the scope completely unexecutable through the JSON interface.

Minimal reproduction:

```
declaration enumeration Choice:
  -- Alpha
  -- Beta

declaration scope PlainEnum:
  input flag content boolean
  output pick content Choice
```

    $ clerk run probe.catala_en --scope=PlainEnum --input '{"flag":true}' -F json
    Unexpected error: Invalid_argument("Json_encoding.construct: consequence
    of non exhaustive Json_encoding.string_enum. Strings are: 'Alpha' 'Beta'")

Established by probing:

- It is purely a **serialisation** failure. The same scope runs correctly with
  human output (`pick = Alpha`), so the logic and the exception hierarchy are
  fine; only `-F json` fails.
- It fires for a **bare enum output** and for an **enum nested inside a struct
  output**. Both are unusable.
- Payload-free, two-case enums are enough to trigger it, so it is not about
  payloads or arity.

**Consequence for this repo, and it is not negotiable.** The architecture
answers rule questions by *executing* the scope and reading its JSON output
(`docs/ARCHITECTURE.md`). A scope with an enum-typed output therefore cannot
answer anything at all — it is dead code that typechecks, passes
`clerk test` (assertions run under the interpreter, not through JSON), and
fails only when someone actually asks it a question. `ServiceCreditClaim`
shipped in exactly that state and the whole of MSA-SCH4 L-5 was unanswerable
until an adversarial reviewer tried to execute it.

**Rule:** no scope may have an enum-typed output, directly or nested. Express
the choice as a `boolean`, or as separate boolean outputs, or as an integer
code with the meanings documented in the literate prose. `scripts/check.sh`
enforces this statically, because `clerk test` cannot see it.
