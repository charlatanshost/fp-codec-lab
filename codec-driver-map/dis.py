#!/usr/bin/env python3
"""Thumb disassembly from a firmware address until the function returns, with
literal-pool values resolved. usage: dis.py seg0_c0000000.bin C070B330 [max_insns]"""
import sys,struct
from capstone import Cs,CS_ARCH_ARM,CS_MODE_THUMB
rom=open(sys.argv[1],'rb').read(); B=0xC0000000
a=int(sys.argv[2],16); n=int(sys.argv[3]) if len(sys.argv)>3 else 200
md=Cs(CS_ARCH_ARM,CS_MODE_THUMB)
cnt=0
while cnt<n:
    ins=list(md.disasm(rom[a-B:a-B+4],a,1))
    if not ins: print('%08X  .short %04x'%(a,struct.unpack_from('<H',rom,a-B)[0])); a+=2; cnt+=1; continue
    i=ins[0]; extra=''
    if i.mnemonic.startswith('ldr') and '[pc' in i.op_str:
        try:
            d=int(i.op_str.split('#')[-1].rstrip(']'),16) if '#' in i.op_str else 0
            pa=((a+4)&~3)+d; extra='   ; =0x%08X'%struct.unpack_from('<I',rom,pa-B)[0]
        except Exception: pass
    print('%08X  %-8s %s%s'%(a,i.mnemonic,i.op_str,extra)); a+=i.size; cnt+=1
    if i.mnemonic.startswith('pop') and 'pc' in i.op_str and cnt>4: break
    if i.mnemonic=='bx' and i.op_str=='lr' and cnt>4: break
