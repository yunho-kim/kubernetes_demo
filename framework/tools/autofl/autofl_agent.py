#!/usr/bin/env python3
"""autofl_agent.py — AutoFL (Kang, An, Yoo, FSE 2024) ported to C and Go, run inside the tool venv.

    autofl_agent.py --input input.json --out-dir <dir> [--reps 5] [--budget 10] [--num-tests 1]
                    [--model M] [--base-url URL] [--protocol tools|text|cosmos] [--temperature T]
    autofl_agent.py --selftest

Port of coinse/autofl (autofl.py + lib/d4j_interface.py), Java -> C:
  class  -> source file          package -> directory
  method -> C function           signature = "<file>:<name>(<parameter types>)"
Go (input "lang": "go"): the same with Go functions and methods; a method's signature carries
its receiver type, "<file>:(*Type).Name(<parameter types>)". The failing test shown to the
model is the Go test function, or, for a subtest of a long table-driven test, its table
entry and the loop running the table (go_test_snippet).
The four tools keep upstream's names modulo the class/method words:
  get_failing_tests_covered_files()                -> {directory: [file, ...]}
  get_failing_tests_covered_functions_for_file(f)  -> ["name(params)", ...]
  get_code_snippet(signature)                      -> numbered source lines
  get_comments(signature)                          -> the comment block preceding the function
Protocol (upstream runner.sh defaults): R repetitions, each rotating which failing
test is shown first and showing 1 test; the first tool call is injected without
asking the model; 10 steps, tool calls disabled on the last; then the model is
asked for culprit signatures, one per line (multi-prediction mode).
--protocol cosmos is COSMosFL's variant of this loop for small models (run_once_cosmos):
functions described in the prompt (system_msg_expbug_with_funcs.txt), the conversation
flattened into one prompt, "Function call:" lines parsed — used by framework/tools/cosmosfl.

input.json (written by `run`):
  {"src": <checkout root>, "files": {file: {"failing_lines": [..], "per_test": {test: [..]}}},
   "failing_tests": [{"id","name","snippet","fail_info"}], "lang": "c" | "go"}
  (a Go test may come with "snippet": null and "go_test": {"files","func","subtest"})
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

# Per-language wording: the signature form the model must answer in, the code fence of the
# failing test, the examples in the tool descriptions and the system prompts.
LANG = {
    "c": {"sig": "`path/to/file.c:function_name(ArgType1, ArgType2, ...)`", "fence": "vtc",
          "file_ex": "src/h1.c", "sig_ex": "src/h1.c:h1_parse_msg_hdrs(struct h1m *, ...)", "name_ex": "h1_parse_msg_hdrs",
          "prompt": "system_msg_expbug.txt", "cosmos_prompt": "system_msg_expbug_with_funcs.txt"},
    "go": {"sig": "`path/to/file.go:FunctionName(ArgType1, ArgType2, ...)` (a method: "
                  "`path/to/file.go:(*Type).MethodName(ArgType1, ...)`)", "fence": "go",
           "file_ex": "pkg/util/parsers/parsers.go", "sig_ex": "pkg/util/parsers/parsers.go:ParseImageName(string)",
           "name_ex": "ParseImageName",
           "prompt": "system_msg_expbug_go.txt", "cosmos_prompt": "system_msg_expbug_with_funcs_go.txt"},
}


def finish_msg(lang):
    return FINISH.replace("methods", "functions").replace("`ClassName.MethodName(ArgType1, ArgType2, ...)`", LANG[lang]["sig"])


def system_suffix(lang):
    return ("\n\nAfter providing this diagnosis, you will be prompted to suggest which functions would be the "
            f"best locations to be fixed. The answers should be in the form of {LANG[lang]['sig']} without "
            "commentary (one per line), as your answer will be automatically processed before finally being "
            "presented to the user.")


def tools(lang):
    L = LANG[lang]
    return [
        {"name": "get_failing_tests_covered_files",
         "description": "This function retrieves a set of source files covered by failing tests and groups them by their directory names.",
         "parameters": {"type": "object", "properties": {}, "required": []}},
        {"name": "get_failing_tests_covered_functions_for_file",
         "description": "This function takes a file_name as input and returns a list of function names covered by failing tests for the specified file in the program under test.",
         "parameters": {"type": "object", "properties": {"file_name": {"type": "string",
                        "description": f"The path of the file in the program under test, e.g., \"{L['file_ex']}\"."}},
                        "required": ["file_name"]}},
        {"name": "get_code_snippet",
         "description": "This function takes a signature as input and returns the corresponding code snippet for the function.",
         "parameters": {"type": "object", "properties": {"signature": {"type": "string",
                        "description": f"The signature of the function to retrieve the code snippet for. e.g. \"{L['sig_ex']}\" or just \"{L['name_ex']}\""}},
                        "required": ["signature"]}},
        {"name": "get_comments",
         "description": "This function takes a signature as input and returns the comment documentation (if available) for the function.",
         "parameters": {"type": "object", "properties": {"signature": {"type": "string",
                        "description": "The signature of the function to retrieve the documentation for."}},
                        "required": ["signature"]}},
    ]


INITIAL = "get_failing_tests_covered_files"


# ---------------------------------------------------------------- function index (tree-sitter)
def index_functions(lang, src, files, failing_lines, per_test):
    return (index_go if lang == "go" else index_c)(src, files, failing_lines, per_test)


def _covering_tests(rng, tests_per_line):
    return len({t for ln in rng for t in tests_per_line.get(str(ln), [])})


def _comment_before(node, data):
    cm, prev = [], node.prev_named_sibling
    while prev is not None and prev.type == "comment":
        cm.insert(0, data[prev.start_byte:prev.end_byte].decode("utf-8", "replace")); prev = prev.prev_named_sibling
    return "\n".join(cm)


def go_param_types(plist, data):
    """Parameter types of a Go parameter_list, one per parameter ("a, b int" -> int, int)."""
    out = []
    for p in plist.named_children if plist is not None else []:
        if p.type not in ("parameter_declaration", "variadic_parameter_declaration"):
            continue
        t = p.child_by_field_name("type")
        ts = " ".join(data[t.start_byte:t.end_byte].decode("utf-8", "replace").split()) if t is not None else ""
        if p.type == "variadic_parameter_declaration":
            ts = "..." + ts
        out += [ts] * max(1, len(p.children_by_field_name("name")))
    return ", ".join(out)


def index_go(src, files, failing_lines, per_test):
    """Go functions and methods covered by failing tests; a method's signature carries its
    receiver type: "pkg/kubelet/server/server.go:(*Server).InstallDefaultHandlers()"."""
    import tree_sitter_go as tsg
    from tree_sitter import Language, Parser
    parser = Parser(Language(tsg.language()))
    out = []
    for rel in sorted(files):
        if not rel.endswith(".go"):
            continue
        try:
            data = open(os.path.join(src, rel), "rb").read()
        except OSError:
            continue
        lines = data.decode("utf-8", "replace").split("\n")
        covered = set(failing_lines.get(rel, []))
        for node in parser.parse(data).root_node.named_children:
            if node.type not in ("function_declaration", "method_declaration"):
                continue
            nm = node.child_by_field_name("name")
            name = data[nm.start_byte:nm.end_byte].decode("utf-8", "replace")
            receiver = ""
            if node.type == "method_declaration":
                r = node.child_by_field_name("receiver")
                rp = next((c for c in r.named_children if c.type == "parameter_declaration"), None) if r is not None else None
                rt = rp.child_by_field_name("type") if rp is not None else None
                receiver = " ".join(data[rt.start_byte:rt.end_byte].decode("utf-8", "replace").split()) if rt is not None else ""
            params = go_param_types(node.child_by_field_name("parameters"), data)
            b, e = node.start_point[0] + 1, node.end_point[0] + 1
            rng = set(range(b, e + 1))
            if not rng & covered:
                continue
            short = f"({receiver}).{name}({params})" if receiver else f"{name}({params})"
            out.append({"signature": f"{rel}:{short}", "file": rel, "name": name, "receiver": receiver, "params": params,
                        "short": short, "begin_line": b, "end_line": e, "comment": _comment_before(node, data),
                        "snippet": "\n".join(lines[b - 1:e]), "num_failing_tests": _covering_tests(rng, per_test.get(rel, {}))})
    return out


def go_test_snippet(files, func, subtest, max_lines=200):
    """Source of Go test `func` (in one of `files`). For a subtest of a long table-driven test,
    the matching table entry and the loop running the table instead of the whole function."""
    import tree_sitter_go as tsg
    from tree_sitter import Language, Parser
    parser = Parser(Language(tsg.language()))
    for path in files:
        try:
            data = open(path, "rb").read()
        except OSError:
            continue
        node = next((n for n in parser.parse(data).root_node.named_children if n.type == "function_declaration"
                     and data[n.child_by_field_name("name").start_byte:n.child_by_field_name("name").end_byte].decode() == func), None)
        if node is None:
            continue
        text = data[node.start_byte:node.end_byte].decode("utf-8", "replace")
        if not subtest or text.count("\n") < max_lines:
            return text
        leaf = subtest.split("/")[-1]
        entry, stack = None, [node]
        while stack and entry is None:                 # the string literal naming the case -> its table entry
            n = stack.pop()
            if n.type in ("interpreted_string_literal", "raw_string_literal"):
                lit = data[n.start_byte + 1:n.end_byte - 1].decode("utf-8", "replace")
                if lit.replace(" ", "_") == leaf:
                    p = n
                    while p is not None and p.type != "keyed_element":
                        p = p.parent
                    if p is not None and p.parent is not None and p.parent.type == "literal_value":
                        entry = p.parent
            stack.extend(reversed(n.named_children))
        body = node.child_by_field_name("body")
        stmts = body.named_children if body is not None else []
        if len(stmts) == 1 and stmts[0].type == "statement_list":       # tree-sitter-go >= 0.25
            stmts = stmts[0].named_children
        loop = next((c for c in reversed(stmts) if c.type == "for_statement"), None)
        if entry is None:
            return "\n".join(text.split("\n")[:max_lines]) + "\n\t// ... (truncated)"
        part = lambda n: data[n.start_byte:n.end_byte].decode("utf-8", "replace")
        out = [text.split("\n")[0], "\t// ...", f"\t// the test case \"{leaf}\":", "\t" + part(entry), "\t// ..."]
        if loop is not None:
            out += ["\t" + part(loop)]
        return "\n".join(out + ["}"])
    return f"(source of {func} not found)"


def index_c(src, files, failing_lines, per_test):
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
def parse_go_signature(s):
    """(file, receiver, name) of a free-form Go signature: "path/x.go:(*T).M(int)",
    "x.go:F", "T.M", "(*T).M", "pkg.F", "F(...)", and the stack-trace forms
    "k8s.io/kubernetes/pkg/x.(*T).M(...)", "pkg.(*T).M". A one-word qualifier may be a
    receiver type or a package name; the caller narrows by it only when that matches something."""
    s = re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", s.strip().strip("`")).strip().strip("`")
    file_part = recv = None
    m = re.match(r"^([\w./-]+\.go)\s*:\s*", s)
    if m:
        file_part, s = m.group(1), s[m.end():]
    s = re.sub(r"^[\w.-]+(?:/[\w.-]+)+\.(?=[A-Za-z_(])", "", s)     # import-path qualifier
    m = re.match(r"^(?:[A-Za-z_]\w*\.)?\(\s*\*?\s*([A-Za-z_]\w*)[^)]*\)\s*\.\s*", s)
    if m:
        recv, s = m.group(1), s[m.end():]
    m = re.match(r"^((?:[A-Za-z_]\w*\s*\.\s*)*)([A-Za-z_]\w*)\s*(\(|$)", s)
    if not m:
        return file_part, recv, None
    quals = [q.strip() for q in m.group(1).split(".") if q.strip()]
    return file_part, recv or (quals[-1] if quals else None), m.group(2)


class CInterface:
    """The four AutoFL tools over the function index (C, or Go with lang="go")."""

    def __init__(self, functions, tests, lang="c"):
        self.functions = functions
        self.tests = tests
        self.lang = lang
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
            return [f.get("short") or f"{f['name']}({f['params']})" for f in fs]
        return {"error_message": f"No function information available for the file: {file_name}. "
                                 "The available file names can be found by calling get_failing_tests_covered_files()."}

    def _match(self, signature):
        """(exact, candidates) for a free-form signature the model wrote."""
        if self.lang == "go":
            return self._match_go(signature)
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

    def _match_go(self, signature):
        file_part, recv, name = parse_go_signature(signature)
        cands = self.by_name.get(name, []) if name else []
        if file_part:
            narrowed = [f for f in cands if f["file"] == file_part or f["file"].endswith("/" + file_part)]
            if narrowed:
                cands = narrowed
        if recv:
            narrowed = [f for f in cands if f["receiver"].lstrip("*").split("[")[0] == recv]
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


# ---------------------------------------------------------------- COSMosFL's small-model protocol
# COSMosFL (Cho, Kang, An, Yoo, LLM4Code 2025; coinse/cosmosfl, utils/llm_utils.py OllamaEngine
# and autofl/autofl.py) runs AutoFL on small models as instruction following instead of chat
# tool calling: the conversation is flattened into one prompt ("[System] ...\n[User] ...\n
# [Assistant] Function call: f(k=v)\n[Function] {...}\n[Assistant] "), sent as a single
# generation request (Ollama /api/generate; here one user message to a chat endpoint, which
# applies the model's template the same way), and the first "Function call:" line of the reply
# is the call. The functions are described in system_msg_expbug_with_funcs.txt; the
# directory-level coverage tool is not offered there (it is injected as the first call).
COSMOS_ARG = {"get_failing_tests_covered_files": None, "get_failing_tests_covered_functions_for_file": "file_name"}


def cosmos_prompt(messages):
    def call2str(fc):
        return fc["name"] + "(" + ", ".join(f"{k}={v}" for k, v in json.loads(fc["arguments"]).items()) + ")"
    s = ""
    for m in messages:
        fc = f"Function call: {call2str(m['function_call'])}" if "function_call" in m else ""
        s += f"[{m['role'].title()}] {m['content'] if m['content'] is not None else ''}{fc}\n"
    return s + "[Assistant] "


def cosmos_parse(text):
    """Upstream OllamaEngine.parse_response: the reply as an assistant message, with a
    function_call when a "Function call:" line is present (a malformed one raises, as upstream)."""
    if text and "Function call:" in text:
        call = next(l for l in text.splitlines() if "Function call:" in l).split("Function call:")[1].strip()
        name = call.split("(")[0]
        arg = call.split("(")[1].removesuffix(")")
        if "=" in arg:
            arg = arg.split("=")[-1]
        arg = arg.strip('"').strip("'")
        key = COSMOS_ARG.get(name, "signature")
        return {"role": "assistant", "content": None,
                "function_call": {"name": name, "arguments": json.dumps({key: arg} if key else {})}}
    return {"role": "assistant", "content": text}


def run_once_cosmos(llm, ri, tests, rep, args, system_msg):
    fails = tests[rep % len(tests):] + tests[:rep % len(tests)]        # test_offset rotation
    fails = fails[: args.num_tests]
    names = [t["name"] for t in fails]
    user = f"The test `{names}` failed.\n"
    user += f"The test looks like:\n\n```{LANG[ri.lang]['fence']}\n" + "\n\n".join(t["snippet"].rstrip() for t in fails) + "\n```\n\n"
    user += "It failed with the following error message and call stack:\n\n```\n" + "\n\n".join(t["fail_info"].rstrip() for t in fails) + "\n```\n\n"
    user += f"Start by calling the `{INITIAL}` function."
    messages = [{"role": "system", "content": system_msg + system_suffix(ri.lang)}, {"role": "user", "content": user},
                {"role": "assistant", "content": None, "function_call": {"name": INITIAL, "arguments": "{}"}},
                {"role": "function", "name": INITIAL, "content": json.dumps(ri.fname2func[INITIAL]())}]

    def ask():
        return cosmos_parse(llm.chat([{"role": "user", "content": cosmos_prompt(messages)}]).content)

    t0 = time.time(); steps = 0; calls_made = []
    out = {"rep": rep, "tests_shown": names, "messages": messages}
    try:
        for _ in range(args.budget):
            if time.time() - t0 > args.run_timeout:
                break
            msg = ask()
            steps += 1
            fc = msg.get("function_call")
            if not fc:
                messages.append(msg); break
            try:                                     # upstream drops a call it cannot execute
                result = ri.fname2func[fc["name"]](**json.loads(fc["arguments"]))
            except Exception:
                continue
            messages.append(msg)
            messages.append({"role": "function", "name": fc["name"], "content": json.dumps(result)})
            calls_made.append({"name": fc["name"], "arguments": fc["arguments"]})
        messages.append({"role": "user", "content": finish_msg(ri.lang)})
        final = ask()
        messages.append(final)
        answer = final["content"].strip()            # a function call here fails the run, as upstream
    except Exception as e:
        out.update(error=repr(e), steps=steps, calls=calls_made, matched={}, seconds=round(time.time() - t0, 1))
        return out
    preds = [l.strip().strip("`-* ") for l in answer.splitlines() if l.strip()]
    matched = {}
    for p in preds:
        for s in ri.matching_signatures(p):
            matched.setdefault(s, []).append(p)
    out.update(steps=steps, calls=calls_made, answer=answer, predictions=preds, matched=matched,
               seconds=round(time.time() - t0, 1))
    return out


# ---------------------------------------------------------------- statement-level follow-up (autofl_line)
# Not part of published AutoFL: after the function answer, one more question asks which lines of
# those functions to change. It comes last, so the function answer (and the autofl / autofl_op2tb
# formulas) is unaffected; runs saved before it existed get it as a follow-up on their conversation.
def line_question(lang):
    ext = "go" if lang == "go" else "c"
    return ("For each culprit function you listed, which lines should be changed to fix the bug? Use the line "
            "numbers shown in the code snippets; if the fix adds missing code, give the line after which it should "
            "be inserted. Your answer will be processed automatically, so answer only with lines of the form "
            f"`path/to/file.{ext}:LINE` (one per line, most likely first), without commentary.")


def parse_lines(answer, ri, matched):
    """[(file, line)] named in a line answer ("file:LINE", "file:function:LINE", "file:A-B"),
    kept only inside the functions covered by failing tests. A number whose file is missing or
    wrong (e.g. "src/htx.c:469" for include/haproxy/htx.h) is read against the numbered code of
    the functions the run named, which is where the model took it from."""
    files = sorted({f["file"] for f in ri.functions})
    resolve = lambda n: n if n in files else next((f for f in files if f.endswith("/" + n)), None)
    inside = lambda f, n: any(x["file"] == f and x["begin_line"] <= n <= x["end_line"] for x in ri.functions)
    named = [x for x in ri.functions if x["signature"] in matched]
    cited = []                                   # (file or None, line)
    for m in re.finditer(r"([\w./-]+\.(?:c|h|go))\s*:\s*(?:[^:\s]*\s*:\s*)?L?(\d+)(?:\s*[-–]\s*(\d+))?", answer or ""):
        a = int(m.group(2)); b = int(m.group(3)) if m.group(3) else a
        cited += [(resolve(m.group(1)), n) for n in range(a, min(max(a, b), a + 20) + 1)]
    if not cited:
        cited = [(None, n) for n in map(int, re.findall(r"\b(\d{1,6})\b", answer or ""))]
    out = []
    for f, n in cited:
        if f and inside(f, n):
            out.append((f, n))
        else:
            out += [(x["file"], n) for x in named if x["begin_line"] <= n <= x["end_line"]][:1]
    return list(dict.fromkeys(out))


def ask_lines(llm, ri, messages, matched):
    messages.append({"role": "user", "content": line_question(ri.lang)})
    answer = (llm.chat(messages).content or "").strip()
    messages.append({"role": "assistant", "content": answer})
    return answer, parse_lines(answer, ri, matched)


def run_once(llm, ri, tests, rep, args, system_msg):
    fails = tests[rep % len(tests):] + tests[:rep % len(tests)]        # test_offset rotation
    fails = fails[: args.num_tests]
    names = [t["name"] for t in fails]
    user = f"The test `{names}` failed.\n"
    user += f"The test looks like:\n\n```{LANG[ri.lang]['fence']}\n" + "\n\n".join(t["snippet"].rstrip() for t in fails) + "\n```\n\n"
    user += "It failed with the following error message and call stack:\n\n```\n" + "\n\n".join(t["fail_info"].rstrip() for t in fails) + "\n```\n\n"
    user += f"Start by calling the `{INITIAL}` function."
    sysmsg = system_msg + system_suffix(ri.lang)
    if args.protocol == "text":
        sysmsg += TEXT_PROTOCOL.format(tools=", ".join(t["name"] + "(" + ", ".join(t["parameters"]["properties"]) + ")" for t in tools(ri.lang)))
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
        msg = llm.chat(messages, tools(ri.lang), choice)
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
    messages.append({"role": "user", "content": finish_msg(ri.lang)})
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
    line_answer, lines = ask_lines(llm, ri, messages, matched)
    return {"rep": rep, "tests_shown": names, "steps": steps, "calls": calls_made, "answer": answer,
            "predictions": preds, "matched": matched, "line_answer": line_answer, "lines": lines,
            "messages": messages, "seconds": round(time.time() - t0, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input"); ap.add_argument("--out-dir")
    ap.add_argument("--reps", type=int, default=5); ap.add_argument("--budget", type=int, default=10)
    ap.add_argument("--num-tests", type=int, default=1)
    ap.add_argument("--model", default=os.environ.get("AUTOFL_MODEL", "gpt-4o-mini"))
    ap.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL"))
    ap.add_argument("--protocol", default="tools", choices=("tools", "text", "cosmos"),
                    help="cosmos: COSMosFL's flattened-prompt protocol for small models (no final re-ask)")
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--run-timeout", type=int, default=600)
    ap.add_argument("--finish-retry", type=int, default=1, help="re-ask once if the final answer names no function")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        import openai, tree_sitter_c, tree_sitter_go  # noqa: F401
        from tree_sitter import Language, Parser
        Parser(Language(tree_sitter_c.language())).parse(b"int f(int a) { return a; }")
        Parser(Language(tree_sitter_go.language())).parse(b"package p\nfunc f(a int) int { return a }")
        print(f"autofl deps OK (openai {openai.__version__}, tree-sitter-c, tree-sitter-go)"); return

    inp = json.load(open(args.input))
    os.makedirs(os.path.join(args.out_dir, "runs"), exist_ok=True)
    lang = inp.get("lang", "c")
    functions = index_functions(lang, inp["src"], list(inp["files"]),
                                {f: v["failing_lines"] for f, v in inp["files"].items()},
                                {f: v["per_line_tests"] for f, v in inp["files"].items()})
    json.dump(functions, open(os.path.join(args.out_dir, "functions.json"), "w"), indent=1)
    print(f"[autofl] indexed {len(functions)} functions covered by failing tests in {len({f['file'] for f in functions})} files", flush=True)
    for t in inp["failing_tests"]:                # Go: the test function (or table entry) from its source
        if t.get("snippet") is None and t.get("go_test"):
            g = t["go_test"]
            t["snippet"] = go_test_snippet(g["files"], g["func"], g.get("subtest"))
            if g.get("redact", True):
                t["snippet"] = re.sub(r"CVE-\d{4}-\d+", "CVE-XXXX-XXXXX", t["snippet"])
    ri = CInterface(functions, inp["failing_tests"], lang)
    prompt = LANG[lang]["cosmos_prompt" if args.protocol == "cosmos" else "prompt"]
    system_msg = open(os.path.join(HERE, prompt)).read().strip()
    once = run_once_cosmos if args.protocol == "cosmos" else run_once
    llm = LLM(args.model, args.base_url, args.protocol, args.temperature)
    print(f"[autofl] model={args.model} base_url={args.base_url or 'openai'} protocol={args.protocol} reps={args.reps} budget={args.budget}", flush=True)
    for rep in range(args.reps):
        path = os.path.join(args.out_dir, "runs", f"rep{rep}.json")
        if os.path.exists(path):
            print(f"[autofl] rep {rep}: exists, skipping", flush=True); continue
        try:
            r = once(llm, ri, inp["failing_tests"], rep, args, system_msg)
        except Exception as e:
            r = {"rep": rep, "error": repr(e), "matched": {}}
        json.dump(r, open(path, "w"), indent=1)
        print(f"[autofl] rep {rep}: steps={r.get('steps')} calls={len(r.get('calls', []))} "
              f"predictions={r.get('predictions')} matched={list(r.get('matched', {}))} lines={r.get('lines')} "
              f"({r.get('seconds')}s)" + (f" ERROR {r['error']}" if "error" in r else ""), flush=True)
    # runs saved before the line question existed: ask it as a follow-up on the saved conversation
    if args.protocol != "cosmos":
        for rep in range(args.reps):
            path = os.path.join(args.out_dir, "runs", f"rep{rep}.json")
            r = json.load(open(path)) if os.path.exists(path) else None
            if r and "line_answer" in r:             # re-parse, so parser fixes reach saved runs
                lines = [list(p) for p in parse_lines(r["line_answer"], ri, r.get("matched", {}))]
                if lines != r.get("lines"):
                    r["lines"] = lines
                    json.dump(r, open(path, "w"), indent=1)
                    print(f"[autofl] rep {rep}: lines re-parsed {lines}", flush=True)
                continue
            if not r or "error" in r or not r.get("messages"):
                continue
            try:
                r["line_answer"], r["lines"] = ask_lines(llm, ri, r["messages"], r.get("matched", {}))
            except Exception as e:
                print(f"[autofl] rep {rep}: line follow-up failed: {e!r}", flush=True); continue
            r["line_followup"] = True
            json.dump(r, open(path, "w"), indent=1)
            print(f"[autofl] rep {rep}: lines (follow-up on the saved run) {r['lines']}", flush=True)


if __name__ == "__main__":
    main()
