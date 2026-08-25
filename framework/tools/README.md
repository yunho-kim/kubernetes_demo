# framework/tools — external FL / APR tool adapters

Each subdirectory integrates one third-party tool with the cvebench contract.
Tools run **on the host, without a dedicated container**; whatever they need is
installed into a private, git-ignored location under the tool directory by its
`prepare.sh`, so the harness works on any machine after one preparation step.

**The full interface specification is in [`docs/INTEGRATION.md`](../../docs/INTEGRATION.md).**

```
framework/tools/<tool>/
├── tool.json         # {"name","kind":"fl"|"apr","description","reference","url","granularity"}
├── prepare.sh        # optional one-off setup on a new machine (venv, pip, downloads …)
├── requirements.txt  # pinned dependencies (when the tool is Python)
├── run               # adapter:  FL : run <results-dir> -o <out-dir> [--metric M] [args]
│                     #           APR: run <results-dir> <workdir> -o <out-dir> [args]
└── .venv/            # created by prepare.sh, never committed
```

`cvebench prepare -t <tool>` runs `prepare.sh`; `cvebench fl -t <tool>` /
`cvebench apr -t <tool>` run the adapter; `cvebench summary` compares every
tool's `ranking.json` / `validation.json`.

| tool | kind | what | reference |
| --- | --- | --- | --- |
| `sbfl` (built in, `framework/bin/sbfl-rank`) | fl | Ochiai, Tarantula, Jaccard, D*, Op2, Barinel, GP13, Kulczynski2, Ample | Abreu et al. 2007; Jones & Harrold 2005; Naish et al. 2011; Yoo 2012 … |
| `flitsr` | fl | FLITSR / FLITSR* multi-fault SBFL over the GZoltar spectrum | D. Callaghan, B. Fischer, "Improving Spectrum-Based Localization of Multiple Faults by Iterative Test Suite Reduction", ISSTA 2023. https://github.com/DCallaz/flitsr |
| `mbfl` | fl | Mutation-based FL: Mull mutants baked into the buggy program (clang IR plugin), killed by the bug's reg-tests; Metallaxis (Ochiai, D*) and MUSE line scores. `prepare.sh` builds the Debian+clang+Mull+vtest base image; the tool logic runs on the host | M. Papadakis, Y. Le Traon, "Metallaxis-FL", STVR 2015; S. Moon et al., "Ask the Mutants" (MUSE), ICST 2014; A. Denisov, S. Pankevich, "Mull it over", ICSTW 2018. https://github.com/mull-project/mull |
| `llmao` | fl | Test-free LLM FL: CodeGen (350M/6B/16B) final hidden states + LLMAO's trained bidirectional adapter (Devign/C checkpoint), scored per line on the files executed by failing tests; plain, coverage-filtered and Op2-tie-broken rankings. `prepare.sh` makes a venv (torch, transformers 4.x) and clones upstream at a pinned commit | A. Z. H. Yang, C. Le Goues, R. Martins, V. J. Hellendoorn, "Large Language Models for Test-Free Fault Localization", ICSE 2024. https://github.com/squaresLab/LLMAO |
| `autofl` | fl | AutoFL ported to C: an LLM agent reads the failing `.vtc` test + vtest failure output and calls `get_failing_tests_covered_files / …_functions_for_file / get_code_snippet / get_comments` (tree-sitter function index), then names culprit functions; R runs are combined by upstream's vote scoring and expanded to lines. Any OpenAI-compatible endpoint (`OPENAI_BASE_URL`, `OPENAI_API_KEY`, `AUTOFL_MODEL`); `--protocol text` for servers without tool calling; CVE ids in tests are redacted by default | S. Kang, G. An, S. Yoo, "A Quantitative and Qualitative Evaluation of LLM-Based Explainable Fault Localization", FSE 2024. https://github.com/coinse/autofl |
| `oracle` | apr | reference adapter: emits the upstream fix (plausible) and an empty patch (not) — pipeline check + template | — |
