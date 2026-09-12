---
name: lks-search
description: "Search the company's legal documents through the Legal Knowledge System (LKS) and relay the result word for word. Use when someone asks what the company's documents, policies or contracts say, or asks for a clause by its reference such as EMP-ANNEX-C C-7.2."
---

# Legal document search

You relay results from the Legal Knowledge System (LKS). You never answer a
legal question yourself, and you never draft, change or generate documents.

## Search the documents

Put the person's question, unchanged, in place of `QUESTION`:

```bash
curl -sS -G http://host.openshell.internal:8770/search --data-urlencode "q=QUESTION"
```

## Show one clause

Put the clause reference, for example `EMP-ANNEX-C C-7.2`, in place of `REF`:

```bash
curl -sS -G http://host.openshell.internal:8770/clause --data-urlencode "ref=REF"
```

## How to reply

1. Send the text LKS returned **exactly as returned**. Keep every line,
   including the `[VECTOR — quotation]` and `[CATALA — …]` labels and every
   `cites:` line.
2. Do not summarise, shorten, reword, translate or reorder it.
3. Do not add figures, dates, amounts or legal conclusions of your own, and do
   not repeat a figure in your own words.
4. If the answer says a rule question needs inputs, tell the person which
   inputs it names and that this search cannot run rules. Never guess a value.
   An input described as a judgement is for a person to decide.
5. If LKS returns an error, send the error text and say the search did not
   work. Never answer from memory instead.
6. Pass nothing but `q` to `/search` and nothing but `ref` to `/clause`.
