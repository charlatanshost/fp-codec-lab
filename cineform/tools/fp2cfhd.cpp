// fp2cfhd — post-converter prototype: SIGMA fp CinemaDNG frame -> CineForm RAW (Bayer) sample.
// Purpose: pin down the encoder mapping (packing, black/white, Bayer phase, curve, quality)
// and measure ratio / error / CPU cost before any thought of porting to the camera.
//
// usage: fp2cfhd [-q 1..6] [-n] [-o out.cfhd] [-r out_roundtrip.pgm] frame.DNG
//   -q  CineForm quality (1=LOW .. 4=FILMSCAN1 .. 6=FILMSCAN3), default 4
//   -n  no black subtraction (feed raw<<4 straight in)
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>
#include <chrono>
#include <vector>
#include "CFHDTypes.h"
#include "CFHDEncoder.h"
#include "CFHDDecoder.h"
#include "CFHDMetadataTags.h"
#ifdef ENCODE_ONLY
#include <unistd.h>
#define EMU_MARK 0x7F00 // unused syscall number, intercepted by emu_count.py
#endif

struct Dng {
	int w = 0, h = 0, bps = 0, comp = 0, cfa = -1;
	uint32_t strip = 0, stripBytes = 0;
	double black = 0; uint32_t white = 0;
	std::vector<uint8_t> file;
};

static uint32_t rd(const uint8_t *p, int sz, bool le) {
	uint32_t v = 0;
	for (int i = 0; i < sz; i++) v = (v << 8) | p[le ? sz - 1 - i : i];
	return v;
}

static bool parseDng(const char *path, Dng &d) {
	FILE *f = fopen(path, "rb");
	if (!f) return false;
	fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
	d.file.resize(n);
	if (fread(d.file.data(), 1, n, f) != (size_t)n) { fclose(f); return false; }
	fclose(f);
	const uint8_t *b = d.file.data();
	bool le = b[0] == 'I';
	uint32_t off = rd(b + 4, 4, le);
	int cnt = rd(b + off, 2, le);
	static const int tsz[] = {0, 1, 1, 2, 4, 8, 1, 1, 2, 4, 8, 4, 8};
	for (int i = 0; i < cnt; i++) {
		const uint8_t *e = b + off + 2 + i * 12;
		int tag = rd(e, 2, le), type = rd(e + 2, 2, le);
		uint32_t c = rd(e + 4, 4, le);
		const uint8_t *v = (tsz[type] * c <= 4) ? e + 8 : b + rd(e + 8, 4, le);
		uint32_t v0 = (type == 3) ? rd(v, 2, le) : rd(v, 4, le);
		switch (tag) {
		case 256: d.w = v0; break;
		case 257: d.h = v0; break;
		case 258: d.bps = v0; break;
		case 259: d.comp = v0; break;
		case 273: d.strip = v0; break;
		case 279: d.stripBytes = v0; break;
		case 33422: // CFAPattern, 0=R 1=G 2=B
			if (v[0] == 0 && v[1] == 1) d.cfa = CFHD_BAYER_FORMAT_RED_GRN;
			else if (v[0] == 1 && v[1] == 0) d.cfa = CFHD_BAYER_FORMAT_GRN_RED;
			else if (v[0] == 1 && v[1] == 2) d.cfa = CFHD_BAYER_FORMAT_GRN_BLU;
			else if (v[0] == 2) d.cfa = CFHD_BAYER_FORMAT_BLU_GRN;
			break;
		case 50714: // BlackLevel: average all entries
			if (type == 5) { double s = 0; for (uint32_t k = 0; k < c; k++) s += (double)rd(v + 8 * k, 4, le) / rd(v + 8 * k + 4, 4, le); d.black = s / c; }
			else d.black = v0;
			break;
		case 50717: d.white = v0; break;
		}
	}
	return d.w && d.h && d.strip;
}

