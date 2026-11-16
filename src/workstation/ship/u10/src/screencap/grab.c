#include "screencap.h"

#include <X11/Xutil.h>
#include <X11/extensions/Xrandr.h>

#include <limits.h>
#include <stdint.h>
#include <stdlib.h>

struct channel {
	uint32_t maximum;
	unsigned shift;
};

static enum sc_result
root_attributes(Display *display, Window root, XWindowAttributes *attrs)
{
	if (!display || root == None)
		return SC_ERROR;
	if (!XGetWindowAttributes(display, root, attrs))
		return SC_WINDOW_CLOSED;
	if (!attrs->screen || RootWindowOfScreen(attrs->screen) != root ||
	    attrs->width <= 0 || attrs->height <= 0)
		return SC_ERROR;
	return SC_OK;
}

static int64_t
min_edge(int64_t a, int64_t b)
{
	return a < b ? a : b;
}

static int64_t
max_edge(int64_t a, int64_t b)
{
	return a > b ? a : b;
}

static int
intersect(const struct sc_rect *a, const struct sc_rect *b, struct sc_rect *out)
{
	int64_t x = max_edge(a->x, b->x), y = max_edge(a->y, b->y);
	int64_t right = min_edge((int64_t)a->x + a->width, (int64_t)b->x + b->width);
	int64_t bottom = min_edge((int64_t)a->y + a->height, (int64_t)b->y + b->height);

	if (right <= x || bottom <= y)
		return 0;
	*out = (struct sc_rect){ (int)x, (int)y, (unsigned)(right - x),
	    (unsigned)(bottom - y) };
	return 1;
}

/* Each root is one coordinate space. Distinct X screens cannot form a RandR
 * monitor union. The caller selects the root and handles that mode limit. */
enum sc_result
sc_monitors(Display *display, Window root, struct sc_monitor_list *out)
{
	struct sc_monitor_list found = { 0 };
	struct sc_rect screen, reported, clipped;
	XWindowAttributes attrs;
	XRRMonitorInfo *monitors;
	int event, error, major, minor, count = 0, i;
	enum sc_result result;

	if (!out)
		return SC_ERROR;
	out->count = 0;
	result = root_attributes(display, root, &attrs);
	if (result != SC_OK)
		return result;
	screen = (struct sc_rect){ 0, 0, (unsigned)attrs.width, (unsigned)attrs.height };
	if (!XRRQueryExtension(display, &event, &error) ||
	    !XRRQueryVersion(display, &major, &minor) || major < 1 ||
	    (major == 1 && minor < 5))
		goto fallback;
	monitors = XRRGetMonitors(display, root, True, &count);
	if (!monitors && count == 0)
		goto fallback;
	if (!monitors)
		return SC_ERROR;
	if (count < 0 || (size_t)count > sizeof(found.items) / sizeof(found.items[0])) {
		XRRFreeMonitors(monitors);
		return SC_LIMIT;
	}
	for (i = 0; i < count; i++) {
		if (monitors[i].width <= 0 || monitors[i].height <= 0)
			continue;
		reported = (struct sc_rect){ monitors[i].x, monitors[i].y,
		    (unsigned)monitors[i].width, (unsigned)monitors[i].height };
		if (intersect(&screen, &reported, &clipped))
			found.items[found.count++] = clipped;
	}
	XRRFreeMonitors(monitors);
	if (count == 0)
		goto fallback;
	if (found.count == 0)
		return SC_ERROR;
	*out = found;
	return SC_OK;

fallback:
	out->items[0] = screen;
	out->count = 1;
	return SC_OK;
}

enum sc_result
sc_bounds(const struct sc_monitor_list *monitors, struct sc_rect *out)
{
	const struct sc_rect *item;
	int64_t left, top, right, bottom;
	size_t i;

	if (!out || !monitors || !monitors->count)
		return SC_ERROR;
	if (monitors->count > sizeof(monitors->items) / sizeof(monitors->items[0]))
		return SC_LIMIT;
	item = &monitors->items[0];
	left = item->x;
	top = item->y;
	right = left + item->width;
	bottom = top + item->height;
	for (i = 0; i < monitors->count; i++) {
		item = &monitors->items[i];
		if (!item->width || !item->height)
			return SC_ERROR;
		left = min_edge(left, item->x);
		top = min_edge(top, item->y);
		right = max_edge(right, (int64_t)item->x + item->width);
		bottom = max_edge(bottom, (int64_t)item->y + item->height);
	}
	if (right - left > UINT_MAX || bottom - top > UINT_MAX)
		return SC_LIMIT;
	*out = (struct sc_rect){ (int)left, (int)top, (unsigned)(right - left),
	    (unsigned)(bottom - top) };
	return SC_OK;
}

