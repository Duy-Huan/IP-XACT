/* SPDX-License-Identifier: Apache-2.0 */
#include "string.h"

void *memcpy(void *dest, const void *src, size_t count)
{
    unsigned char *d = dest;
    const unsigned char *s = src;
    for (size_t i = 0; i < count; ++i) d[i] = s[i];
    return dest;
}

void *memset(void *dest, int value, size_t count)
{
    unsigned char *d = dest;
    for (size_t i = 0; i < count; ++i) d[i] = (unsigned char)value;
    return dest;
}

int memcmp(const void *left, const void *right, size_t count)
{
    const unsigned char *a = left, *b = right;
    for (size_t i = 0; i < count; ++i) {
        if (a[i] != b[i]) return (int)a[i] - (int)b[i];
    }
    return 0;
}
