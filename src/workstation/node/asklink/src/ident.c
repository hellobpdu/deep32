/*
 * Node identity (ECDSA P-256, self-signed; the pin, not the CA or the
 * expiry, is the trust anchor) and relay pin records.  Files live in the
 * state directory (default /etc/d32/state/chat), mode 0600, root only.
 *
 *   node.key   PKCS#8 PEM private key      node.crt   PEM certificate
 *   <link>.pin "d32u-pin 1\nid <relay id>\npin <64 hex>\n"
 */
#include <sys/types.h>
#include <sys/stat.h>

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <sha2.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <openssl/bio.h>
#include <openssl/ec.h>
#include <openssl/evp.h>
#include <openssl/obj_mac.h>
#include <openssl/pem.h>
#include <openssl/x509.h>
#include <openssl/x509v3.h>

#include "chatd.h"

static int
path_of(char *out, size_t outlen, const char *dir, const char *name,
    const char *suffix)
{
	int	 n = snprintf(out, outlen, "%s/%s%s", dir, name, suffix);

	return (n < 0 || (size_t)n >= outlen) ? -1 : 0;
}

/* Reads a private (0600, owner = us) file of at most 64 KiB. */
static int
read_private(const char *path, char **out, size_t *outlen, int need_private)
{
	struct stat	 st;
	char		*b;
	ssize_t		 n;
	size_t		 got = 0;
	int		 fd;

	if ((fd = open(path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC)) == -1)
		return -1;
	if (fstat(fd, &st) == -1 || !S_ISREG(st.st_mode) ||
	    st.st_size > 65536 ||
	    (need_private && ((st.st_mode & 077) || st.st_uid != geteuid()))) {
		close(fd);
		errno = EPERM;
		return -1;
	}
	if ((b = calloc(1, st.st_size + 1)) == NULL) {
		close(fd);
		return -1;
	}
	while (got < (size_t)st.st_size &&
	    (n = read(fd, b + got, st.st_size - got)) > 0)
		got += n;
	close(fd);
	if (got != (size_t)st.st_size) {
		freezero(b, st.st_size + 1);
		return -1;
	}
	*out = b;
	*outlen = got;
	return 0;
}

static int
write_atomic(const char *path, const void *data, size_t len, mode_t mode)
{
	char	 tmp[PATH_MAX];
	int	 fd, n;

	n = snprintf(tmp, sizeof(tmp), "%s.tmp%d", path, (int)getpid());
	if (n < 0 || (size_t)n >= sizeof(tmp))
		return -1;
	if ((fd = open(tmp, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW |
	    O_CLOEXEC, mode)) == -1)
		return -1;
	if (fchmod(fd, mode) == -1 || write(fd, data, len) != (ssize_t)len ||
	    fsync(fd) == -1) {
		close(fd);
		unlink(tmp);
		return -1;
	}
	close(fd);
	if (rename(tmp, path) == -1) {
		unlink(tmp);
		return -1;
	}
	return 0;
}

int
ident_generate(const char *dir)
{
	char		 kpath[PATH_MAX], cpath[PATH_MAX];
	EVP_PKEY_CTX	*pctx = NULL;
	EVP_PKEY	*pkey = NULL;
	X509		*x = NULL;
	X509_NAME	*name;
	X509_EXTENSION	*ext = NULL;
	BIO		*kb = NULL, *cb = NULL;
	BUF_MEM		*km = NULL, *cm = NULL;
	unsigned char	 serial[16];
	BIGNUM		*bn = NULL;
	int		 rv = -1;

	if (path_of(kpath, sizeof(kpath), dir, "node", ".key") == -1 ||
	    path_of(cpath, sizeof(cpath), dir, "node", ".crt") == -1)
		return -1;
	if (access(kpath, F_OK) == 0 || access(cpath, F_OK) == 0) {
		errno = EEXIST;
		return -1;
	}
	if ((pctx = EVP_PKEY_CTX_new_id(EVP_PKEY_EC, NULL)) == NULL ||
	    EVP_PKEY_keygen_init(pctx) <= 0 ||
	    EVP_PKEY_CTX_set_ec_paramgen_curve_nid(pctx,
	    NID_X9_62_prime256v1) <= 0 ||
	    EVP_PKEY_keygen(pctx, &pkey) <= 0)
		goto done;
	arc4random_buf(serial, sizeof(serial));
	serial[0] &= 0x7f;
	if ((x = X509_new()) == NULL || !X509_set_version(x, 2) ||
	    (bn = BN_bin2bn(serial, sizeof(serial), NULL)) == NULL ||
	    BN_to_ASN1_INTEGER(bn, X509_get_serialNumber(x)) == NULL ||
	    X509_gmtime_adj(X509_getm_notBefore(x), -86400) == NULL ||
	    X509_gmtime_adj(X509_getm_notAfter(x), 3650L * 86400) == NULL ||
	    !X509_set_pubkey(x, pkey))
		goto done;
	name = X509_get_subject_name(x);
	if (!X509_NAME_add_entry_by_txt(name, "CN", MBSTRING_ASC,
	    (const unsigned char *)"Deep32 node D32U", -1, -1, 0) ||
	    !X509_set_issuer_name(x, name))
		goto done;
	if ((ext = X509V3_EXT_conf_nid(NULL, NULL, NID_key_usage,
	    "critical,digitalSignature")) == NULL ||
	    !X509_add_ext(x, ext, -1) ||
	    !X509_sign(x, pkey, EVP_sha256()))
		goto done;
	if ((kb = BIO_new(BIO_s_mem())) == NULL ||
	    (cb = BIO_new(BIO_s_mem())) == NULL ||
	    !PEM_write_bio_PrivateKey(kb, pkey, NULL, NULL, 0, NULL, NULL) ||
	    !PEM_write_bio_X509(cb, x))
		goto done;
	BIO_get_mem_ptr(kb, &km);
	BIO_get_mem_ptr(cb, &cm);
	if (write_atomic(kpath, km->data, km->length, 0600) == -1 ||
	    write_atomic(cpath, cm->data, cm->length, 0644) == -1)
		goto done;
	rv = 0;
 done:
	BN_free(bn);
	X509_EXTENSION_free(ext);
	if (kb != NULL) {
		/* the private key PEM must not linger in freed memory */
		BIO_get_mem_ptr(kb, &km);
		if (km != NULL && km->data != NULL)
			explicit_bzero(km->data, km->length);
		BIO_free(kb);
	}
	BIO_free(cb);
	X509_free(x);
	EVP_PKEY_free(pkey);
	EVP_PKEY_CTX_free(pctx);
	return rv;
}

