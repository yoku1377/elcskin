ENTRY(Reset_Handler)

_estack = 0x20001FF0;
_Min_Heap_Size = 0x100;
_Min_Stack_Size = 0x400;

MEMORY
{
  RAM (xrw)  : ORIGIN = 0x20000000, LENGTH = 0x1FF0
  FLASH (rx) : ORIGIN = 0x08000000, LENGTH = 8K
}

SECTIONS
{
  .isr_vector : { KEEP(*(.isr_vector)) } > FLASH
  .text :
  {
    *(.text*)
    *(.rodata*)
    KEEP(*(.init))
    KEEP(*(.fini))
    . = ALIGN(4);
    _etext = .;
  } > FLASH
  _sidata = LOADADDR(.data);
  .data :
  {
    . = ALIGN(4);
    _sdata = .;
    *(.data*)
    . = ALIGN(4);
    _edata = .;
  } > RAM AT > FLASH
  .bss (NOLOAD) :
  {
    . = ALIGN(4);
    _sbss = .;
    *(.bss*)
    *(COMMON)
    . = ALIGN(4);
    _ebss = .;
  } > RAM
  ._user_heap_stack (NOLOAD) :
  {
    . = ALIGN(8);
    . = . + _Min_Heap_Size;
    . = . + _Min_Stack_Size;
    . = ALIGN(8);
  } > RAM
  /DISCARD/ : { *(.note*) *(.comment*) }
}

ASSERT(SIZEOF(.isr_vector) + SIZEOF(.text) + SIZEOF(.data) <= LENGTH(FLASH), "Bootloader exceeds 8 KiB")
