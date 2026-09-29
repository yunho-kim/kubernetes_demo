#!/usr/bin/env python3
"""llmao_infer.py — score C source lines with LLMAO (runs inside the tool venv).

    llmao_infer.py --src <tree> --files f1 [f2 ...] --out scores.json [--model 350M|6B|16B]
                   [--device auto|cuda|cpu] [--chunk 124] [--offset 0]
    llmao_infer.py --selftest        # load the 350M adapter head only

Faithful to upstream (squaresLab/LLMAO, llmao_d4j_window.py / demo.py):
  * lines are filtered the way LLMAO does before tokenisation (blank lines and
    lines starting with '/', '*', '#' are dropped) — but here every filtered
    line keeps its original 1-based line number, instead of being recovered by
    string matching;
  * the file is processed in chunks of --chunk (124) filtered lines;
  * per chunk: CodeGen final hidden states at newline tokens -> LLMAO adapter
    (VoltronTransformerPretrained, Devign checkpoint) -> sigmoid probability per
    line.  Probability index i maps to filtered line i (upstream demo.py);
    upstream's llmao_d4j_window.py uses i-1 instead — --offset 1 reproduces it.
  * a line's score is its probability (max over chunks — each line is in one).

Output: {"model": ..., "device": ..., "files": {file: {"<line>": prob, ...}}}
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
UP = os.path.join(HERE, "upstream")
sys.path.insert(0, UP)
os.environ.setdefault("HF_HOME", os.path.join(HERE, ".venv", "hf-cache"))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import torch  # noqa: E402

DIMS = {"350M": 1024, "6B": 4096, "16B": 6144}
NL_IDS = (198, 628)          # CodeGen tokenizer: "\n" and "\n\n"
MAX_TOK = 2048


def load_head(model_size):
    from transformer import VoltronTransformerPretrained
    target_dim, num_head = (1024, 16) if model_size == "16B" else (512, 8)
    head = VoltronTransformerPretrained(num_layer=2, dim_model=DIMS[model_size], num_head=num_head, target_dim=target_dim)
    sd = torch.load(os.path.join(UP, "model_checkpoints", f"devign_{model_size}"), map_location="cpu")
    head.load_state_dict(sd, strict=False)
    head.eval()
    return head


def load_backbone(model_size, device):
    from transformers import AutoModelForCausalLM, AutoTokenizer
    name = f"Salesforce/codegen-{model_size}-multi"
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    model = AutoModelForCausalLM.from_pretrained(name, output_hidden_states=True, torch_dtype=dtype)
    model.to(device).eval()
    tok = AutoTokenizer.from_pretrained("Salesforce/codegen-350M-mono")
    return model, tok


def filtered_lines(path):
    """[(lineno, text)] after LLMAO's filter, de-duplicated like demo.py does NOT (window method keeps dups)."""
    out = []
    with open(path, errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            s = line.strip()
            if s and not s.startswith("/") and not s.startswith("*") and not s.startswith("#"):
                out.append((i, line if line.endswith("\n") else line + "\n"))
    return out


def drop_double_newlines(text):          # upstream TokenizeMask.drop_double_newlines
    parts, out, even = text.split("\n"), [], False
    for line in parts:
        if not line and not even:
            even = True
            continue
        even = False
        out.append(line)
    return "\n".join(out)


@torch.no_grad()
def score_chunk(backbone, tok, head, device, chunk):
    text = drop_double_newlines("".join(t for _, t in chunk))
    ids = tok(text, return_tensors="pt", truncation=True, max_length=MAX_TOK)["input_ids"].to(device)
    hs = backbone(input_ids=ids)[2][-1][0]                      # last layer hidden states [T, dim]
    nl = torch.where((ids[0] == NL_IDS[0]) | (ids[0] == NL_IDS[1]))[0]
    feats = hs[nl].float().cpu()                                # [n_lines, dim]
    n = feats.shape[0]
    emb = torch.zeros(MAX_TOK, feats.shape[1]); emb[:n] = feats
    mask = torch.zeros(MAX_TOK); mask[:n] = 1
    logits = head(emb[None], mask[None])[0]
    probs = torch.sigmoid(logits)[:n].tolist()
    return probs, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src")
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--out")
    ap.add_argument("--model", default="350M", choices=sorted(DIMS))
    ap.add_argument("--device", default="auto")
    ap.add_argument("--chunk", type=int, default=124)
    ap.add_argument("--offset", type=int, default=0, help="prob index i -> filtered line i-offset (upstream demo.py: 0; llmao_d4j_window.py: 1)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        head = load_head("350M")
        print(f"llmao adapter head OK ({sum(p.numel() for p in head.parameters())} params); "
              f"torch {torch.__version__}, cuda={torch.cuda.is_available()}")
        return

    device = args.device
    if device == "auto":
        device = "cpu"
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            need = {"350M": 3, "6B": 15, "16B": 38}[args.model] * 2**30
            if free >= need:
                device = "cuda"
            else:
                print(f"[llmao] cuda has {free/2**30:.1f} GiB free of {total/2**30:.1f}; codegen-{args.model} "
                      f"needs ~{need/2**30:.0f} GiB -> using cpu", flush=True)
    t0 = time.time()
    head = load_head(args.model)
    backbone, tok = load_backbone(args.model, device)
    print(f"[llmao] loaded codegen-{args.model}-multi + devign_{args.model} on {device} ({time.time()-t0:.0f}s)", flush=True)

    out = {"model": args.model, "device": device, "chunk": args.chunk, "offset": args.offset, "files": {}}
    for k, rel in enumerate(args.files, 1):
        path = os.path.join(args.src, rel)
        if not os.path.exists(path):
            print(f"[llmao] missing {rel}", file=sys.stderr); continue
        lines = filtered_lines(path)
        scores = {}
        t1 = time.time()
        for start in range(0, len(lines), args.chunk):
            chunk = lines[start:start + args.chunk]
            try:
                probs, n = score_chunk(backbone, tok, head, device, chunk)
            except RuntimeError as e:            # OOM etc.
                print(f"[llmao] {rel}@{start}: {e}", file=sys.stderr); continue
            for i, p in enumerate(probs):
                j = i - args.offset
                if 0 <= j < len(chunk):
                    ln = chunk[j][0]
                    scores[str(ln)] = max(scores.get(str(ln), 0.0), float(p))
        out["files"][rel] = scores
        print(f"[llmao] {k}/{len(args.files)} {rel}: {len(lines)} lines, {len(scores)} scored ({time.time()-t1:.1f}s)", flush=True)
    json.dump(out, open(args.out, "w"))


if __name__ == "__main__":
    main()
