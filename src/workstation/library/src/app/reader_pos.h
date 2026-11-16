#ifndef E26_READER_POS_H
#define E26_READER_POS_H

#define E26_READER_HASH_HEX 64

typedef struct {
	char section[129], hash[E26_READER_HASH_HEX + 1];
	int block, text;
} e26_reader_pos;

#endif
