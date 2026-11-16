#include "screencap.h"

#include <limits.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>

enum {
	SIGNATURE_BYTES = 8,
	CHUNK_HEADER_BYTES = 8,
	CHUNK_OVERHEAD = CHUNK_HEADER_BYTES + 4,
	IHDR_BYTES = 13,
	IDAT_OFFSET = SIGNATURE_BYTES + CHUNK_OVERHEAD + IHDR_BYTES,
	PNG_OVERHEAD = IDAT_OFFSET + 2 * CHUNK_OVERHEAD,
	/* compress2 uses zlib's default deflate window and memory level. */
	ZLIB_WORKSPACE = 512 * 1024
};

struct png_size {
	size_t rgb_stride;
	size_t filtered;
	uLongf compressed_bound;
};

static enum sc_result
measure(unsigned width, unsigned height, struct png_size *size)
{
	const size_t limit = SC_BUFFER_LIMIT;
	size_t pixels, stride, raw;
	uLongf bound;

	if (!width || !height)
		return SC_ERROR;
	if (width > SC_DIMENSION_LIMIT || height > SC_DIMENSION_LIMIT)
		return SC_LIMIT;
	stride = (size_t)width * 3;
	if (height > limit / (stride + 1))
		return SC_LIMIT;
	pixels = stride * height;
	raw = pixels + height; /* One filter byte per row. */
	if (pixels > limit - raw || PNG_OVERHEAD + ZLIB_WORKSPACE > limit - raw - pixels)
		return SC_LIMIT;
	bound = compressBound((uLong)raw);
	if (bound > limit - raw - pixels - PNG_OVERHEAD - ZLIB_WORKSPACE ||
	    bound > UINT_MAX - 4u)
		return SC_LIMIT;
	*size = (struct png_size){ stride, raw, bound };
	return SC_OK;
}

enum sc_result
sc_png_fits(unsigned width, unsigned height)
{
	struct png_size size;
	return measure(width, height, &size);
}

static void
store32(unsigned char *dest, uint32_t value)
{
	dest[0] = (unsigned char)(value >> 24);
	dest[1] = (unsigned char)(value >> 16);
	dest[2] = (unsigned char)(value >> 8);
	dest[3] = (unsigned char)value;
}

/* Payload is already in place. CRC includes the type and payload. */
static void
finish_chunk(unsigned char *chunk, const char type[4], size_t length)
{
	uLong crc = crc32(0, Z_NULL, 0);
	store32(chunk, (uint32_t)length);
	memcpy(chunk + 4, type, 4);
	crc = crc32(crc, chunk + 4, (uInt)(length + 4));
	store32(chunk + CHUNK_HEADER_BYTES + length, (uint32_t)crc);
}

enum sc_result
sc_png(const struct sc_image *image, unsigned char **out, size_t *length)
{
	static const unsigned char signature[SIGNATURE_BYTES] = {
		0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n'
	};
	struct png_size size;
	unsigned char *filtered, *png, *row, *ihdr, *idat;
	uLongf compressed;
	enum sc_result result;
	unsigned y;
	int status;

	if (out)
		*out = NULL;
	if (length)
		*length = 0;
	if (!out || !length || !image || !image->rgb)
		return SC_ERROR;
	result = measure(image->width, image->height, &size);
	if (result != SC_OK)
		return result;
	filtered = malloc(size.filtered);
	if (!filtered)
		return SC_ERROR;
	png = malloc((size_t)size.compressed_bound + PNG_OVERHEAD);
	if (!png) {
		free(filtered);
		return SC_ERROR;
	}
	for (y = 0, row = filtered; y < image->height; y++) {
		*row++ = 0;
		memcpy(row, image->rgb + (size_t)y * size.rgb_stride, size.rgb_stride);
		row += size.rgb_stride;
	}
	idat = png + IDAT_OFFSET;
	compressed = size.compressed_bound;
	status = compress2(idat + CHUNK_HEADER_BYTES, &compressed, filtered,
	    (uLong)size.filtered, 6);
	free(filtered);
	if (status != Z_OK) {
		free(png);
		return SC_ERROR;
	}

	memcpy(png, signature, SIGNATURE_BYTES);
	ihdr = png + SIGNATURE_BYTES + CHUNK_HEADER_BYTES;
	store32(ihdr, image->width);
	store32(ihdr + 4, image->height);
	ihdr[8] = 8; /* Bit depth. */
	ihdr[9] = 2; /* TrueColor RGB. */
	ihdr[10] = ihdr[11] = ihdr[12] = 0; /* Compression, filter, interlace. */
	finish_chunk(png + SIGNATURE_BYTES, "IHDR", IHDR_BYTES);
	finish_chunk(idat, "IDAT", (size_t)compressed);
	finish_chunk(idat + CHUNK_OVERHEAD + compressed, "IEND", 0);
	*out = png;
	*length = PNG_OVERHEAD + (size_t)compressed;
	return SC_OK;
}
