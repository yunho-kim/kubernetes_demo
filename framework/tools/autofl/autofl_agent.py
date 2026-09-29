#!/usr/bin/env python3
"""autofl_agent.py — AutoFL (Kang, An, Yoo, FSE 2024) ported to C, run inside the tool venv.

    autofl_agent.py --input input.json --out-dir <dir> [--reps 5] [--budget 10] [--num-tests 1]
                    [--model M] [--base-url URL] [--protocol tools|text] [--temperature T]
    autofl_agent.py --selftest

Port of coinse/autofl (autofl.py + lib/d4j_interface.py), Java -> C:
  class  -> source file          package -> directory
  method -> C function           signature = "<file>:<name>(<parameter types>)"
The four tools keep upstream's names modulo the class/method words:
  get_failing_tests_covered_files()                -> {directory: [file, ...]}
  get_failing_tests_covered_functions_for_file(f)  -> ["name(params)", ...]
  get_code_snippet(signature)                      -> numbered source lines
  get_comments(signature)                          -> the comment block preceding the function
Protocol (upstream runner.sh defaults): R repetitions, each rotating which failing
test is shown first and showing 1 test; the first tool call is injected without
asking the model; 10 steps, tool calls disabled on the last; then the model is
asked for culprit signatures, one per line (multi-prediction mode).

input.json (written by `run`):
  {"src": <checkout root>, "files": {file: {"failing_lines": [..], "per_test": {test: [..]}}},
   "failing_tests": [{"id","name","snippet","fail_info"}]}
Outputs: functions.json (index of failing-covered functions) and runs/rep<k>.json.
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

FINISH = ("Based on the available information, provide the signatures of the most likely culprit "
          "methods for the bug. Your answer will be processed automatically, so make sure to only "
          "answer with the accurate signatures of all likely culprits (in `ClassName.MethodName(ArgType1, ArgType2, ...)` "
          "format), without commentary (one per line). ")
# C wording of the same instruction
FINISH_C = FINISH.replace("methods", "functions").replace("`ClassName.MethodName(ArgType1, ArgType2, ...)`",
                                                          "`path/to/file.c:function_name(ArgType1, ArgType2, ...)`")
SYSTEM_SUFFIX_C = ("\n\nAfter providing this diagnosis, you will be prompted to suggest which functions would be the "
                   "best locations to be fixed. The answers should be in the form of "
                   "`path/to/file.c:function_name(ArgType1, ArgType2, ...)` without commentary (one per line), as your "
                   "answer will be automatically processed before finally being presented to the user.")

TOOLS = [
    {"name": "get_failing_tests_covered_files",
     "description": "This function retrieves a set of source files covered by failing tests and groups them by their directory names.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "get_failing_tests_covered_functions_for_file",
     "description": "This function takes a file_name as input and returns a list of function names covered by failing tests for the specified file in the program under test.",
     "parameters": {"type": "object", "properties": {"file_name": {"type": "string",
                    "description": "The path of the file in the program under test, e.g., \"src/h1.c\"."}},
                    "required": ["file_name"]}},
    {"name": "get_code_snippet",
     "description": "This function takes a signature as input and returns the corresponding code snippet for the function.",
     "parameters": {"type": "object", "properties": {"signature": {"type": "string",
                    "description": "The signature of the function to retrieve the code snippet for. e.g. \"src/h1.c:h1_parse_msg_hdrs(struct h1m *, ...)\" or just \"h1_parse_msg_hdrs\""}},
                    "required": ["signature"]}},
    {"name": "get_comments",
     "description": "This function takes a signature as input and returns the comment documentation (if available) for the function.",
     "parameters": {"type": "object", "properties": {"signature": {"type": "string",
                    "description": "The signature of the function to retrieve the documentation for."}},
                    "required": ["signature"]}},
]
INITIAL = "get_failing_tests_covered_files"


# ---------------------------------------------------------------- C function index (tree-sitter)
def index_functions(src, files, failing_lines, per_test):
    import tree_sitter_c as tsc
    from tree_sitter import Language, Parser
    parser = Parser(Language(tsc.language()))
    out = []
    for rel in sorted(files):
        path = os.path.join(src, rel)
        try:
            data = open(path, "rb").read()
        except OSError:
            continue
        tree = parser.parse(data)
        lines = data.decode("utf-8", "replace").split("\n")
        covered = set(failing_lines.get(rel, []))
        tests_per_line = per_test.get(rel, {})

        def visit(node):
            if node.type == "function_definition":
                name, params = None, ""
                d = node.child_by_field_name("declarator")
                while d is not None and d.type != "function_declarator":
                    d = d.child_by_field_name("declarator") or next((c for c in d.children if "declarator" in c.type), None)
                if d is not None:
                    nm = d.child_by_field_name("declarator")
                    while nm is not None and nm.type != "identifier":
                        nm = nm.child_by_field_name("declarator") or next((c for c in nm.children if c.type == "identifier" or "declarator" in c.type), None)
                    if nm is not None:
                        name = data[nm.start_byte:nm.end_byte].decode("utf-8", "replace")
                    pl = d.child_by_field_name("parameters")
                    if pl is not None:
                        ptypes = []
                        for p in pl.named_children:
                            if p.type in ("parameter_declaration", "variadic_parameter"):
                                t = data[p.start_byte:p.end_byte].decode("utf-8", "replace")
                                dd = p.child_by_field_name("declarator")
                                if dd is not None:            # drop the parameter name, keep its type
                                    ident = dd
                                    while ident is not None and ident.type != "identifier":
                                        ident = ident.child_by_field_name("declarator") or next((c for c in ident.children if c.type == "identifier" or "declarator" in c.type), None)
                                    if ident is not None:
                                        t = (data[p.start_byte:ident.start_byte] + data[ident.end_byte:p.end_byte]).decode("utf-8", "replace")
                                ptypes.append(" ".join(t.split()))
                        params = ", ".join(ptypes)
                if name:
                    b, e = node.start_point[0] + 1, node.end_point[0] + 1
                    rng = set(range(b, e + 1))
                    if rng & covered:
                        # comment block immediately preceding the definition
                        cm, prev = [], node.prev_named_sibling
                        while prev is not None and prev.type == "comment":
                            cm.insert(0, data[prev.start_byte:prev.end_byte].decode("utf-8", "replace")); prev = prev.prev_named_sibling
                        ntests = len({t for ln in rng for t in tests_per_line.get(str(ln), [])})
                        out.append({"signature": f"{rel}:{name}({params})", "file": rel, "name": name, "params": params,
                                    "begin_line": b, "end_line": e, "comment": "\n".join(cm),
                                    "snippet": "\n".join(lines[b - 1:e]), "num_failing_tests": ntests})
                return
            for c in node.children:
                visit(c)
        visit(tree.root_node)
    return out


# ---------------------------------------------------------------- repository interface
class CInterface:
    def __init__(self, functions, tests):
        self.functions = functions
        self.tests = tests
        self.by_name = {}
        for f in functions:
            self.by_name.setdefault(f["name"], []).append(f)

    # -- tools
    def get_failing_tests_covered_files(self):
        grouped = {}
        for f in sorted({f["file"] for f in self.functions}):
            d, b = os.path.split(f)
            grouped.setdefault(d or ".", []).append(b)
        return grouped

    def get_failing_tests_covered_functions_for_file(self, file_name):
        file_name = file_name.strip()
        fs = [f for f in self.functions if f["file"] == file_name or f["file"].endswith("/" + file_name)]
        if fs:
            return [f"{f['name']}({f['params']})" for f in fs]
        return {"error_message": f"No function information available for the file: {file_name}. "
                                 "The available file names can be found by calling get_failing_tests_covered_files()."}

    def _match(self, signature):
        """(exact, candidates) for a free-form signature the model wrote."""
        s = signature.strip().strip("`")
        file_part, name = None, None
        m = re.match(r"^\s*([\w./-]+\.[ch])\s*[:.]\s*([A-Za-z_]\w*)", s)
        if m:
            file_part, name = m.group(1), m.group(2)
        else:
            m = re.match(r"^\s*(?:[\w\s\*]+\s+)?\*?([A-Za-z_]\w*)\s*(\(|$)", s)
            if m:
                name = m.group(1)
        if not name:
            return None, []
        cands = self.by_name.get(name, [])
        if file_part:
            narrowed = [f for f in cands if f["file"] == file_part or f["file"].endswith("/" + file_part)]
            if narrowed:
                cands = narrowed
        if len(cands) == 1:
            return cands[0], cands
        return None, cands[:5]

    def get_code_snippet(self, signature):
        f, cands = self._match(signature)
        if f:
            return self._numbered(f)
        if not cands:
            return {"error_message": f"No functions with the name {signature} were found. It may not be covered by the failing tests. Please try something else."}
        calls = sorted({f"get_code_snippet({c['signature']})" for c in cands})
        return {"error_message": f"There are multiple matches to that query. Do you mean any of the following: {calls}?"}

    def get_comments(self, signature):
        f, cands = self._match(signature)
        if f:
            return f["comment"] or "(no comment found)"
        if not cands:
            return {"error_message": f"No functions with the name {signature} were found. Please try something else."}
        calls = sorted({f"get_comments({c['signature']})" for c in cands})
        return {"error_message": f"There are multiple matches to that query. Do you mean any of the following: {calls}?"}

    @staticmethod
    def _numbered(f):
        w = len(str(f["end_line"]))
        return "\n".join(f"{n:>{w}} : {l}" for n, l in zip(range(f["begin_line"], f["end_line"] + 1), f["snippet"].split("\n")))

    def matching_signatures(self, expr):
        f, cands = self._match(expr)
        if f:
            return [f["signature"]]
        return [c["signature"] for c in cands]

    @property
    def fname2func(self):
        return {"get_failing_tests_covered_files": self.get_failing_tests_covered_files,
                "get_failing_tests_covered_functions_for_file": self.get_failing_tests_covered_functions_for_file,
                "get_code_snippet": self.get_code_snippet, "get_comments": self.get_comments}


# ---------------------------------------------------------------- LLM
class LLM:
    def __init__(self, model, base_url, protocol, temperature):
        from openai import OpenAI
        key = os.environ.get("OPENAI_API_KEY") or "none"
        self.client = OpenAI(api_key=key, base_url=base_url) if base_url else OpenAI(api_key=key)
        self.model, self.protocol, self.temperature = model, protocol, temperature
        self.calls = 0

    def chat(self, messages, tools=None, tool_choice="auto"):
        kw = {"model": self.model, "messages": messages}
        if self.temperature is not None:
            kw["temperature"] = self.temperature
        if tools and self.protocol == "tools":
            kw["tools"] = [{"type": "function", "function": t} for t in tools]
            kw["tool_choice"] = tool_choice
        for attempt in range(5):
            try:
                self.calls += 1
                r = self.client.chat.completions.create(**kw)
                return r.choices[0].message
            except Exception as e:                       # rate limit / transient server errors
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)


TEXT_PROTOCOL = ("\n\nTool use: functions are available: {tools}. To call one, reply with exactly one line of the form\n"
                 "FUNCTION_CALL {{\"name\": \"<function>\", \"arguments\": {{...}}}}\nand nothing else; the result will be "
                 "returned to you. When you no longer need to call functions, write your diagnosis instead.")


def to_dict(msg):
    d = {"role": msg.role, "content": msg.content}
    if getattr(msg, "tool_calls", None):
        d["tool_calls"] = [{"id": tc.id, "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in msg.tool_calls]
    return d


def extract_calls(msg, protocol):
    """[(id, name, args_json)] requested by the model."""
    if protocol == "tools":
        return [(tc.id, tc.function.name, tc.function.arguments) for tc in (getattr(msg, "tool_calls", None) or [])]
    calls = []
    for line in (msg.content or "").splitlines():
        m = re.match(r"\s*FUNCTION_CALL\s*(\{.*\})\s*$", line)
        if m:
            try:
                j = json.loads(m.group(1)); calls.append((f"call{len(calls)}", j["name"], json.dumps(j.get("arguments", {}))))
            except Exception:
                pass
    return calls


def run_once(llm, ri, tests, rep, args, system_msg):
    fails = tests[rep % len(tests):] + tests[:rep % len(tests)]        # test_offset rotation
    fails = fails[: args.num_tests]
    names = [t["name"] for t in fails]
    user = f"The test `{names}` failed.\n"
    user += "The test looks like:\n\n```vtc\n" + "\n\n".join(t["snippet"].rstrip() for t in fails) + "\n```\n\n"
    user += "It failed with the following error message and call stack:\n\n```\n" + "\n\n".join(t["fail_info"].rstrip() for t in fails) + "\n```\n\n"
    user += f"Start by calling the `{INITIAL}` function."
    sysmsg = system_msg + SYSTEM_SUFFIX_C
    if args.protocol == "text":
        sysmsg += TEXT_PROTOCOL.format(tools=", ".join(t["name"] + "(" + ", ".join(t["parameters"]["properties"]) + ")" for t in TOOLS))
    messages = [{"role": "system", "content": sysmsg}, {"role": "user", "content": user}]
    # upstream injects the first call without asking the model
    first = json.dumps(ri.fname2func[INITIAL]())
    if args.protocol == "tools":
        messages.append({"role": "assistant", "content": None, "tool_calls": [{"id": "call0", "type": "function", "function": {"name": INITIAL, "arguments": "{}"}}]})
        messages.append({"role": "tool", "tool_call_id": "call0", "name": INITIAL, "content": first})
    else:
        messages.append({"role": "assistant", "content": f'FUNCTION_CALL {{"name": "{INITIAL}", "arguments": {{}}}}'})
        messages.append({"role": "user", "content": f"Result of {INITIAL}: {first}"})

    t0 = time.time(); steps = 0; calls_made = []
    for i in range(args.budget):
        if time.time() - t0 > args.run_timeout:
            break
        choice = "none" if i == args.budget - 1 else "auto"
        msg = llm.chat(messages, TOOLS, choice)
        steps += 1
        calls = extract_calls(msg, args.protocol)
        if not calls or choice == "none":
            messages.append(to_dict(msg)); break
        messages.append(to_dict(msg))
        for cid, fname, fargs in calls:
            fn = ri.fname2func.get(fname)
            try:
                result = fn(**json.loads(fargs or "{}")) if fn else {"error_message": f"unknown function {fname}"}
            except Exception as e:
                result = {"error_message": f"bad arguments: {e}"}
            calls_made.append({"name": fname, "arguments": fargs})
            if args.protocol == "tools":
                messages.append({"role": "tool", "tool_call_id": cid, "name": fname, "content": json.dumps(result)})
            else:
                messages.append({"role": "user", "content": f"Result of {fname}: {json.dumps(result)}"})
    messages.append({"role": "user", "content": FINISH_C})
    final = llm.chat(messages)
    answer = (final.content or "").strip()
    messages.append({"role": "assistant", "content": answer})

    def parse(ans):
        ps = [l.strip().strip("`-* ") for l in ans.splitlines() if l.strip()]
        m = {}
        for p in ps:
            for s in ri.matching_signatures(p):
                m.setdefault(s, []).append(p)
        return ps, m
    preds, matched = parse(answer)
    # Local models sometimes answer the final question with tool-call markup or
    # prose; upstream does not retry, but one strict re-ask keeps such runs usable.
    for _ in range(args.finish_retry):
        if matched:
            break
        messages.append({"role": "user", "content": "Functions can no longer be called. Answer only with the "
                         "signature(s) of the most likely culprit function(s), one per line, in the form "
                         "`path/to/file.c:function_name(ArgType1, ...)`, without any other text."})
        final = llm.chat(messages)
        answer = (final.content or "").strip()
        messages.append({"role": "assistant", "content": answer})
        preds, matched = parse(answer)
    return {"rep": rep, "tests_shown": names, "steps": steps, "calls": calls_made, "answer": answer,
            "predictions": preds, "matched": matched, "messages": messages, "seconds": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input"); ap.add_argument("--out-dir")
    ap.add_argument("--reps", type=int, default=5); ap.add_argument("--budget", type=int, default=10)
    ap.add_argument("--num-tests", type=int, default=1)
    ap.add_argument("--model", default=os.environ.get("AUTOFL_MODEL", "gpt-4o-mini"))
    ap.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    ap.add_argument("--protocol", default="tools", choices=("tools", "text"))
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--run-timeout", type=int, default=600)
    ap.add_argument("--finish-retry", type=int, default=1, help="re-ask once if the final answer names no function")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        import openai, tree_sitter_c  # noqa: F401
        from tree_sitter import Language, Parser
        Parser(Language(tree_sitter_c.language())).parse(b"int f(int a) { return a; }")
        print(f"autofl deps OK (openai {openai.__version__}, tree-sitter-c)"); return

    inp = json.load(open(args.input))
    os.makedirs(os.path.join(args.out_dir, "runs"), exist_ok=True)
    functions = index_functions(inp["src"], list(inp["files"]),
                                {f: v["failing_lines"] for f, v in inp["files"].items()},
                                {f: v["per_line_tests"] for f, v in inp["files"].items()})
    json.dump(functions, open(os.path.join(args.out_dir, "functions.json"), "w"), indent=1)
    print(f"[autofl] indexed {len(functions)} functions covered by failing tests in {len({f['file'] for f in functions})} files", flush=True)
    ri = CInterface(functions, inp["failing_tests"])
    system_msg = open(os.path.join(HERE, "system_msg_expbug.txt")).read().strip()
    llm = LLM(args.model, args.base_url, args.protocol, args.temperature)
    print(f"[autofl] model={args.model} base_url={args.base_url or 'openai'} protocol={args.protocol} reps={args.reps} budget={args.budget}", flush=True)
    for rep in range(args.reps):
        path = os.path.join(args.out_dir, "runs", f"rep{rep}.json")
        if os.path.exists(path):
            print(f"[autofl] rep {rep}: exists, skipping", flush=True); continue
        try:
            r = run_once(llm, ri, inp["failing_tests"], rep, args, system_msg)
        except Exception as e:
            r = {"rep": rep, "error": repr(e), "matched": {}}
        json.dump(r, open(path, "w"), indent=1)
        print(f"[autofl] rep {rep}: steps={r.get('steps')} calls={len(r.get('calls', []))} "
              f"predictions={r.get('predictions')} matched={list(r.get('matched', {}))} ({r.get('seconds')}s)"
              + (f" ERROR {r['error']}" if "error" in r else ""), flush=True)


if __name__ == "__main__":
    main()
