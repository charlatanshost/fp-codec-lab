#!/usr/bin/env python3
"""Run a static ARM (musl, armhf) Linux binary under unicorn and count the
instructions executed between two EMU_MARK syscalls.

This counts instructions, not Cortex-A7 cycles. Convert with an explicit IPC
assumption, and treat the camera as the only source of real timing.

usage: emu_count.py binary [args...]     (paths in args are host paths)
"""
import os
import struct
import sys
import time

from unicorn import (Uc, UcError, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_BLOCK,
                     UC_HOOK_INTR, UC_PROT_ALL)
from unicorn.arm_const import *

EMU_MARK = 0x7F00
PAGE = 0x1000
STACK_TOP = 0x7FF00000
STACK_SIZE = 0x200000
MMAP_BASE = 0x40000000
ENOENT, EBADF, ENOTTY, ENOSYS = 2, 9, 25, 38


def align(x, a=PAGE):
    return (x + a - 1) & ~(a - 1)


class Emu:
    def __init__(self, path, argv):
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        try:
            from unicorn import UC_CPU_ARM_CORTEX_A7  # noqa
            self.uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A7)
        except Exception:
            pass
        self.mapped = []
        self.fds = {0: None, 1: sys.stdout.buffer, 2: sys.stderr.buffer}
        self.next_fd = 3
        self.mmap_top = MMAP_BASE
        self.counting = False
        self.insns = 0
        self.marked = []
        self.tcache = {}
        self.exit_code = None
        entry = self.load_elf(path)
        self.setup_stack(argv)
        self.uc.reg_write(UC_ARM_REG_C1_C0_2, self.uc.reg_read(UC_ARM_REG_C1_C0_2) | (0xF << 20))
        self.uc.reg_write(UC_ARM_REG_FPEXC, 0x40000000)
        self.uc.hook_add(UC_HOOK_INTR, self.on_intr)
        self.uc.hook_add(UC_HOOK_BLOCK, self.on_block)
        self.entry = entry

    def map(self, addr, size):
        start, end = addr & ~(PAGE - 1), align(addr + size)
        for s, e in self.mapped:
            if start < e and end > s:
                # map only the uncovered parts
                if start < s:
                    self.map(start, s - start)
                if end > e:
                    self.map(e, end - e)
                return
        self.uc.mem_map(start, end - start, UC_PROT_ALL)
        self.mapped.append((start, end))

    def load_elf(self, path):
        data = open(path, 'rb').read()
        assert data[:4] == b'\x7fELF' and data[4] == 1, 'need 32-bit ELF'
        e_entry, e_phoff = struct.unpack_from('<II', data, 24)
        e_phentsize, e_phnum = struct.unpack_from('<HH', data, 42)
        top = 0
        for i in range(e_phnum):
            p_type, p_off, p_vaddr, _, p_filesz, p_memsz = struct.unpack_from('<6I', data, e_phoff + i * e_phentsize)
            if p_type == 1:  # PT_LOAD
                if p_off == 0:
                    self.phdr_addr = p_vaddr + e_phoff
                self.map(p_vaddr, p_memsz)
                self.uc.mem_write(p_vaddr, data[p_off:p_off + p_filesz])
                top = max(top, p_vaddr + p_memsz)
        self.phdr = (e_phoff, e_phentsize, e_phnum)
        self.brk_base = self.brk = align(top)
        self.map(self.brk, PAGE)
        self.brk_mapped = self.brk + PAGE
        return e_entry

    def setup_stack(self, argv):
        self.map(STACK_TOP - STACK_SIZE, STACK_SIZE)
        sp = STACK_TOP - 0x1000
        strs = []
        for a in argv:
            b = a.encode() + b'\0'
            sp -= len(b)
            self.uc.mem_write(sp, b)
            strs.append(sp)
        sp -= 16
        rnd = sp
        self.uc.mem_write(rnd, os.urandom(16))
        sp &= ~15
        hwcap = (1 << 1) | (1 << 2) | (1 << 3) | (1 << 6) | (1 << 12) | (1 << 13) | (1 << 15) | (1 << 17)
        _, phent, phnum = self.phdr
        auxv = [(3, self.phdr_addr), (4, phent), (5, phnum), (6, PAGE), (16, hwcap), (25, rnd), (0, 0)]
        words = [len(argv)] + strs + [0] + [0]  # argc, argv, NULL, envp NULL
        for k, v in auxv:
            words += [k, v]
        sp -= 4 * len(words)
        sp &= ~15
        self.uc.mem_write(sp, struct.pack('<%dI' % len(words), *words))
        self.uc.reg_write(UC_ARM_REG_SP, sp)

    def on_block(self, uc, addr, size, _):
        if not self.counting:
            return
        n = self.tcache.get((addr, size))
        if n is None:
            if uc.reg_read(UC_ARM_REG_CPSR) & 0x20:  # Thumb: count 16/32-bit encodings
                code, n, i = uc.mem_read(addr, size), 0, 0
                while i < size:
                    hw = code[i] | (code[i + 1] << 8)
                    i += 4 if (hw >> 11) in (0x1D, 0x1E, 0x1F) else 2
                    n += 1
            else:
                n = size // 4
            self.tcache[(addr, size)] = n
        self.insns += n

    def ret(self, v):
        self.uc.reg_write(UC_ARM_REG_R0, v & 0xFFFFFFFF)

    def cstr(self, addr):
        out = b''
        while True:
            chunk = bytes(self.uc.mem_read(addr, 64))
            z = chunk.find(b'\0')
            if z >= 0:
                return (out + chunk[:z]).decode()
            out += chunk
            addr += 64

    def on_intr(self, uc, intno, _):
        if intno != 2:
            raise RuntimeError('unexpected interrupt %d at %#x' % (intno, uc.reg_read(UC_ARM_REG_PC)))
        r = [uc.reg_read(x) for x in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                       UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5)]
        nr = uc.reg_read(UC_ARM_REG_R7)
        if nr == EMU_MARK:
            if r[0]:
                self.counting, self.insns, self.t0 = True, 0, time.time()
            else:
                self.counting = False
                self.marked.append(self.insns)
            return self.ret(0)
        if nr in (1, 248):  # exit, exit_group
            self.exit_code = r[0]
            return uc.emu_stop()
        if nr == 45:  # brk
            want = r[0]
            if want > self.brk:
                if want > self.brk_mapped:
                    self.map(self.brk_mapped, want - self.brk_mapped)
                    self.brk_mapped = align(want)
                self.brk = want
            return self.ret(self.brk)
        if nr == 192:  # mmap2
            size = align(r[1])
            if r[4] != 0xFFFFFFFF and r[4] in self.fds:
                return self.ret(-ENOSYS)
            addr = self.mmap_top
            self.mmap_top += size + PAGE
            self.map(addr, size)
            return self.ret(addr)
        if nr in (91, 220, 125):  # munmap, madvise, mprotect: keep memory, it's an emulator
            return self.ret(0)
        if nr == 163:  # mremap: allocate fresh and copy
            old, osz, nsz = r[0], r[1], align(r[2])
            addr = self.mmap_top
            self.mmap_top += nsz + PAGE
            self.map(addr, nsz)
            self.uc.mem_write(addr, bytes(self.uc.mem_read(old, min(osz, nsz))))
            return self.ret(addr)
        if nr in (5, 322):  # open, openat
            path = self.cstr(r[1] if nr == 322 else r[0])
            flags = r[2] if nr == 322 else r[1]
            try:
                f = open(path, 'r+b' if flags & 3 == 2 else ('wb' if flags & 3 == 1 else 'rb'))
            except OSError:
                return self.ret(-ENOENT)
            fd, self.next_fd = self.next_fd, self.next_fd + 1
            self.fds[fd] = f
            return self.ret(fd)
        if nr == 3:  # read
            f = self.fds.get(r[0])
            if f is None:
                return self.ret(-EBADF)
            b = f.read(r[2])
            self.uc.mem_write(r[1], b)
            return self.ret(len(b))
        if nr == 145:  # readv
            f, total = self.fds.get(r[0]), 0
            if f is None:
                return self.ret(-EBADF)
            for i in range(r[2]):
                base, ln = struct.unpack('<II', self.uc.mem_read(r[1] + 8 * i, 8))
                b = f.read(ln)
                self.uc.mem_write(base, b)
                total += len(b)
                if len(b) < ln:
                    break
            return self.ret(total)
        if nr == 4:  # write
            f = self.fds.get(r[0])
            f.write(bytes(self.uc.mem_read(r[1], r[2])))
            f.flush()
            return self.ret(r[2])
        if nr == 146:  # writev
            f, total = self.fds.get(r[0]), 0
            for i in range(r[2]):
                base, ln = struct.unpack('<II', self.uc.mem_read(r[1] + 8 * i, 8))
                f.write(bytes(self.uc.mem_read(base, ln)))
                total += ln
            f.flush()
            return self.ret(total)
        if nr == 140:  # _llseek(fd, hi, lo, result*, whence)
            f = self.fds.get(r[0])
            pos = f.seek((r[1] << 32) | r[2], r[4])
            self.uc.mem_write(r[3], struct.pack('<Q', pos))
            return self.ret(0)
        if nr == 19:  # lseek
            return self.ret(self.fds[r[0]].seek(r[1], r[2]))
        if nr == 6:  # close
            f = self.fds.pop(r[0], None)
            if f and r[0] > 2:
                f.close()
            return self.ret(0)
        if nr == 54:  # ioctl: nothing is a tty
            return self.ret(-ENOTTY)
        if nr in (263, 403):  # clock_gettime(64): fake, timing comes from the count
            t = time.time()
            if nr == 403:
                self.uc.mem_write(r[1], struct.pack('<qq', int(t), int((t % 1) * 1e9)))
            else:
                self.uc.mem_write(r[1], struct.pack('<ii', int(t), int((t % 1) * 1e9)))
            return self.ret(0)
        if nr == 0xF0005:  # ARM set_tls
            uc.reg_write(UC_ARM_REG_C13_C0_3, r[0])
            return self.ret(0)
        if nr in (256, 224, 20):  # set_tid_address, gettid, getpid
            return self.ret(1000)
        if nr in (174, 175, 186):  # rt_sigaction, rt_sigprocmask, sigaltstack
            return self.ret(0)
        if nr == 242:  # sched_getaffinity: one CPU, like the fp
            self.uc.mem_write(r[2], struct.pack('<I', 1))
            return self.ret(4)
        sys.stderr.write('[emu] unhandled syscall %d (%#x) -> ENOSYS\n' % (nr, nr))
        return self.ret(-ENOSYS)

    def run(self):
        t = time.time()
        try:
            self.uc.emu_start(self.entry, 0xFFFFFFFF)
        except UcError as e:
            pc = self.uc.reg_read(UC_ARM_REG_PC)
            raise SystemExit('[emu] %s at pc=%#x' % (e, pc))
        return time.time() - t


if __name__ == '__main__':
    emu = Emu(sys.argv[1], [os.path.basename(sys.argv[1])] + sys.argv[2:])
    wall = emu.run()
    for i, n in enumerate(emu.marked):
        print('[emu] marked region %d: %d instructions' % (i, n))
    print('[emu] exit %s, host wall %.1f s' % (emu.exit_code, wall))
