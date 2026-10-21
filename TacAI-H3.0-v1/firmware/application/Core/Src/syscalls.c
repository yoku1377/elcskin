#include <sys/stat.h>
#include <stdint.h>
#include "usart.h"

extern char _end;
extern char _estack;
static char *s_heap_end;

int _write(int file, char *ptr, int len)
{
    (void)file;
    return HAL_UART_Transmit(&huart1, (uint8_t *)ptr, (uint16_t)len, 1000u) == HAL_OK ? len : -1;
}

void *_sbrk(ptrdiff_t increment)
{
    char *previous;
    if (s_heap_end == 0) s_heap_end = &_end;
    previous = s_heap_end;
    if (s_heap_end + increment >= &_estack) return (void *)-1;
    s_heap_end += increment;
    return previous;
}

int _close(int file) { (void)file; return -1; }
int _fstat(int file, struct stat *status) { (void)file; status->st_mode = S_IFCHR; return 0; }
int _isatty(int file) { (void)file; return 1; }
int _lseek(int file, int offset, int whence) { (void)file; (void)offset; (void)whence; return 0; }
int _read(int file, char *ptr, int len) { (void)file; (void)ptr; (void)len; return 0; }
int _kill(int pid, int sig) { (void)pid; (void)sig; return -1; }
int _getpid(void) { return 1; }
void _exit(int status) { (void)status; for (;;) { } }
