#!/usr/bin/env python3
"""Find Thumb BL/BLX call sites and odd (Thumb) function pointers targeting the
given addresses. usage: xref.py seg0_c0000000.bin C0709158,C070D858"""
import sys,struct
rom=open(sys.argv[1],'rb').read(); B=0xC0000000
targets={int(t,16) for t in sys.argv[2].split(',')}
lim=min(len(rom),0x00B00000)   # code lives below ~C0B00000
for o in range(0,lim-4,2):
    h1,h2=struct.unpack_from('<HH',rom,o)
    if (h1&0xF800)==0xF000 and (h2&0xD000) in (0xD000,0xC000):
        S_=(h1>>10)&1; imm10=h1&0x3FF; J1=(h2>>13)&1; J2=(h2>>11)&1; imm11=h2&0x7FF
        I1=1-(J1^S_); I2=1-(J2^S_)
        off=(S_<<24)|(I1<<23)|(I2<<22)|(imm10<<12)|(imm11<<1)
        if S_: off-=1<<25
        pc=B+o+4
        tgt=pc+off if (h2&0xD000)==0xD000 else ((pc&~3)+off)
        if tgt in targets: print('%08X -> %08X %s'%(B+o,tgt,'bl' if (h2&0xD000)==0xD000 else 'blx'))
    # 32-bit literal function pointers (vtables)
for o in range(0,len(rom)-4,4):
    v=struct.unpack_from('<I',rom,o)[0]
    if (v&~1) in targets and v&1: print('%08X ptr-> %08X'%(B+o,v))
