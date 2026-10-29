#!/bin/sh
# Build fp2cfhd (x86 host converter) and fp2cfhd_arm (Cortex-A7 + NEON, static, encode-only)
# against an unmodified CineForm SDK checkout (github.com/gopro/cineform-sdk).
#
# usage: tools/build.sh /path/to/cineform-sdk [x86|arm|all]
#
# Needs: gcc/g++ for x86. For ARM: python3 with `pip install ziglang unicorn` (zig's clang
# cross-compiles to arm-linux-musleabihf; unicorn runs emu_count.py). No root needed.
# Outputs go to $BUILD (default: projects/cineform/build/, git-ignored).
set -eu

SDK=$(cd "${1:?usage: build.sh /path/to/cineform-sdk [x86|arm|all]}" && pwd)
WHAT=${2:-all}
HERE=$(cd "$(dirname "$0")" && pwd)
BUILD=${BUILD:-$HERE/../build}
PY=${PY:-python3}
SSE2NEON_URL=https://raw.githubusercontent.com/DLTcollab/sse2neon/v1.7.0/sse2neon.h
JOBS=${JOBS:-$(nproc)}
mkdir -p "$BUILD"
BUILD=$(cd "$BUILD" && pwd)

# The encoder SDK includes <uuid/uuid.h> only to stamp a random GUID into each sample.
mkdir -p "$BUILD/shim/uuid"
cat > "$BUILD/shim/uuid/uuid.h" <<'EOF'
#pragma once
#include <stdlib.h>
typedef unsigned char uuid_t[16];
static inline void uuid_generate(uuid_t u) { for (int i = 0; i < 16; i++) u[i] = (unsigned char)rand(); }
EOF

SRCS="Codec/*.c Codec/*.cpp WarpLib/*.c EncoderSDK/*.cpp"
INC="-ICommon -ITables -ICodec -IConvertLib -IWarpLib"
DEF="-D_ALLOCATOR=1 -DWARPSTUFF=1"

compile_all() { # $1 objdir, $2 c compiler, $3 c++ compiler, $4 flags
	mkdir -p "$1"
	(cd "$SDK" && ls $SRCS $5) | while read -r f; do echo "$f"; done |
		xargs -P "$JOBS" -I{} sh -c '
			src="{}"; o="'"$1"'/$(echo "$src" | tr / _).o"
			case "$src" in /*) path="$src";; *) path="'"$SDK"'/$src";; esac
			case "$src" in *.c) cc="'"$2"'";; *) cc="'"$3"'";; esac
			cd "'"$SDK"'" && $cc '"$4"' -c "$path" -o "$o" 2>>"'"$1"'/errors.log" || { echo "FAILED: $src"; exit 255; }'
}

if [ "$WHAT" = x86 ] || [ "$WHAT" = all ]; then
	echo "== x86"
	compile_all "$BUILD/obj-x86" gcc g++ "-O2 -fPIC -fopenmp -w -I$BUILD/shim $INC $DEF -DHAVE_OPENMP=1" "DecoderSDK/*.cpp ConvertLib/*.cpp"
	ar rcs "$BUILD/libCFHDCodec-x86.a" "$BUILD"/obj-x86/*.o
	g++ -O2 -fopenmp -w -I"$SDK/Common" "$HERE/fp2cfhd.cpp" "$BUILD/libCFHDCodec-x86.a" -lpthread -lm -o "$BUILD/fp2cfhd"
	echo "built $BUILD/fp2cfhd"
fi

if [ "$WHAT" = arm ] || [ "$WHAT" = all ]; then
	echo "== arm (Cortex-A7, SSE2 -> NEON via sse2neon)"
	ZIG="$PY -m ziglang"
	export ZIG_GLOBAL_CACHE_DIR="$BUILD/zigcache" ZIG_LOCAL_CACHE_DIR="$BUILD/zigcache"
	mkdir -p "$BUILD/armshim"
	[ -f "$BUILD/sse2neon.h" ] || curl -sSfL -o "$BUILD/sse2neon.h" "$SSE2NEON_URL"
	# Route every x86 intrinsics header (including the SDK's direct <mm_malloc.h>) to sse2neon.
	for h in mmintrin.h xmmintrin.h emmintrin.h pmmintrin.h tmmintrin.h smmintrin.h nmmintrin.h \
	         immintrin.h intrin.h x86intrin.h mm_malloc.h; do
		printf '#pragma once\n#include "%s"\n' "$BUILD/sse2neon.h" > "$BUILD/armshim/$h"
	done
	# One line assigns NULL to an unsigned int, which clang rejects in C++; compile a fixed copy.
	sed 's/mdc_size\[decoder->metadatachunks\] = NULL;/mdc_size[decoder->metadatachunks] = 0;/' \
		"$SDK/Codec/DemoasicFrames.cpp" > "$BUILD/DemoasicFrames.cpp"
	SRCS=$(cd "$SDK" && ls $SRCS | grep -v DemoasicFrames.cpp | tr '\n' ' ')
	FLAGS="-target arm-linux-musleabihf -mcpu=cortex_a7 -O2 -w -I$BUILD/armshim -I$BUILD/shim $INC $DEF -D_ASMOPT=0"
	compile_all "$BUILD/obj-arm" "$ZIG cc" "$ZIG c++" "$FLAGS" "$BUILD/DemoasicFrames.cpp"
	$ZIG c++ -target arm-linux-musleabihf -mcpu=cortex_a7 -O2 -w -static -DENCODE_ONLY -I"$SDK/Common" \
		"$HERE/fp2cfhd.cpp" "$BUILD"/obj-arm/*.o -o "$BUILD/fp2cfhd_arm"
	echo "built $BUILD/fp2cfhd_arm"
fi