// TIFF packs sub-byte samples MSB-first
static void unpack12(const uint8_t *src, uint16_t *dst, size_t count) {
	for (size_t i = 0; i < count; i += 2, src += 3) {
		dst[i] = (src[0] << 4) | (src[1] >> 4);
		dst[i + 1] = ((src[1] & 0xF) << 8) | src[2];
	}
}

int main(int argc, char **argv) {
	int qual = 4; bool noBlack = false; const char *out = NULL, *rt = NULL, *in = NULL, *ext = NULL;
	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "-q") && i + 1 < argc) qual = atoi(argv[++i]);
		else if (!strcmp(argv[i], "-n")) noBlack = true;
		else if (!strcmp(argv[i], "-o") && i + 1 < argc) out = argv[++i];
		else if (!strcmp(argv[i], "-r") && i + 1 < argc) rt = argv[++i];
		else if (!strcmp(argv[i], "-d") && i + 1 < argc) ext = argv[++i];
		else in = argv[i];
	}
	Dng d;
	if (!in || !parseDng(in, d)) { fprintf(stderr, "usage: fp2cfhd [-q 1..6] [-n] [-o out.cfhd] [-r rt.pgm] frame.DNG\n"); return 1; }
	if (d.comp != 1 || d.bps != 12) { fprintf(stderr, "only uncompressed 12-bit cDNG supported (comp=%d bps=%d)\n", d.comp, d.bps); return 1; }
	if (d.cfa < 0) d.cfa = CFHD_BAYER_FORMAT_RED_GRN;
	size_t npix = (size_t)d.w * d.h;
	double white = d.white ? d.white : 4095, black = noBlack ? 0 : d.black;

	std::vector<uint16_t> raw(npix), byr4(npix), dec(npix);
	unpack12(d.file.data() + d.strip, raw.data(), npix);

	// Mapping: (raw - black) / (white - black) -> full-scale 16-bit linear. The encoder then
	// applies its default log90 curve to 12 bits before the wavelet.
	double scale = 65535.0 / (white - black);
	for (size_t i = 0; i < npix; i++) {
		double v = (raw[i] - black) * scale;
		byr4[i] = v < 0 ? 0 : v > 65535 ? 65535 : (uint16_t)lrint(v);
	}

	CFHD_EncoderRef enc = NULL;
	CFHD_MetadataRef meta = NULL;
	CFHD_Error err = CFHD_OpenEncoder(&enc, NULL);
	if (!err) err = CFHD_MetadataOpen(&meta);
	uint32_t bfmt = d.cfa;
	if (!err) err = CFHD_MetadataAdd(meta, TAG_BAYER_FORMAT, METADATATYPE_UINT32, 4, &bfmt, false);
	CFHD_EncodingQuality q = (CFHD_EncodingQuality)qual;
	if (!err) err = CFHD_PrepareToEncode(enc, d.w, d.h, CFHD_PIXEL_FORMAT_BYR4, CFHD_ENCODED_FORMAT_BAYER, CFHD_ENCODING_FLAGS_NONE, q);
	if (!err) err = CFHD_MetadataAttach(enc, meta);
	if (err) { fprintf(stderr, "encoder setup failed: %d\n", err); return 1; }

	void *sample = NULL; size_t sampleSize = 0;
	double encMs = 1e9;
#ifdef ENCODE_ONLY
	const int passes = 1; // emulated: one encode, the instruction count is what matters
#else
	const int passes = 5; // best of 5
#endif
	for (int pass = 0; pass < passes; pass++) {
		auto t0 = std::chrono::steady_clock::now();
#ifdef ENCODE_ONLY
		syscall(EMU_MARK, 1); // the emulator counts instructions between the two marks
#endif
		err = CFHD_EncodeSample(enc, byr4.data(), d.w * 2);
		if (!err) err = CFHD_GetSampleData(enc, &sample, &sampleSize);
#ifdef ENCODE_ONLY
		syscall(EMU_MARK, 0);
#endif
		auto t1 = std::chrono::steady_clock::now();
		if (err) { fprintf(stderr, "encode failed: %d\n", err); return 1; }
		double ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
		if (ms < encMs) encMs = ms;
	}
	if (out) { FILE *f = fopen(out, "wb"); fwrite(sample, 1, sampleSize, f); fclose(f); }

	// -d: score a sample produced elsewhere (e.g. the ARM build) instead of our own encode
	std::vector<uint8_t> extSample;
	if (ext) {
		FILE *f = fopen(ext, "rb");
		if (!f) { fprintf(stderr, "cannot open %s\n", ext); return 1; }
		fseek(f, 0, SEEK_END); extSample.resize(ftell(f)); fseek(f, 0, SEEK_SET);
		if (fread(extSample.data(), 1, extSample.size(), f) != extSample.size()) { fclose(f); return 1; }
		fclose(f);
		sample = extSample.data(); sampleSize = extSample.size();
	}

