#!/usr/bin/env python3
"""Per-function map of 0x300Cxxxx register accesses in a Thumb code range.

Linear Thumb sweep; a function starts at a `push {..., lr}`. Tracks registers
that hold a value built by movw/movt (or ldr-literal) in 0x300C0000..0x300C0FFF,
plus simple `add rd, rn, #imm` derivations, and logs ldr/str through them.
Heuristic: no control-flow analysis, so treat results as leads to check."""
import struct
import sys
from collections import defaultdict
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG

ROM = open(sys.argv[1], 'rb').read()
BASE = 0xC0000000
lo, hi = int(sys.argv[2], 16), int(sys.argv[3], 16)
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
md.detail = True
MMIO_LO, MMIO_HI = 0x300C0000, 0x300C1000

def word(a):
    return struct.unpack_from('<I', ROM, a - BASE)[0]

funcs = defaultdict(lambda: {'r': set(), 'w': set()})
cur = None
vals = {}   # reg id -> known value
addr = lo
while addr < hi:
    insns = list(md.disasm(ROM[addr - BASE:addr - BASE + 4], addr, 1))
    if not insns:
        addr += 2
        continue
    i = insns[0]
    m = i.mnemonic
    ops = i.operands
    if m.startswith('push') and 'lr' in i.op_str:
        cur, vals = i.address, {}
    if cur is None:
        addr += i.size
        continue
    try:
        if m.startswith('movw') and ops[1].type == ARM_OP_IMM:
            vals[ops[0].reg] = ops[1].imm & 0xFFFF
        elif m.startswith('movt') and ops[1].type == ARM_OP_IMM and ops[0].reg in vals:
            vals[ops[0].reg] = (vals[ops[0].reg] & 0xFFFF) | (ops[1].imm << 16)
        elif m.startswith('ldr') and len(ops) == 2 and ops[1].type == ARM_OP_MEM and ops[1].mem.base == 11 + 4:  # pc
            pa = ((i.address + 4) & ~3) + ops[1].mem.disp
            if BASE <= pa < BASE + len(ROM) - 4:
                vals[ops[0].reg] = word(pa)
        elif m.startswith(('add', 'adds')) and len(ops) == 3 and ops[1].type == ARM_OP_REG and ops[2].type == ARM_OP_IMM \
                and ops[1].reg in vals:
            vals[ops[0].reg] = vals[ops[1].reg] + ops[2].imm
        elif m.startswith(('ldr', 'str')) and len(ops) >= 2 and ops[-1].type == ARM_OP_MEM and ops[-1].mem.base in vals \
                and ops[-1].mem.index == 0:
            ea = vals[ops[-1].mem.base] + ops[-1].mem.disp
            if MMIO_LO <= ea < MMIO_HI:
                funcs[cur]['w' if m.startswith('str') else 'r'].add(ea & 0xFFF)
        elif ops and ops[0].type == ARM_OP_REG and not m.startswith(('str', 'cmp', 'tst', 'b', 'push', 'pop')):
            vals.pop(ops[0].reg, None)   # destination overwritten by something we don't model
    except Exception:
        pass
    addr += i.size

for f in sorted(funcs):
    d = funcs[f]
    fmt = lambda s: ' '.join('%03X' % x for x in sorted(s))
    print('%08X  W[%s]  R[%s]' % (f, fmt(d['w']), fmt(d['r'])))
