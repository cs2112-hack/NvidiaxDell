# catala/

Ten modules plus two that are expected to fail, `clerk test` green at 96/96 on
catala 1.2.1. `../SCOPES.md` is the contract; `../gold/fact_patterns.jsonl` holds the
expected values the tests assert.

```bash
eval "$(opam env --switch=catala)"
clerk test                       # whole suite
clerk test tests/test_liability_cap_acme_northwind.catala_en
clerk test <file> --reset        # re-record expected output (review the diff!)
clerk typecheck src/<module>.catala_en
```

Layout:

```
clerk.toml            [project] include_dirs + one [[target]] listing every module
src/legal_types.catala_en          shared enums (ClaimType, WorkState, Department, …)
src/<rule>_<party>.catala_en       one module per agreement, never per clause type
src/conflict_*.catala_en           modules that are expected to report a conflict
tests/test_<module>.catala_en      fact patterns with assertions + recorded output
```

Conventions, all of them enforced by something:

- The **original clause text sits directly above the code** that encodes it, as indented
  prose with the manifest `doc_id` and section number in the heading. `catala interpret
  --trace` and `clerk exceptions` report those headings, which is how the chat component
  shows "the clause that decided it" without a model in the loop.
- **One module per agreement.** Two documents can only collide inside one agreement's
  scope, which is what makes the four calibration pairs in `../gold/conflicts.jsonl` come
  back negative.
- **A precedence clause becomes a `label`/`exception` pair**; a collision with no
  precedence clause is left to fail. `src/conflict_*.catala_en` are those failures, with
  the error output recorded as the expected result.
- Declarations a caller needs go in a ```` ```catala-metadata ```` block, money is `money`,
  and `#[test]` is only for scopes that must evaluate cleanly. See
  `../../CATALA_CHEATSHEET.md` §§ 10–13 for the errors behind each of those rules.
- Catala is generated then repaired against `catala typecheck`. On this box the generator
  was the session (no `$LOCAL_LLM_URL`); the verifier was always the compiler.