#ifdef ENCODE_ONLY
	printf("%s  %dx%d  q=%d  cineform %zu B  ratio %.2f:1\n", in, d.w, d.h, qual, sampleSize, npix * 1.5 / sampleSize);
	CFHD_MetadataClose(meta);
	CFHD_CloseEncoder(enc);
	return 0;
#else

	CFHD_DecoderRef decr = NULL;
	int aw = 0, ah = 0; CFHD_PixelFormat af;
	err = CFHD_OpenDecoder(&decr, NULL);
	if (!err) err = CFHD_PrepareToDecode(decr, d.w, d.h, CFHD_PIXEL_FORMAT_BYR4, CFHD_DECODED_RESOLUTION_FULL,
										 CFHD_DECODING_FLAGS_NONE, sample, sampleSize, &aw, &ah, &af);
	if (!err) err = CFHD_DecodeSample(decr, sample, sampleSize, dec.data(), d.w * 2);
	if (err) { fprintf(stderr, "decode failed: %d (actual %dx%d fmt %08x)\n", err, aw, ah, af); return 1; }

	// Error measured back in the camera's 12-bit code values
	double se = 0, maxe = 0, seDark = 0, seClip = 0, sumClip = 0; size_t nDark = 0, nClip = 0;
	for (size_t i = 0; i < npix; i++) {
		double back = dec[i] / scale + black;
		double e = back - raw[i];
		se += e * e; if (fabs(e) > maxe) maxe = fabs(e);
		if (raw[i] < black + 64) { seDark += e * e; nDark++; }
		if (raw[i] >= white) { seClip += e * e; sumClip += e; nClip++; }
	}
	double rmse = sqrt(se / npix), psnr = 20 * log10((white - black) / rmse);
	double packed = npix * 1.5;
	printf("%s  %dx%d  cfa=%d  black=%.1f white=%.0f  q=%d\n", in, d.w, d.h, d.cfa, black, white, qual);
	printf("  cineform %zu B  ratio vs 12-bit packed %.2f:1  (%.2f bits/px)\n", sampleSize, packed / sampleSize, sampleSize * 8.0 / npix);
	printf("  rmse %.2f DN12  max %.0f DN12  psnr %.1f dB  dark-rmse %.2f DN12 (%zu px)\n", rmse, maxe, psnr, nDark ? sqrt(seDark / nDark) : 0, nDark);
	if (nClip)
		printf("  clipped %zu px: mean err %.1f DN12; unclipped rmse %.2f DN12\n", nClip, sumClip / nClip, sqrt((se - seClip) / (npix - nClip)));
	printf("  encode %.1f ms  = %.1f Mpix/s\n", encMs, npix / encMs / 1000.0);

	if (rt) { // 16-bit PGM of the decoded mosaic, for eyeballing
		FILE *f = fopen(rt, "wb");
		fprintf(f, "P5\n%d %d\n65535\n", d.w, d.h);
		for (size_t i = 0; i < npix; i++) { uint8_t be[2] = {(uint8_t)(dec[i] >> 8), (uint8_t)dec[i]}; fwrite(be, 1, 2, f); }
		fclose(f);
	}
	CFHD_CloseDecoder(decr);
	CFHD_MetadataClose(meta);
	CFHD_CloseEncoder(enc);
	return 0;
#endif
}