static int
make_channel(unsigned long mask, struct channel *out)
{
	uint32_t value;
	unsigned shift = 0;

	if (!mask || mask > UINT32_MAX)
		return 0;
	value = (uint32_t)mask;
	while ((value & 1u) == 0) {
		shift++;
		value >>= 1;
	}
	if ((value & (value + 1u)) != 0)
		return 0;
	*out = (struct channel){ value, shift };
	return 1;
}

static enum sc_result
pixmap_stride(Display *display, int depth, unsigned width, int *bits, size_t *stride)
{
	XPixmapFormatValues *formats;
	int count, i, pad = 0;

	formats = XListPixmapFormats(display, &count);
	if (!formats)
		return SC_ERROR;
	for (i = 0; i < count; i++) {
		if (formats[i].depth == depth) {
			*bits = formats[i].bits_per_pixel;
			pad = formats[i].scanline_pad;
			break;
		}
	}
	XFree(formats);
	if ((pad != 8 && pad != 16 && pad != 32) ||
	    (*bits != 16 && *bits != 24 && *bits != 32) || *bits < depth)
		return SC_UNSUPPORTED;
	/* width is already bounded at 16384. */
	*stride = (((size_t)width * (unsigned)*bits + (unsigned)pad - 1) /
	    (unsigned)pad) * ((unsigned)pad / 8);
	return SC_OK;
}

static int
left_to_right(const void *left, const void *right)
{
	const struct sc_rect *a = left, *b = right;
	return (a->x > b->x) - (a->x < b->x);
}

static enum sc_result
clip_mask(const struct sc_rect *area, const struct sc_monitor_list *mask,
    struct sc_monitor_list *clips)
{
	struct sc_rect clip;
	size_t i;

	if (!mask)
		return SC_OK;
	if (!mask->count)
		return SC_ERROR;
	if (mask->count > sizeof(mask->items) / sizeof(mask->items[0]))
		return SC_LIMIT;
	for (i = 0; i < mask->count; i++) {
		if (!mask->items[i].width || !mask->items[i].height)
			return SC_ERROR;
		if (intersect(area, &mask->items[i], &clip)) {
			clip.x -= area->x;
			clip.y -= area->y;
			clips->items[clips->count++] = clip;
		}
	}
	qsort(clips->items, clips->count, sizeof(clips->items[0]), left_to_right);
	return SC_OK;
}

static uint32_t
read_pixel(const unsigned char *bytes, unsigned count, int order)
{
	uint32_t value = 0;
	unsigned i;

	if (order == LSBFirst) {
		for (i = 0; i < count; i++)
			value |= (uint32_t)bytes[i] << (i * 8);
	} else {
		for (i = 0; i < count; i++)
			value = (value << 8) | bytes[i];
	}
	return value;
}

static unsigned char
scale_channel(uint32_t pixel, const struct channel *channel)
{
	uint32_t component = (pixel >> channel->shift) & channel->maximum;
	if (channel->maximum == 255)
		return (unsigned char)component;
	return (unsigned char)(((uint64_t)component * 255 + channel->maximum / 2) /
	    channel->maximum);
}

static void
copy_span(const unsigned char *source, unsigned char *output, unsigned first,
    unsigned last, unsigned bytes, int order, const struct channel *channels)
{
	uint32_t pixel;
	unsigned x;

	source += (size_t)first * bytes;
	output += (size_t)first * 3;
	for (x = first; x < last; x++) {
		pixel = read_pixel(source, bytes, order);
		output[0] = scale_channel(pixel, &channels[0]);
		output[1] = scale_channel(pixel, &channels[1]);
		output[2] = scale_channel(pixel, &channels[2]);
		source += bytes;
		output += 3;
	}
}

