// zim.cpp: e26-lib's libzim shim (W1-DATA; zimshim.h). Read-only: an Archive per opened file, entries by path
// (reader.zim's A/<h0h1>/<h>.art articles, and media paths in the encyclopedia layers), partial reads through
// Item::getData(offset, size) so a large item is never read whole for one chunk. No exception leaves this file.
#include "zimshim.h"
#include <zim/archive.h>
#include <zim/entry.h>
#include <zim/error.h>
#include <zim/item.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <string>

extern "C" void *lib_zim_open(const char *path, char *err, size_t errlen)
{
	try {
		return new zim::Archive(std::string(path));
	} catch (const std::exception &e) {
		if (err && errlen) std::snprintf(err, errlen, "%s", e.what());
	} catch (...) {
		if (err && errlen) std::snprintf(err, errlen, "unreadable ZIM");
	}
	return nullptr;
}

extern "C" void lib_zim_close(void *z)
{
	delete static_cast<zim::Archive *>(z);
}

extern "C" int lib_zim_read(void *z, const char *path, uint64_t offset, size_t limit, unsigned char **data, size_t *len,
                            uint64_t *total)
{
	*data = nullptr;
	*len = 0;
	*total = 0;
	try {
		zim::Archive *a = static_cast<zim::Archive *>(z);
		if (!a->hasEntryByPath(path)) return 0;
		zim::Item it = a->getEntryByPath(path).getItem(true);
		uint64_t size = it.getSize();
		uint64_t n = offset >= size ? 0 : size - offset;
		if (n > limit) n = limit;
		*total = size;
		*data = static_cast<unsigned char *>(std::malloc(n ? n : 1));
		if (!*data) return -1;
		if (n) {
			zim::Blob b = it.getData(static_cast<zim::offset_type>(offset), static_cast<zim::size_type>(n));
			if (b.size() != n) { std::free(*data); *data = nullptr; return -1; }
			std::memcpy(*data, b.data(), n);
		}
		*len = static_cast<size_t>(n);
		return 1;
	} catch (const zim::EntryNotFound &) {
		return 0;
	} catch (...) {
		std::free(*data);
		*data = nullptr;
		return -1;
	}
}