int
ident_load(const char *dir, char **cert, size_t *certlen, char **key,
    size_t *keylen)
{
	char	 kpath[PATH_MAX], cpath[PATH_MAX];

	if (path_of(kpath, sizeof(kpath), dir, "node", ".key") == -1 ||
	    path_of(cpath, sizeof(cpath), dir, "node", ".crt") == -1)
		return -1;
	if (read_private(cpath, cert, certlen, 0) == -1)
		return -1;
	if (read_private(kpath, key, keylen, 1) == -1) {
		free(*cert);
		return -1;
	}
	return 0;
}

/* pin = SHA-256 of the complete DER certificate (sas.md item 2). */
int
ident_pin(const char *pem, size_t len, uint8_t *pin)
{
	BIO		*b;
	X509		*x;
	unsigned char	*der = NULL;
	int		 n;
	SHA2_CTX	 ctx;

	if ((b = BIO_new_mem_buf(pem, len)) == NULL)
		return -1;
	x = PEM_read_bio_X509(b, NULL, NULL, NULL);
	BIO_free(b);
	if (x == NULL)
		return -1;
	n = i2d_X509(x, &der);
	X509_free(x);
	if (n <= 0)
		return -1;
	SHA256Init(&ctx);
	SHA256Update(&ctx, der, n);
	SHA256Final(pin, &ctx);
	free(der);
	return 0;
}

int
pin_load(const char *dir, const char *link, struct pin_rec *r)
{
	char	 path[PATH_MAX], *b, *id, *hex, *nl;
	size_t	 len;
	int	 rv = -1;

	memset(r, 0, sizeof(*r));
	if (path_of(path, sizeof(path), dir, link, ".pin") == -1)
		return -1;
	if (read_private(path, &b, &len, 1) == -1)
		return -1;
	if (len < 12 || strncmp(b, "d32u-pin 1\nid ", 14) != 0)
		goto done;
	id = b + 14;
	if ((nl = strchr(id, '\n')) == NULL)
		goto done;
	*nl = '\0';
	if (!d32u_id_ok(id, strlen(id)))
		goto done;
	hex = nl + 1;
	if (strncmp(hex, "pin ", 4) != 0)
		goto done;
	hex += 4;
	if ((nl = strchr(hex, '\n')) == NULL || nl[1] != '\0')
		goto done;
	if (hex_decode(hex, nl - hex, r->pin, D32U_PIN_LEN) == -1)
		goto done;
	strlcpy(r->relay_id, id, sizeof(r->relay_id));
	rv = 0;
 done:
	freezero(b, len + 1);
	return rv;
}

int
pin_save(const char *dir, const char *link, const struct pin_rec *r)
{
	char	 path[PATH_MAX], hex[2 * D32U_PIN_LEN + 1], out[256];
	int	 n;

	if (!d32u_id_ok(r->relay_id, strlen(r->relay_id)) ||
	    path_of(path, sizeof(path), dir, link, ".pin") == -1)
		return -1;
	hex_encode(r->pin, D32U_PIN_LEN, hex);
	n = snprintf(out, sizeof(out), "d32u-pin 1\nid %s\npin %s\n",
	    r->relay_id, hex);
	if (n < 0 || (size_t)n >= sizeof(out))
		return -1;
	return write_atomic(path, out, n, 0600);
}

int
pin_delete(const char *dir, const char *link)
{
	char	 path[PATH_MAX];

	if (path_of(path, sizeof(path), dir, link, ".pin") == -1)
		return -1;
	if (unlink(path) == -1 && errno != ENOENT)
		return -1;
	return 0;
}