static void
copy_row(const XImage *image, const struct sc_monitor_list *clips,
    unsigned y, unsigned char *output, const struct channel *channels)
{
	const unsigned char *source = (const unsigned char *)image->data +
	    (size_t)y * (size_t)image->bytes_per_line;
	unsigned bytes = (unsigned)image->bits_per_pixel / 8;
	const struct sc_rect *clip;
	unsigned end = 0, first, last;
	size_t i;

	if (!clips) {
		copy_span(source, output, 0, (unsigned)image->width, bytes,
		    image->byte_order, channels);
		return;
	}
	/* The caller zeroed RGB. Convert only the union, retaining black holes. */
	for (i = 0; i < clips->count; i++) {
		clip = &clips->items[i];
		if (y < (unsigned)clip->y || y >= (unsigned)clip->y + clip->height)
			continue;
		first = (unsigned)clip->x > end ? (unsigned)clip->x : end;
		last = (unsigned)clip->x + clip->width;
		if (last > first)
			copy_span(source, output, first, last, bytes, image->byte_order, channels);
		if (last > end)
			end = last;
	}
}

enum sc_result
sc_grab(Display *display, Window root, const struct sc_rect *area,
    const struct sc_monitor_list *mask, struct sc_image *out)
{
	struct sc_monitor_list clips = { 0 };
	XWindowAttributes attrs;
	XImage *image;
	struct channel channels[3];
	unsigned long red, green, blue;
	unsigned char *rgb;
	size_t stride, rgb_stride, size;
	int bits = 0;
	unsigned y;
	enum sc_result result;

	if (!out)
		return SC_ERROR;
	*out = (struct sc_image){ 0 };
	if (!area || !area->width || !area->height || area->x < 0 || area->y < 0)
		return SC_ERROR;
	/* GetImage encodes x and y as signed 16-bit protocol fields. */
	if (area->width > SC_DIMENSION_LIMIT || area->height > SC_DIMENSION_LIMIT ||
	    area->x > INT16_MAX || area->y > INT16_MAX)
		return SC_LIMIT;
	result = root_attributes(display, root, &attrs);
	if (result != SC_OK)
		return result;
	if ((int64_t)area->x + area->width > attrs.width ||
	    (int64_t)area->y + area->height > attrs.height)
		return SC_WINDOW_CLOSED;
	if (!attrs.visual || attrs.visual->class != TrueColor ||
	    (attrs.depth != 16 && attrs.depth != 24 && attrs.depth != 32))
		return SC_UNSUPPORTED;
	red = attrs.visual->red_mask;
	green = attrs.visual->green_mask;
	blue = attrs.visual->blue_mask;
	if ((red & green) || (red & blue) || (green & blue) ||
	    !make_channel(red, &channels[0]) || !make_channel(green, &channels[1]) ||
	    !make_channel(blue, &channels[2]))
		return SC_UNSUPPORTED;
	result = pixmap_stride(display, attrs.depth, area->width, &bits, &stride);
	if (result != SC_OK)
		return result;
	if (((uint64_t)(red | green | blue) >> bits) != 0)
		return SC_UNSUPPORTED;
	rgb_stride = (size_t)area->width * 3;
	if (area->height > (size_t)SC_BUFFER_LIMIT / (stride + rgb_stride))
		return SC_LIMIT;
	size = rgb_stride * area->height;
	result = clip_mask(area, mask, &clips);
	if (result != SC_OK)
		return result;

	image = XGetImage(display, root, area->x, area->y, area->width,
	    area->height, AllPlanes, ZPixmap);
	if (!image)
		return SC_WINDOW_CLOSED;
	if (!image->data || image->format != ZPixmap || image->depth != attrs.depth ||
	    image->bits_per_pixel != bits || image->xoffset != 0 ||
	    image->width != (int)area->width || image->height != (int)area->height ||
	    image->bytes_per_line != (int)stride ||
	    (image->byte_order != LSBFirst && image->byte_order != MSBFirst)) {
		XDestroyImage(image);
		return SC_UNSUPPORTED;
	}
	rgb = mask ? calloc(1, size) : malloc(size);
	if (!rgb) {
		XDestroyImage(image);
		return SC_ERROR;
	}
	for (y = 0; y < area->height; y++)
		copy_row(image, mask ? &clips : NULL, y, rgb + y * rgb_stride, channels);
	XDestroyImage(image);
	*out = (struct sc_image){ area->width, area->height, rgb };
	return SC_OK;
}
