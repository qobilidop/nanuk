"""The IR pane text renderer: deterministic text, exact per-state line
ranges, and per-op asm emission counts that mirror the v0 lowering
(ext/shl/adv/stmd/sethdr = 1, re-anchor mark = 0, dispatch = 2 per case
+ 1 for the default, goto/halt = 1)."""

from nanuk.ir.lower import to_asm
from nanuk.testkit.load import load_example
build_ir = load_example("l2l3l4/parse.py").build_ir

from bridge import render_ir


def test_renders_all_states_with_exact_ranges():
    program = build_ir()
    rendered = render_ir(program)
    lines = rendered.text.splitlines()
    assert [s.name for s in rendered.states] == [st.name for st in program.states]
    for state in rendered.states:
        start, end = state.ir_range
        assert lines[start - 1] == f"{state.name}:"          # 1-based
        assert all(lines[i].startswith("    ") for i in range(start, end))


def test_op_labels_and_lines():
    rendered = render_ir(build_ir())
    start = rendered.states[0]
    labels = [op.label for op in start.ops]
    # start: mark eth, extract eth.dst, smd, extract ethertype, advance,
    # dispatch (the dispatch is one op entry; its cases are lines within it)
    assert labels[0] == "mark eth"
    assert "eth.dst" in labels[1]
    lines = rendered.text.splitlines()
    for op in start.ops:
        assert lines[op.ir_line - 1].strip() != ""


def test_asm_counts_sum_to_real_instruction_count():
    # The ordered-walk provenance only works if per-op counts exactly
    # partition each state's asm block. Cross-check against to_asm.
    program = build_ir()
    rendered = render_ir(program)
    asm_lines = to_asm(program).splitlines()
    for st, rst in zip(program.states, rendered.states):
        label_idx = asm_lines.index(f"{st.name}:")
        n = 0
        for line in asm_lines[label_idx + 1:]:
            if not line.startswith("    "):
                break
            n += 1
        assert sum(op.asm_count for op in rst.ops) == n, st.name


def test_reanchor_mark_costs_zero_asm_lines():
    program = build_ir()
    rendered = render_ir(program)
    body = next(s for s in rendered.states if s.name == "ipv4_body")
    reanchor = body.ops[0]
    assert reanchor.label == "mark ipv4 (re-anchor)"
    assert reanchor.asm_count == 0
