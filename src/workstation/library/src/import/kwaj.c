/* Portable C99 KWAJ decoder. Only ISO C library calls, no platform APIs.
 * Method 3: MSB-first canonical Huffman, five tables, 4096-byte history.
 * Method 4 includes a small raw DEFLATE reader (no zlib dependency).
 * Build CLI: clang -std=c99 -Wall -Wextra -Werror -DKWAJ_MAIN kwaj.c -o kwaj
 */
#include "kwaj.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#ifdef KWAJ_MAIN
#include <stdio.h>
#endif
#define MAX_OUTPUT ((size_t)256 * 1024 * 1024)
typedef struct { unsigned char *p; size_t n, cap; int error; } Output;
typedef struct { const unsigned char *p; size_t n, bit; int little, error; } Bits;
typedef struct { unsigned count[16], first[16], base[16], symbols[288]; } Tree;
static unsigned u16(const unsigned char *p) { return p[0] | ((unsigned)p[1] << 8); }
static uint32_t u32(const unsigned char *p) { return (uint32_t)u16(p) | ((uint32_t)u16(p+2) << 16); }
static void put(Output *o, unsigned v) {
    if (o->error) return;
    if (o->n == MAX_OUTPUT) { o->error = 3; return; }
    if (o->n == o->cap) {
        size_t cap = o->cap ? o->cap * 2 : 4096;
        unsigned char *p = (unsigned char *)realloc(o->p, cap);
        if (!p) { o->error = 3; return; }
        o->p = p; o->cap = cap;
    }
    o->p[o->n++] = (unsigned char)v;
}
static unsigned bits(Bits *b, unsigned n) {
    unsigned v = 0, i;
    if (b->error || n > 24 || b->bit > b->n * 8 || n > b->n * 8 - b->bit) {
        b->error = 1; return 0;
    }
    for (i=0; i<n; i++, b->bit++) {
        unsigned c = (b->p[b->bit / 8] >> (b->little ? b->bit % 8 : 7-b->bit % 8)) & 1;
        if (b->little) v |= c << i; else v = (v << 1) | c;
    }
    return v;
}
static int tree(Tree *t, const unsigned *length, unsigned n, int complete) {
    unsigned i, k, code=0, total=0, next[16];
    memset(t, 0, sizeof(*t));
    if (n > 288) return 2;
    for (i=0; i<n; i++) {
        if (length[i] > 15) return 2;
        if (length[i]) t->count[length[i]]++;
    }
    for (k=1; k<16; k++) {
        code = (code + t->count[k-1]) << 1;
        if (code + t->count[k] > (1u << k)) return 2;
        t->first[k]=code; t->base[k]=total; next[k]=total; total += t->count[k];
    }
    if (complete && code + t->count[15] != 32768) return 2;
    for (i=0; i<n; i++) if (length[i]) t->symbols[next[length[i]]++] = i;
    return 0;
}
static unsigned symbol(Bits *b, const Tree *t) {
    unsigned code=0, n;
    for (n=1; n<16; n++) {
        code = (code << 1) | bits(b,1);
        if (b->error) return 0;
        if (code >= t->first[n] && code - t->first[n] < t->count[n])
            return t->symbols[t->base[n] + code - t->first[n]];
    }
    b->error=2; return 0;
}
static int lzh(const unsigned char *p, size_t n, Output *o) {
    Bits b={p,n,0,0,0}; Tree t[5]; unsigned kind[5], lens[256], i,j,v,pos=0,last=0;
    static const unsigned sizes[5]={16,16,32,64,256};
    unsigned char window[4096]; memset(window, ' ', sizeof(window));
    for (i=0;i<5;i++) kind[i]=bits(&b,4);
    if (bits(&b,4) || b.error) return 2;
    for (i=0;i<5;i++) {
        if (kind[i]>3) return 2;
        v=0;
        if (kind[i]==0) { unsigned x=sizes[i]; while (x>1) {v++;x>>=1;} }
        else if (kind[i]!=3) v=bits(&b,4);
        for (j=0;j<sizes[i];j++) {
            if (kind[i]==3) v=bits(&b,4);
            else if (j && kind[i]==1) {
                if (bits(&b,1)) { if (bits(&b,1)) v=bits(&b,4); else v++; }
            } else if (j && kind[i]==2) {
                unsigned d=bits(&b,2); if (d==3) v=bits(&b,4); else v=v+d-1;
            }
            if (b.error || v>15) return 2;
            lens[j]=v;
        }
        if (tree(t+i,lens,sizes[i],1)) return 2;
    }
    while (!o->error) {
        unsigned length=symbol(&b,t+(last ? 1 : 0));
        if (b.error) break;
        if (length) {
            unsigned dist=symbol(&b,t+3); dist=(dist << 6) | bits(&b,6);
            if (b.error) break;
            for (j=0;j<length+2;j++) {
                v=window[(pos-dist)&4095]; put(o,v); window[pos]=(unsigned char)v; pos=(pos+1)&4095;
            }
            last=0;
        } else {
            unsigned char run[32]; length=symbol(&b,t+2)+1;
            if (b.error) break;
            for (j=0;j<length;j++) { run[j]=(unsigned char)symbol(&b,t+4); if(b.error) break; }
            if (b.error) break;
            for (j=0;j<length;j++) { v=run[j]; put(o,v); window[pos]=(unsigned char)v; pos=(pos+1)&4095; }
            last=length!=32;
        }
    }
    return b.error==2 ? 2 : o->error;
}
static int lzss(const unsigned char *p,size_t n,Output *o) {
    unsigned char window[4096]; unsigned pos=4096-18, j; size_t at=0;
    memset(window,' ',sizeof(window));
    while (at<n && !o->error) {
        unsigned flags=p[at++];
        for (j=0;j<8 && at<n;j++) {
            if (flags & (1u<<j)) { unsigned v=p[at++]; put(o,v);window[pos]=(unsigned char)v;pos=(pos+1)&4095; }
            else {
                unsigned a,c,k,source;
                if (n-at<2) return 2;
                a=p[at++];c=p[at++];source=a|((c&240)<<4);
                for(k=0;k<(c&15)+3;k++) {unsigned v=window[(source+k)&4095];put(o,v);window[pos]=(unsigned char)v;pos=(pos+1)&4095;}
            }
        }
    }
    return o->error;
}
/* Raw DEFLATE. Codes read most significant code bit first, numeric fields LSB. */
static int inflate_block(const unsigned char *p,size_t n,Output *o) {
    static const unsigned lb[29]={3,4,5,6,7,8,9,10,11,13,15,17,19,23,27,31,35,43,51,59,67,83,99,115,131,163,195,227,258};
    static const unsigned le[29]={0,0,0,0,0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,0};
    static const unsigned db[30]={1,2,3,4,5,7,9,13,17,25,33,49,65,97,129,193,257,385,513,769,1025,1537,2049,3073,4097,6145,8193,12289,16385,24577};
    static const unsigned de[30]={0,0,0,0,1,1,2,2,3,3,4,4,5,5,6,6,7,7,8,8,9,9,10,10,11,11,12,12,13,13};
    static const unsigned co[19]={16,17,18,0,8,7,9,6,10,5,11,4,12,3,13,2,14,1,15};
    Bits b={p,n,0,1,0}; unsigned final=0; size_t begin=o->n;
    while (!final && !b.error && !o->error) {
        unsigned type,i,lens[320],dist[32];Tree lt,dt;final=bits(&b,1);type=bits(&b,2);
        if(type==0) {
            unsigned len,inv;b.bit=(b.bit+7)&~(size_t)7;len=bits(&b,16);inv=bits(&b,16);
            if(b.error || (len^inv)!=65535) return 2;
            for(i=0;i<len;i++) {unsigned v=bits(&b,8);if(b.error)return 2;put(o,v);}
            continue;
        }
        if(type==1) {
            for(i=0;i<288;i++) lens[i]=i<144?8:i<256?9:i<280?7:8;
            for(i=0;i<32;i++) dist[i]=5;
            if(tree(&lt,lens,288,0)||tree(&dt,dist,32,0))return 2;
        } else if(type==2) {
            unsigned nl=bits(&b,5)+257,nd=bits(&b,5)+1,nc=bits(&b,4)+4,cl[19]={0};Tree ct;
            if(nl>286||nd>32)return 2;
            for(i=0;i<nc;i++)cl[co[i]]=bits(&b,3);
            if(b.error||tree(&ct,cl,19,0))return 2;
            i=0;
            while(i<nl+nd) {
                unsigned s=symbol(&b,&ct),repeat,value;
                if(b.error)return 2;
                if(s<16){lens[i++]=s;continue;}
                if(s==16){if(!i)return 2;repeat=bits(&b,2)+3;value=lens[i-1];}
                else if(s==17){repeat=bits(&b,3)+3;value=0;}
                else if(s==18){repeat=bits(&b,7)+11;value=0;}
                else return 2;
                if(b.error||repeat>nl+nd-i)return 2;
                while(repeat--)lens[i++]=value;
            }
            if(!lens[256]||tree(&lt,lens,nl,0)||tree(&dt,lens+nl,nd,0))return 2;
        } else return 2;
        for(;;) {
            unsigned s=symbol(&b,&lt),length,distance,k;
            if(b.error)return 2;
            if(s<256){put(o,s);continue;}
            if(s==256)break;
            if(s>285)return 2;
            s-=257;length=lb[s]+bits(&b,le[s]);s=symbol(&b,&dt);
            if(s>29||b.error)return 2;
            distance=db[s]+bits(&b,de[s]);
            if(b.error||distance>o->n||distance>32768)return 2;
            for(k=0;k<length;k++){unsigned v=o->p[o->n-distance];put(o,v);if(o->error)return o->error;}
            if(o->n-begin>32768)return 2;
        }
    }
    if(b.error || (b.bit+7)/8!=n || o->n-begin>32768)return 2;
    return o->error;
}
static int mszip(const unsigned char *p,size_t n,Output *o) {
    size_t at=0;
    while(at<n) {
        unsigned size;int rc;if(n-at<2)return 2;size=u16(p+at);at+=2;
        if(!size)return at==n?0:2;
        if(size<2||size>n-at||p[at]!='C'||p[at+1]!='K')return 2;
        rc=inflate_block(p+at+2,size-2,o);if(rc)return rc;at+=size;
    }
    return o->error;
}
static int skip(size_t *at,size_t n,size_t end) {if(*at>end||n>end-*at)return 1;*at+=n;return 0;}
int kwaj_decode(const unsigned char *p,size_t n,unsigned char **output,size_t *output_size) {
    static const unsigned char magic[8]={'K','W','A','J',0x88,0xf0,0x27,0xd1};
    unsigned method,off,flags,i;uint32_t expected=0;size_t at=14;Output o={NULL,0,0,0};int rc=0;
    if(!output||!output_size)return 1;
    *output=NULL;*output_size=0;
    if(!p||n<14||memcmp(p,magic,8))return 1;
    method=u16(p+8);off=u16(p+10);flags=u16(p+12);
    if(method>4||flags&~63u||off<14||off>n)return 1;
    if(flags&1){if(skip(&at,4,off))return 1;expected=u32(p+at-4);}
    if(flags&2){if(skip(&at,2,off))return 1;}
    if(flags&4){unsigned len;if(skip(&at,2,off))return 1;len=u16(p+at-2);if(skip(&at,len,off))return 1;}
    for(i=0;i<2;i++)if(flags&(8u<<i)){
        unsigned j,max=i?4:9;
        for(j=0;j<max;j++){if(skip(&at,1,off))return 1;if(!p[at-1])break;}
        if(j==max)return 1;
    }
    if(flags&32){unsigned len;if(skip(&at,2,off))return 1;len=u16(p+at-2);if(skip(&at,len,off))return 1;}
    p+=off;n-=off;
    if(method<2){size_t j;for(j=0;j<n;j++)put(&o,p[j]^(method?255:0));rc=o.error;}
    else if(method==2)rc=lzss(p,n,&o);
    else if(method==3)rc=lzh(p,n,&o);
    else rc=mszip(p,n,&o);
    if(!rc&&(flags&1)&&o.n!=expected)rc=4;
    if(rc){free(o.p);return rc;}
    *output=o.p;*output_size=o.n;return 0;
}
void kwaj_free(void *p){free(p);}
#ifdef KWAJ_MAIN
int main(int argc,char **argv) {
    FILE *f;long length;unsigned char *input,*output=NULL;size_t size;int rc;
    if(argc!=3){fprintf(stderr,"usage: kwaj input output\n");return 1;}
    f=fopen(argv[1],"rb");if(!f)return 1;
    if(fseek(f,0,SEEK_END)||(length=ftell(f))<0||fseek(f,0,SEEK_SET)){fclose(f);return 1;}
    input=(unsigned char *)malloc((size_t)length+1);if(!input){fclose(f);return 3;}
    if(fread(input,1,(size_t)length,f)!=(size_t)length){free(input);fclose(f);return 1;}fclose(f);
    rc=kwaj_decode(input,(size_t)length,&output,&size);free(input);
    if(rc){fprintf(stderr,"KWAJ decode error %d\n",rc);return rc;}
    f=fopen(argv[2],"wb");if(!f){free(output);return 1;}
    rc=fwrite(output,1,size,f)!=size;rc|=fclose(f)!=0;free(output);return rc;
}
#endif
